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

# Playwright looks for browsers in ~/.cache by default, which is not persisted
# across Render build/runtime containers.  The build script installs to this
# project-relative path, so force the same location here.
os.environ.setdefault(
    "PLAYWRIGHT_BROWSERS_PATH",
    "/opt/render/project/src/.playwright-browsers",
)

MENU_URL = "https://www.dabbahwala.com/subscription-plan-build-your-own-box"
TELNYX_PHONE = "+18444322224"
OTP_TIMEOUT = 90          # seconds to wait for OTP SMS
OTP_POLL_INTERVAL = 3     # seconds between Telnyx API polls

# The exact button text that navigates to the menu / triggers login
ONE_TIME_PURCHASE_TEXT = "One Time Purchase"


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

async def _click_one_time_purchase(page, PwTimeout) -> bool:
    """Click the 'One Time Purchase' button. Returns True if found and clicked."""
    locator = page.locator(
        f'button:has-text("{ONE_TIME_PURCHASE_TEXT}"), '
        f'a:has-text("{ONE_TIME_PURCHASE_TEXT}")'
    )
    try:
        await locator.first.wait_for(state="visible", timeout=10_000)
        await locator.first.click()
        logger.info("Clicked '%s'", ONE_TIME_PURCHASE_TEXT)
        return True
    except Exception:
        logger.warning("'%s' button not found", ONE_TIME_PURCHASE_TEXT)
        return False


async def _handle_login(page, PwTimeout):
    """
    Complete the phone + OTP login flow on /apex/login.
    Raises RuntimeError if OTP is not received in time.
    """
    logger.info("On login page — entering phone number %s", TELNYX_PHONE)

    # Find phone input (placeholder="Phone number" from the actual site)
    phone_input = None
    for sel in [
        'input[placeholder="Phone number"]',
        'input[placeholder*="phone" i]',
        'input[type="tel"]',
        'input[type="text"]',
        'input[type="number"]',
    ]:
        try:
            phone_input = await page.wait_for_selector(sel, timeout=8_000)
            if phone_input:
                logger.info("Phone input found: %s", sel)
                break
        except PwTimeout:
            continue

    if not phone_input:
        html = await page.content()
        logger.error("Phone input not found on login page. HTML: %s", html[:3000])
        raise RuntimeError("Phone number input not found on login page")

    otp_request_time = time.time()
    await phone_input.click()
    await phone_input.fill("")
    await phone_input.type(TELNYX_PHONE, delay=80)
    logger.info("Typed phone number")

    # Click "Continue" (the submit button on the login page)
    for sel in [
        'button:has-text("Continue")',
        'button[type="submit"]',
        'button:has-text("Send")',
        'button:has-text("Next")',
    ]:
        try:
            btn = await page.wait_for_selector(sel, timeout=5_000)
            if btn:
                await btn.click()
                logger.info("Clicked submit: %s", sel)
                break
        except PwTimeout:
            continue
    else:
        await phone_input.press("Enter")
        logger.info("Pressed Enter on phone field")

    await page.screenshot(path="/tmp/dw_otp_requested.png")

    # Poll Telnyx for the OTP
    otp = poll_telnyx_for_otp(sent_after_ts=otp_request_time)
    if not otp:
        raise RuntimeError("OTP not received from Telnyx — aborting scrape")

    # Find OTP input and fill it
    otp_input = None
    for sel in [
        'input[placeholder*="OTP" i]',
        'input[placeholder*="code" i]',
        'input[placeholder*="otp" i]',
        'input[type="number"]',
        'input[maxlength="6"]',
        'input[maxlength="4"]',
        'input[name*="otp" i]',
        'input[name*="code" i]',
    ]:
        try:
            otp_input = await page.wait_for_selector(sel, timeout=5_000)
            if otp_input:
                logger.info("OTP input found: %s", sel)
                break
        except PwTimeout:
            continue

    if not otp_input:
        # Try individual single-digit inputs
        digit_inputs = await page.query_selector_all('input[maxlength="1"]')
        if len(digit_inputs) >= 4:
            logger.info("Found %d single-digit OTP inputs", len(digit_inputs))
            for i, digit in enumerate(otp[: len(digit_inputs)]):
                await digit_inputs[i].fill(digit)
            otp_input = digit_inputs[0]
        else:
            html = await page.content()
            logger.error("OTP input not found. HTML: %s", html[:3000])
            raise RuntimeError("OTP input not found on page")

    if otp_input:
        try:
            await otp_input.fill(otp)
        except Exception:
            pass  # already filled digit-by-digit above
    logger.info("Filled OTP: %s", otp)

    # Submit OTP
    for sel in [
        'button:has-text("Verify")',
        'button:has-text("Continue")',
        'button:has-text("Submit")',
        'button:has-text("Confirm")',
        'button[type="submit"]',
    ]:
        try:
            btn = await page.wait_for_selector(sel, timeout=3_000)
            if btn:
                await btn.click()
                logger.info("OTP submitted via: %s", sel)
                break
        except PwTimeout:
            continue
    else:
        await page.keyboard.press("Enter")

    await page.wait_for_load_state("networkidle", timeout=30_000)
    await asyncio.sleep(2)
    await page.screenshot(path="/tmp/dw_after_otp.png")
    logger.info("Login complete — current URL: %s", page.url)


async def _scrape_items_from_page(page) -> list[dict]:
    """
    Scrape all menu items across every visible date tab on the menu page.
    Returns a list of unique item dicts.
    """
    all_items: dict[str, dict] = {}  # keyed by item_name for dedup

    async def _extract_current_tab() -> list[dict]:
        await asyncio.sleep(1)  # let tab content render
        return await page.evaluate("""
            () => {
                const results = [];
                // Cards: try several class patterns used by the site
                const cards = Array.from(document.querySelectorAll(
                    '[class*="item-card"], [class*="menu-item"], [class*="product-card"], ' +
                    '[class*="dish-card"], [class*="food-item"], [class*="ItemCard"], ' +
                    '[class*="MenuItem"], [class*="ProductCard"]'
                ));

                // Fallback: any element that contains an img, a heading, and a price-like span
                const fallback = cards.length === 0;
                const candidates = fallback
                    ? Array.from(document.querySelectorAll('li, article, [class*="card"], [class*="item"]'))
                    : cards;

                for (const card of candidates) {
                    // Name: first heading or strong element
                    const nameEls = card.querySelectorAll('h1,h2,h3,h4,h5,strong,[class*="name"],[class*="title"]');
                    let name = null;
                    for (const el of nameEls) {
                        const t = el.innerText.trim();
                        if (t && t.length >= 2 && t.length <= 120) { name = t; break; }
                    }
                    if (!name) continue;

                    // Skip UI chrome
                    const skip = ['Add','Remove','View','Cart','Checkout','Search','Menu','Back','Next','Prev'];
                    if (skip.includes(name)) continue;

                    // Description / category: first <p> or element with desc/category class
                    const descEl = card.querySelector('p,[class*="desc"],[class*="category"],[class*="subtitle"]');
                    const description = (descEl && descEl.innerText.trim() !== name)
                        ? descEl.innerText.trim().slice(0, 300) : null;

                    // Category from description text (heuristic)
                    let category = description || null;

                    // is_veg: look for a veg/non-veg indicator element or text
                    const vegEl = card.querySelector('[class*="veg"],[class*="non-veg"],[class*="nonveg"]');
                    let is_veg = null;
                    if (vegEl) {
                        const cls = (vegEl.className || '').toLowerCase();
                        const txt = (vegEl.innerText || '').toLowerCase();
                        is_veg = !(cls.includes('non') || txt.includes('non'));
                    } else if (category) {
                        const cat = category.toLowerCase();
                        if (cat.includes('non-veg') || cat.includes('nonveg') || cat.includes('chicken') || cat.includes('mutton') || cat.includes('fish')) {
                            is_veg = false;
                        } else if (cat.includes('veg')) {
                            is_veg = true;
                        }
                    }

                    // Image
                    const imgEl = card.querySelector('img');
                    const image_url = imgEl ? imgEl.src : null;

                    results.push({ item_name: name, description, category, is_veg, image_url });
                }
                return results;
            }
        """)

    # Find all date/day tab buttons
    date_tabs = await page.query_selector_all(
        '[class*="date-tab"], [class*="day-tab"], [class*="date-btn"], '
        '[class*="day-btn"], [role="tab"], [class*="DateTab"], [class*="DayTab"]'
    )
    logger.info("Found %d date tabs", len(date_tabs))

    if date_tabs:
        for i, tab in enumerate(date_tabs):
            try:
                label = await tab.inner_text()
                await tab.click()
                logger.info("Clicked date tab %d: %s", i, label.strip())
                tab_items = await _extract_current_tab()
                for item in tab_items:
                    name = item.get("item_name", "").strip()
                    if name and name not in all_items:
                        all_items[name] = item
                logger.info("Tab %d yielded %d items (total unique: %d)", i, len(tab_items), len(all_items))
            except Exception as e:
                logger.warning("Error on date tab %d: %s", i, e)
    else:
        # No tabs found — scrape the single visible page
        logger.info("No date tabs found — scraping single page view")
        tab_items = await _extract_current_tab()
        for item in tab_items:
            name = item.get("item_name", "").strip()
            if name and name not in all_items:
                all_items[name] = item

    return list(all_items.values())


async def scrape_menu_items() -> list[dict]:
    """
    Navigate dabbahwala.com subscription page and scrape all weekly menu items.

    Exact flow (from site observation):
      Step 1 — Navigate to /subscription-plan-build-your-own-box
      Step 2 — Click "One Time Purchase →"
               → If session cached: goes straight to menu (step 4)
               → If not logged in: redirected to /apex/login
      Step 3 — Enter phone number, receive OTP via Telnyx, submit OTP
               → Redirected back to subscription landing page
      Step 4 — Click "One Time Purchase →" again → menu page
      Step 5 — Iterate date tabs and scrape all item cards

    Returns list of dicts: {item_name, category, is_veg, description, image_url}
    """
    from playwright.async_api import async_playwright, TimeoutError as PwTimeout

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
            # ── Step 1: load the subscription landing page ────────────────────
            logger.info("Step 1 — Navigating to %s", MENU_URL)
            await page.goto(MENU_URL, wait_until="networkidle", timeout=60_000)
            await asyncio.sleep(2)
            await page.screenshot(path="/tmp/dw_step1.png")
            logger.info("Landing page loaded — URL: %s", page.url)

            # ── Step 2: click "One Time Purchase" ─────────────────────────────
            logger.info("Step 2 — Clicking '%s'", ONE_TIME_PURCHASE_TEXT)
            await _click_one_time_purchase(page, PwTimeout)
            await page.wait_for_load_state("networkidle", timeout=30_000)
            await asyncio.sleep(2)
            await page.screenshot(path="/tmp/dw_step2.png")
            logger.info("After button click — URL: %s", page.url)

            # ── Step 3: handle login if redirected to /apex/login ─────────────
            if "/login" in page.url or "/apex" in page.url:
                logger.info("Step 3 — Login required, handling OTP flow")
                await _handle_login(page, PwTimeout)

                # After login we're back on the subscription landing page
                logger.info("Step 3 complete — URL: %s", page.url)
                await page.screenshot(path="/tmp/dw_step3.png")

                # ── Step 4 (post-login): click "One Time Purchase" again ───────
                logger.info("Step 4 — Clicking '%s' again after login", ONE_TIME_PURCHASE_TEXT)
                await _click_one_time_purchase(page, PwTimeout)
                await page.wait_for_load_state("networkidle", timeout=30_000)
                await asyncio.sleep(2)
                await page.screenshot(path="/tmp/dw_step4.png")
                logger.info("Menu page — URL: %s", page.url)
            else:
                # Cached session — already on the menu page
                logger.info("Cached session — skipped login, on menu at URL: %s", page.url)

            # ── Step 5: scrape menu items across all date tabs ────────────────
            logger.info("Step 5 — Scraping menu items")
            await page.screenshot(path="/tmp/dw_menu.png")
            items = await _scrape_items_from_page(page)

            if not items:
                logger.warning("No items scraped — saving HTML for debugging")
                html = await page.content()
                with open("/tmp/dw_menu_page.html", "w") as f:
                    f.write(html)
                logger.info("Saved page HTML to /tmp/dw_menu_page.html")
                content = await page.inner_text("main, body")
                logger.info("Page text (first 3000):\n%s", content[:3000])
            else:
                logger.info("Scraped %d unique menu items", len(items))

            return items

        except Exception as exc:
            logger.error("Scrape failed: %s", exc, exc_info=True)
            try:
                await page.screenshot(path="/tmp/dw_error.png")
            except Exception:
                pass
            raise
        finally:
            await browser.close()


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
