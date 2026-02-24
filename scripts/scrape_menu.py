#!/usr/bin/env python3
"""
scrape_menu.py — Weekly menu scraper for dabbahwala.com
=======================================================
Navigates to the "Build Your Own Box" subscription page, completes
SMS OTP verification using the Telnyx number +18444322224, then
scrapes all available menu items and upserts them into
weekly_menu_schedule for the current delivery week.

Usage (one-shot, run by n8n or manually):
    python scripts/scrape_menu.py [--week-start YYYY-MM-DD]

Environment variables required:
    DATABASE_URL      — PostgreSQL connection string
    TELNYX_API_KEY    — Telnyx V2 API key for polling inbound SMS
"""

import argparse
import asyncio
import logging
import os
import re
import sys
import time
from datetime import date, timedelta

import httpx
import psycopg2
import psycopg2.extras

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("scrape_menu")

MENU_URL = "https://www.dabbahwala.com/subscription-plan-build-your-own-box?step=1"
TELNYX_PHONE = "+18444322224"
OTP_TIMEOUT = 90          # seconds to wait for OTP SMS
OTP_POLL_INTERVAL = 3     # seconds between Telnyx API polls


# ---------------------------------------------------------------------------
# OTP helper — poll Telnyx V2 REST API for recent inbound messages
# ---------------------------------------------------------------------------

def _telnyx_api_key() -> str:
    key = os.environ.get("TELNYX_API_KEY", "").strip()
    if not key:
        raise RuntimeError("TELNYX_API_KEY environment variable is not set")
    return key


def poll_telnyx_for_otp(sent_after_ts: float, timeout: int = OTP_TIMEOUT) -> str | None:
    """
    Poll Telnyx V2 /messages endpoint for an inbound SMS to TELNYX_PHONE
    that arrived after sent_after_ts (unix epoch float).

    Returns the extracted OTP string, or None if timed out.
    """
    api_key = _telnyx_api_key()
    headers = {"Authorization": f"Bearer {api_key}"}
    deadline = time.time() + timeout

    logger.info("Polling Telnyx for OTP (to=%s, timeout=%ds)…", TELNYX_PHONE, timeout)

    while time.time() < deadline:
        try:
            resp = httpx.get(
                "https://api.telnyx.com/v2/messages",
                params={
                    "filter[to][eq]": TELNYX_PHONE,
                    "filter[direction][eq]": "inbound",
                    "page[size]": 5,
                },
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            messages = resp.json().get("data", [])
            for msg in messages:
                # received_at is ISO8601 — convert to epoch for comparison
                received_raw = msg.get("received_at") or msg.get("created_at") or ""
                if received_raw:
                    from datetime import datetime, timezone
                    try:
                        dt = datetime.fromisoformat(received_raw.replace("Z", "+00:00"))
                        received_ts = dt.timestamp()
                    except ValueError:
                        received_ts = 0
                else:
                    received_ts = 0

                if received_ts < sent_after_ts:
                    continue  # older message, skip

                body = msg.get("text", "") or ""
                match = re.search(r"\b(\d{4,8})\b", body)
                if match:
                    otp = match.group(1)
                    logger.info("OTP found in SMS: %s (body: %r)", otp, body[:80])
                    return otp

        except Exception as exc:
            logger.warning("Telnyx poll error: %s", exc)

        time.sleep(OTP_POLL_INTERVAL)

    logger.error("OTP not received within %ds", timeout)
    return None


# ---------------------------------------------------------------------------
# Playwright scraper
# ---------------------------------------------------------------------------

async def scrape_menu_items() -> list[dict]:
    """
    Automate the dabbahwala.com menu page:
      1. Navigate to Build-Your-Own-Box step 1
      2. Enter the Telnyx phone number
      3. Wait for OTP via Telnyx API
      4. Enter OTP and proceed
      5. Scrape all menu items
    Returns list of dicts: {item_name, category, is_veg, description, image_url}
    """
    from playwright.async_api import async_playwright, TimeoutError as PwTimeout

    items: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        try:
            logger.info("Navigating to %s", MENU_URL)
            await page.goto(MENU_URL, wait_until="networkidle", timeout=60_000)
            await page.screenshot(path="/tmp/dw_step1.png")
            logger.info("Page loaded — screenshot saved to /tmp/dw_step1.png")

            # ── Step 1: find and fill the phone number input ─────────────────
            phone_selectors = [
                'input[type="tel"]',
                'input[placeholder*="phone" i]',
                'input[placeholder*="mobile" i]',
                'input[name*="phone" i]',
                'input[id*="phone" i]',
                'input[placeholder*="number" i]',
            ]
            phone_input = None
            for sel in phone_selectors:
                try:
                    phone_input = await page.wait_for_selector(sel, timeout=5_000)
                    if phone_input:
                        logger.info("Found phone input with selector: %s", sel)
                        break
                except PwTimeout:
                    continue

            if not phone_input:
                # Dump page source for debugging
                html = await page.content()
                logger.error("Could not find phone input. Page HTML (first 3000 chars): %s", html[:3000])
                raise RuntimeError("Phone number input not found on page")

            otp_request_time = time.time()

            await phone_input.click()
            await phone_input.fill("")
            await phone_input.type(TELNYX_PHONE, delay=80)
            logger.info("Entered phone number: %s", TELNYX_PHONE)

            # ── Find and click the "Send OTP" / "Get Code" / "Next" button ──
            send_selectors = [
                'button:has-text("Send")',
                'button:has-text("Get OTP")',
                'button:has-text("Get Code")',
                'button:has-text("Send OTP")',
                'button:has-text("Continue")',
                'button:has-text("Next")',
                'button:has-text("Verify")',
                'button[type="submit"]',
            ]
            clicked_send = False
            for sel in send_selectors:
                try:
                    btn = await page.wait_for_selector(sel, timeout=3_000)
                    if btn:
                        await btn.click()
                        clicked_send = True
                        logger.info("Clicked send button: %s", sel)
                        break
                except PwTimeout:
                    continue

            if not clicked_send:
                # Try pressing Enter on the phone field
                await phone_input.press("Enter")
                logger.info("Pressed Enter on phone field as fallback")

            await page.screenshot(path="/tmp/dw_otp_requested.png")

            # ── Poll Telnyx for the OTP ───────────────────────────────────────
            otp = poll_telnyx_for_otp(sent_after_ts=otp_request_time)
            if not otp:
                raise RuntimeError("OTP not received — aborting scrape")

            # ── Find and fill the OTP input ──────────────────────────────────
            otp_selectors = [
                'input[type="number"]',
                'input[placeholder*="OTP" i]',
                'input[placeholder*="code" i]',
                'input[placeholder*="otp" i]',
                'input[maxlength="6"]',
                'input[maxlength="4"]',
                'input[name*="otp" i]',
                'input[name*="code" i]',
            ]
            otp_input = None
            for sel in otp_selectors:
                try:
                    otp_input = await page.wait_for_selector(sel, timeout=5_000)
                    if otp_input:
                        logger.info("Found OTP input with selector: %s", sel)
                        break
                except PwTimeout:
                    continue

            if not otp_input:
                # Some sites show individual digit inputs — try filling each
                digit_inputs = await page.query_selector_all('input[maxlength="1"]')
                if len(digit_inputs) >= 4:
                    logger.info("Found %d single-digit OTP inputs", len(digit_inputs))
                    for i, digit in enumerate(otp[:len(digit_inputs)]):
                        await digit_inputs[i].fill(digit)
                    otp_input = digit_inputs[0]  # flag as found
                else:
                    html = await page.content()
                    logger.error("OTP input not found. HTML (first 3000): %s", html[:3000])
                    raise RuntimeError("OTP input not found on page")

            if otp_input:
                try:
                    await otp_input.fill(otp)
                except Exception:
                    pass  # already filled digit by digit above

            logger.info("Filled OTP: %s", otp)

            # ── Submit OTP ───────────────────────────────────────────────────
            verify_selectors = [
                'button:has-text("Verify")',
                'button:has-text("Submit")',
                'button:has-text("Confirm")',
                'button:has-text("Continue")',
                'button:has-text("Next")',
                'button[type="submit"]',
            ]
            for sel in verify_selectors:
                try:
                    btn = await page.wait_for_selector(sel, timeout=3_000)
                    if btn:
                        await btn.click()
                        logger.info("Clicked verify button: %s", sel)
                        break
                except PwTimeout:
                    continue
            else:
                await page.keyboard.press("Enter")

            # Wait for menu to load
            await page.wait_for_load_state("networkidle", timeout=30_000)
            await page.screenshot(path="/tmp/dw_menu.png")
            logger.info("OTP submitted — screenshot at /tmp/dw_menu.png")

            # ── Scrape menu items ─────────────────────────────────────────────
            # Give the React app a moment to render
            await asyncio.sleep(2)

            # Strategy: look for product/menu item cards
            # Try multiple selector patterns used by common e-commerce/food platforms
            item_data = await page.evaluate("""
                () => {
                    const results = [];

                    // Strategy 1: cards with item names in headings
                    const cards = document.querySelectorAll(
                        '[class*="item"], [class*="product"], [class*="menu"], [class*="card"], [class*="dish"]'
                    );

                    for (const card of cards) {
                        const nameEl = card.querySelector('h1, h2, h3, h4, p, span[class*="name"], span[class*="title"]');
                        const descEl = card.querySelector('p, [class*="desc"]');
                        const imgEl = card.querySelector('img');
                        const vegEl = card.querySelector('[class*="veg"], [class*="non"]');

                        const name = nameEl ? nameEl.innerText.trim() : null;
                        if (!name || name.length < 2 || name.length > 120) continue;

                        // Skip navigation items, buttons, etc.
                        if (['Add', 'Remove', 'View', 'Cart', 'Checkout', 'Search'].includes(name)) continue;

                        results.push({
                            item_name: name,
                            description: (descEl && descEl !== nameEl) ? descEl.innerText.trim().slice(0, 300) : null,
                            image_url: imgEl ? imgEl.src : null,
                            is_veg: vegEl ? (vegEl.className || '').toLowerCase().includes('non') ? false : true : null,
                            category: null,
                        });
                    }
                    return results;
                }
            """)

            if not item_data:
                # Fallback: dump all text from main content area
                logger.warning("JS strategy found 0 items — trying text extraction fallback")
                content = await page.inner_text("main, #main, [role='main'], body")
                logger.info("Page text (first 3000):\n%s", content[:3000])

                # Save page HTML for manual debugging
                html = await page.content()
                with open("/tmp/dw_menu_page.html", "w") as f:
                    f.write(html)
                logger.info("Saved page HTML to /tmp/dw_menu_page.html")
            else:
                logger.info("Scraped %d candidate items", len(item_data))
                # Deduplicate by name
                seen = set()
                for item in item_data:
                    name = item.get("item_name", "").strip()
                    if name and name not in seen:
                        seen.add(name)
                        items.append(item)
                logger.info("After dedup: %d unique items", len(items))

        except Exception as exc:
            logger.error("Scrape failed: %s", exc, exc_info=True)
            try:
                await page.screenshot(path="/tmp/dw_error.png")
            except Exception:
                pass
            raise
        finally:
            await browser.close()

    return items


# ---------------------------------------------------------------------------
# DB upsert
# ---------------------------------------------------------------------------

def upsert_menu(week_start: date, items: list[dict]) -> int:
    """Upsert scraped items into weekly_menu_schedule. Returns count inserted/updated."""
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    cur = conn.cursor()
    count = 0
    try:
        for item in items:
            cur.execute(
                """
                INSERT INTO dabbahwala.weekly_menu_schedule
                    (week_start, item_name, category, is_veg, description, image_url, scraped_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (week_start, item_name) DO UPDATE SET
                    category    = EXCLUDED.category,
                    is_veg      = EXCLUDED.is_veg,
                    description = EXCLUDED.description,
                    image_url   = EXCLUDED.image_url,
                    scraped_at  = NOW()
                """,
                (
                    week_start,
                    item["item_name"],
                    item.get("category"),
                    item.get("is_veg"),
                    item.get("description"),
                    item.get("image_url"),
                ),
            )
            count += 1
        conn.commit()
        logger.info("Upserted %d menu items for week_start=%s", count, week_start)
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
    return count


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def current_week_start() -> date:
    """Return the Monday of the current week."""
    today = date.today()
    return today - timedelta(days=today.weekday())


async def run(week_start: date) -> dict:
    logger.info("=== Menu scrape starting for week_start=%s ===", week_start)
    items = await scrape_menu_items()
    if not items:
        logger.warning("No menu items scraped — check /tmp/dw_menu_page.html for debugging")
        return {"week_start": str(week_start), "items_scraped": 0, "status": "empty"}
    count = upsert_menu(week_start, items)
    logger.info("=== Menu scrape complete: %d items stored ===", count)
    return {"week_start": str(week_start), "items_scraped": count, "status": "ok", "items": items}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape weekly menu from dabbahwala.com")
    parser.add_argument(
        "--week-start",
        default=str(current_week_start()),
        help="Monday of the target week (YYYY-MM-DD). Defaults to this week.",
    )
    args = parser.parse_args()
    week_start = date.fromisoformat(args.week_start)
    result = asyncio.run(run(week_start))
    print(result)
    sys.exit(0 if result["status"] in ("ok", "empty") else 1)
