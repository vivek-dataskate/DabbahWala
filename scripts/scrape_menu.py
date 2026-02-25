#!/usr/bin/env python3
"""
scrape_menu.py — Weekly menu scraper for dabbahwala.com

Flow:
  1. Navigate to the subscription/build-your-own-box page
  2. Enter the Telnyx business number to receive the OTP
  3. Poll the telnyx_messages DB table for the incoming SMS code (up to 2 min)
  4. Enter the OTP to complete verification
  5. Extract menu items from the rendered page
  6. POST the menu to /api/menu/sync

Requirements (add to requirements.txt before running on Render):
  playwright>=1.40.0
  psycopg2-binary (already present)
  requests (already present or use httpx)

Run:
  pip install playwright && playwright install chromium
  python scripts/scrape_menu.py
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone

import httpx
import psycopg2
import psycopg2.extras

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("scrape_menu")

# ---------------------------------------------------------------------------
# Config — all from environment (same vars as the main app)
# ---------------------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL", "")
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
TELNYX_FROM_NUMBER = os.environ.get("TELNYX_FROM_NUMBER", "+18444322224")

MENU_URL = "https://www.dabbahwala.com/subscription-plan-build-your-own-box?step=1"
OTP_POLL_TIMEOUT = 120    # seconds to wait for OTP SMS
OTP_POLL_INTERVAL = 5     # seconds between DB polls


# ---------------------------------------------------------------------------
# DB helper (direct psycopg2 so this script is standalone)
# ---------------------------------------------------------------------------
def _db_conn():
    url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
    conn.autocommit = True
    return conn


def _wait_for_otp(since_ts: datetime) -> str | None:
    """
    Poll telnyx_messages for an inbound SMS containing a short numeric code
    sent to/from our business number after `since_ts`.
    Returns the OTP string or None if timed out.
    """
    log.info("Waiting for OTP SMS on %s (timeout=%ds)…", TELNYX_FROM_NUMBER, OTP_POLL_TIMEOUT)
    deadline = time.time() + OTP_POLL_TIMEOUT
    conn = _db_conn()
    try:
        while time.time() < deadline:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT body FROM dabbahwala.telnyx_messages
                       WHERE direction = 'inbound'
                         AND sent_at   > %s
                         AND (to_number = %s OR from_number = %s)
                       ORDER BY sent_at DESC LIMIT 1""",
                    (since_ts, TELNYX_FROM_NUMBER, TELNYX_FROM_NUMBER),
                )
                row = cur.fetchone()
                if row and row["body"]:
                    # Extract a 4-8 digit code from the SMS body
                    match = re.search(r"\b(\d{4,8})\b", row["body"])
                    if match:
                        code = match.group(1)
                        log.info("OTP received: %s", code)
                        return code
            time.sleep(OTP_POLL_INTERVAL)
    finally:
        conn.close()
    log.warning("OTP timed out after %ds", OTP_POLL_TIMEOUT)
    return None


# ---------------------------------------------------------------------------
# Playwright scraper
# ---------------------------------------------------------------------------
async def scrape_with_playwright() -> list[dict]:
    """
    Navigate the DabbahWala subscription builder, authenticate via SMS OTP,
    and extract the list of menu items available this week.
    Returns a list of dicts: {item_name, category, is_veg, price, is_featured}
    """
    try:
        from playwright.async_api import async_playwright, TimeoutError as PWTimeout
    except ImportError:
        log.error("playwright not installed — run: pip install playwright && playwright install chromium")
        sys.exit(1)

    items: list[dict] = []
    start_ts = datetime.now(tz=timezone.utc)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        page.set_default_timeout(30_000)

        log.info("Opening %s", MENU_URL)
        await page.goto(MENU_URL, wait_until="networkidle")

        # ── Step 1: Enter phone number ───────────────────────────────────
        # Look for a phone input and submit button (SvelteKit form)
        try:
            phone_input = await page.wait_for_selector(
                "input[type='tel'], input[placeholder*='phone'], input[name='phone']",
                timeout=15_000,
            )
            await phone_input.fill(TELNYX_FROM_NUMBER)
            log.info("Entered phone number: %s", TELNYX_FROM_NUMBER)

            # Submit (try button click, then Enter)
            submit_btn = page.locator("button[type='submit'], button:has-text('Send'), button:has-text('Continue')")
            if await submit_btn.count() > 0:
                await submit_btn.first.click()
            else:
                await phone_input.press("Enter")

            await page.wait_for_timeout(2000)
        except PWTimeout:
            # Page may not have phone auth — try to extract menu directly
            log.info("No phone input found — attempting direct menu extraction")

        # ── Step 2: Enter OTP ────────────────────────────────────────────
        otp = _wait_for_otp(start_ts)
        if otp:
            try:
                otp_input = await page.wait_for_selector(
                    "input[type='number'], input[inputmode='numeric'], input[placeholder*='code'], input[name='otp']",
                    timeout=10_000,
                )
                await otp_input.fill(otp)
                verify_btn = page.locator(
                    "button[type='submit'], button:has-text('Verify'), button:has-text('Continue')"
                )
                if await verify_btn.count() > 0:
                    await verify_btn.first.click()
                else:
                    await otp_input.press("Enter")
                await page.wait_for_load_state("networkidle")
                log.info("OTP submitted, page loaded")
            except PWTimeout:
                log.warning("OTP input not found — continuing without verification")
        else:
            log.warning("No OTP received — proceeding to try extracting menu anyway")

        # ── Step 3: Extract menu items ────────────────────────────────────
        await page.wait_for_timeout(3000)  # let SvelteKit hydrate

        # Strategy A: intercept the network response that returns menu JSON
        # (SvelteKit typically loads menu via a fetch to its own API)
        # Strategy B: scrape visible item cards from the DOM

        # Try to find item cards
        # Common patterns: div with item name + price, cards, list items
        card_selectors = [
            "[data-item-name]",
            ".menu-item",
            ".item-card",
            ".product-card",
            "[class*='item']",
        ]

        found_via_dom = False
        for sel in card_selectors:
            cards = page.locator(sel)
            count = await cards.count()
            if count > 2:
                log.info("Found %d cards with selector: %s", count, sel)
                for i in range(count):
                    card = cards.nth(i)
                    name = (await card.get_attribute("data-item-name")) or (
                        await card.locator("h2, h3, [class*='name'], [class*='title']").first.text_content()
                        if await card.locator("h2, h3, [class*='name'], [class*='title']").count() > 0
                        else None
                    )
                    if name:
                        items.append({
                            "item_name": name.strip(),
                            "is_featured": False,
                            "display_order": i,
                        })
                found_via_dom = True
                break

        # Strategy C: grab all visible text nodes that look like food items
        # by scanning h2/h3/strong elements in the main content
        if not found_via_dom:
            log.info("DOM card extraction failed — scanning headings for item names")
            headings = await page.locator("main h2, main h3, main strong, section h2, section h3").all_text_contents()
            for idx, text in enumerate(headings):
                text = text.strip()
                if 3 < len(text) < 80 and not any(
                    skip in text.lower()
                    for skip in ["plan", "subscribe", "cart", "total", "checkout", "week", "delivery"]
                ):
                    items.append({"item_name": text, "is_featured": False, "display_order": idx})

        # Strategy D: intercept XHR/fetch responses for JSON with item data
        if not items:
            log.info("Trying JSON extraction from page source…")
            content = await page.content()
            # Look for JSON arrays embedded in script tags
            json_matches = re.findall(r'\[\s*\{[^}]*"name"\s*:', content)
            if json_matches:
                for match in json_matches[:3]:
                    try:
                        data = json.loads(match + "}]")
                        for entry in data:
                            if "name" in entry:
                                items.append({
                                    "item_name": entry["name"],
                                    "category": entry.get("category"),
                                    "is_veg": entry.get("is_veg") or entry.get("isVeg"),
                                    "price": entry.get("price"),
                                    "is_featured": entry.get("featured", False),
                                })
                    except (json.JSONDecodeError, Exception):
                        pass

        await browser.close()

    log.info("Scraped %d items", len(items))
    return items


# ---------------------------------------------------------------------------
# POST to API
# ---------------------------------------------------------------------------
def post_to_api(items: list[dict]) -> dict:
    payload = {
        "items": items,
        "source": "scraper",
    }
    url = f"{API_BASE_URL}/api/menu/sync"
    log.info("POSTing %d items to %s", len(items), url)
    resp = httpx.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main():
    if not DATABASE_URL:
        log.error("DATABASE_URL not set — cannot poll for OTP")
        sys.exit(1)

    items = await scrape_with_playwright()

    if not items:
        log.error("No menu items extracted — aborting sync")
        sys.exit(1)

    result = post_to_api(items)
    log.info("Sync result: %s", result)
    return result


if __name__ == "__main__":
    asyncio.run(main())
