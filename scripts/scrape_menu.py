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
# Playwright helpers
# ---------------------------------------------------------------------------

async def _log_page_state(page, label: str) -> None:
    """Log URL, title, all visible buttons, and all visible inputs."""
    try:
        url = page.url
        title = await page.title()
        logger.info("[%s] URL: %s | Title: %r", label, url, title)

        # All visible buttons
        buttons = await page.evaluate("""
            () => Array.from(document.querySelectorAll('button, [role="button"], input[type="submit"]'))
                .filter(el => {
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                })
                .map(el => ({
                    tag: el.tagName,
                    text: (el.innerText || el.value || '').trim().slice(0, 80),
                    disabled: el.disabled,
                    type: el.type || null,
                }))
        """)
        if buttons:
            btn_summary = "; ".join(
                f"{b['tag']}({b['text']!r}{', disabled' if b['disabled'] else ''})"
                for b in buttons[:20]
            )
            logger.info("[%s] Buttons (%d): %s", label, len(buttons), btn_summary)
        else:
            logger.info("[%s] No visible buttons found", label)

        # All visible inputs
        inputs = await page.evaluate("""
            () => Array.from(document.querySelectorAll('input, textarea, select'))
                .filter(el => {
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                })
                .map(el => ({
                    tag: el.tagName,
                    type: el.type || null,
                    name: el.name || null,
                    placeholder: el.placeholder || null,
                    maxlength: el.maxLength > 0 ? el.maxLength : null,
                    value_len: el.value ? el.value.length : 0,
                }))
        """)
        if inputs:
            inp_summary = "; ".join(
                f"{i['tag']}(type={i['type']!r}, placeholder={i['placeholder']!r}, name={i['name']!r}, maxlen={i['maxlength']})"
                for i in inputs[:20]
            )
            logger.info("[%s] Inputs (%d): %s", label, len(inputs), inp_summary)
        else:
            logger.info("[%s] No visible inputs found", label)

    except Exception as exc:
        logger.warning("[%s] _log_page_state error: %s", label, exc)


async def _click_one_time_purchase(page, PwTimeout) -> bool:
    """Click the 'One Time Purchase' button. Returns True if found and clicked."""
    # Log all matching candidates before clicking
    try:
        candidates = await page.evaluate(
            f"""
            () => Array.from(document.querySelectorAll(
                'button, a, [role="button"]'
            )).filter(el => el.innerText.includes("{ONE_TIME_PURCHASE_TEXT}"))
             .map(el => ({{
                tag: el.tagName,
                text: el.innerText.trim().slice(0, 100),
                visible: el.getBoundingClientRect().width > 0,
                disabled: el.disabled,
                href: el.href || null,
             }}))
            """
        )
        if candidates:
            logger.info(
                "'%s' candidates (%d): %s",
                ONE_TIME_PURCHASE_TEXT,
                len(candidates),
                "; ".join(f"{c['tag']}({c['text']!r}, visible={c['visible']}, disabled={c['disabled']})" for c in candidates),
            )
        else:
            logger.warning("No elements containing '%s' text found in DOM", ONE_TIME_PURCHASE_TEXT)
    except Exception as exc:
        logger.warning("Could not enumerate '%s' candidates: %s", ONE_TIME_PURCHASE_TEXT, exc)

    locator = page.locator(
        f'button:has-text("{ONE_TIME_PURCHASE_TEXT}"), '
        f'a:has-text("{ONE_TIME_PURCHASE_TEXT}")'
    )
    try:
        await locator.first.wait_for(state="visible", timeout=10_000)
        await locator.first.click()
        logger.info("Clicked '%s'", ONE_TIME_PURCHASE_TEXT)
        return True
    except Exception as exc:
        logger.warning("'%s' button not found or not clickable: %s", ONE_TIME_PURCHASE_TEXT, exc)
        return False


async def _handle_login(page, PwTimeout):
    """
    Complete the phone + OTP login flow on /apex/login.
    Raises RuntimeError if OTP is not received in time.
    """
    await _log_page_state(page, "login-page")

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
                logger.info("Phone input found via selector: %s", sel)
                break
        except PwTimeout:
            logger.info("Selector %r — not found (timeout)", sel)
            continue

    if not phone_input:
        html = await page.content()
        logger.error("Phone input not found on login page. HTML (first 3000): %s", html[:3000])
        raise RuntimeError("Phone number input not found on login page")

    otp_request_time = time.time()
    await phone_input.click()
    await phone_input.fill("")
    await phone_input.type(TELNYX_PHONE, delay=80)
    logger.info("Typed phone number into input")

    # Click "Continue" (the submit button on the login page)
    submitted = False
    for sel in [
        'button:has-text("Continue")',
        'button[type="submit"]',
        'button:has-text("Send")',
        'button:has-text("Next")',
    ]:
        try:
            btn = await page.wait_for_selector(sel, timeout=5_000)
            if btn:
                btn_text = await btn.inner_text()
                await btn.click()
                logger.info("Clicked submit button %r (text: %r)", sel, btn_text.strip())
                submitted = True
                break
        except PwTimeout:
            logger.info("Submit selector %r — not found (timeout)", sel)
            continue

    if not submitted:
        await phone_input.press("Enter")
        logger.info("Pressed Enter on phone field (no submit button matched)")

    await page.screenshot(path="/tmp/dw_otp_requested.png")
    await _log_page_state(page, "otp-requested")

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
                logger.info("OTP input found via selector: %s", sel)
                break
        except PwTimeout:
            logger.info("OTP selector %r — not found (timeout)", sel)
            continue

    if not otp_input:
        # Try individual single-digit inputs
        digit_inputs = await page.query_selector_all('input[maxlength="1"]')
        if len(digit_inputs) >= 4:
            logger.info("Found %d single-digit OTP inputs — filling digit by digit", len(digit_inputs))
            for i, digit in enumerate(otp[: len(digit_inputs)]):
                await digit_inputs[i].fill(digit)
            otp_input = digit_inputs[0]
        else:
            html = await page.content()
            logger.error(
                "OTP input not found. digit_inputs=%d. HTML (first 3000): %s",
                len(digit_inputs),
                html[:3000],
            )
            raise RuntimeError("OTP input not found on page")

    if otp_input:
        try:
            await otp_input.fill(otp)
            logger.info("Filled OTP field with: %s", otp)
        except Exception as exc:
            logger.info("otp_input.fill() skipped (already filled digit-by-digit): %s", exc)

    # Submit OTP
    otp_submitted = False
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
                btn_text = await btn.inner_text()
                await btn.click()
                logger.info("OTP submitted via %r (text: %r)", sel, btn_text.strip())
                otp_submitted = True
                break
        except PwTimeout:
            logger.info("OTP submit selector %r — not found (timeout)", sel)
            continue

    if not otp_submitted:
        await page.keyboard.press("Enter")
        logger.info("OTP submitted via Enter key")

    await page.wait_for_load_state("load", timeout=30_000)
    await asyncio.sleep(2)
    await page.screenshot(path="/tmp/dw_after_otp.png")
    logger.info("Login complete — current URL: %s", page.url)
    await _log_page_state(page, "after-otp")


async def _scrape_items_from_page(page) -> list[dict]:
    """
    Scrape all menu items across every visible date tab on the menu page.
    Returns a list of unique item dicts.
    """
    all_items: dict[str, dict] = {}  # keyed by item_name for dedup

    async def _extract_current_tab() -> list[dict]:
        await asyncio.sleep(1)  # let tab content render

        # Log how many candidate card elements exist before extraction
        card_count = await page.evaluate("""
            () => document.querySelectorAll(
                '[class*="item-card"], [class*="menu-item"], [class*="product-card"], ' +
                '[class*="dish-card"], [class*="food-item"], [class*="ItemCard"], ' +
                '[class*="MenuItem"], [class*="ProductCard"]'
            ).length
        """)
        logger.info("Card elements on current tab (primary selectors): %d", card_count)

        if card_count == 0:
            fallback_count = await page.evaluate("""
                () => document.querySelectorAll('li, article, [class*="card"], [class*="item"]').length
            """)
            logger.info("Card elements (fallback selectors): %d", fallback_count)

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
        # Log all tab labels upfront
        tab_labels = []
        for tab in date_tabs:
            try:
                tab_labels.append((await tab.inner_text()).strip())
            except Exception:
                tab_labels.append("?")
        logger.info("Tab labels: %s", tab_labels)

        for i, tab in enumerate(date_tabs):
            try:
                label = tab_labels[i]
                logger.info("Clicking date tab %d/%d: %r", i + 1, len(date_tabs), label)
                await tab.click()
                tab_items = await _extract_current_tab()
                new_items = 0
                for item in tab_items:
                    name = item.get("item_name", "").strip()
                    if name and name not in all_items:
                        all_items[name] = item
                        new_items += 1
                logger.info(
                    "Tab %d (%r): %d items extracted, %d new (total unique: %d)",
                    i + 1, label, len(tab_items), new_items, len(all_items),
                )
            except Exception as e:
                logger.warning("Error on date tab %d: %s", i, e)
    else:
        # No tabs found — scrape the single visible page
        logger.info("No date tabs found — scraping single page view")

        # Log some of the class names present to aid debugging
        class_sample = await page.evaluate("""
            () => {
                const all = new Set();
                document.querySelectorAll('[class]').forEach(el => {
                    (el.className || '').split(/\\s+/).forEach(c => { if (c) all.add(c); });
                });
                return Array.from(all).slice(0, 80);
            }
        """)
        logger.info("CSS classes present on page (first 80): %s", class_sample)

        tab_items = await _extract_current_tab()
        for item in tab_items:
            name = item.get("item_name", "").strip()
            if name and name not in all_items:
                all_items[name] = item
        logger.info("Single-page scrape: %d items", len(all_items))

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

        # ── Forward browser console messages to Python logger ─────────────────
        def _on_console(msg):
            level = msg.type  # "log", "warning", "error", "info", etc.
            text = msg.text
            if level == "error":
                logger.warning("[browser:console:error] %s", text)
            elif level == "warning":
                logger.info("[browser:console:warning] %s", text)
            else:
                logger.debug("[browser:console:%s] %s", level, text)

        page.on("console", _on_console)

        # ── Log non-2xx network responses ─────────────────────────────────────
        async def _on_response(response):
            status = response.status
            if status >= 400:
                logger.warning(
                    "[browser:response] %d %s → %s",
                    status,
                    response.request.method,
                    response.url[:120],
                )

        page.on("response", _on_response)

        try:
            # ── Step 1: load the subscription landing page ────────────────────
            logger.info("Step 1 — Navigating to %s", MENU_URL)
            response = await page.goto(MENU_URL, wait_until="load", timeout=90_000)
            await asyncio.sleep(2)
            await page.screenshot(path="/tmp/dw_step1.png")
            logger.info(
                "Step 1 complete — HTTP %s | URL: %s",
                response.status if response else "n/a",
                page.url,
            )
            await _log_page_state(page, "step1")

            # ── Step 2: click "One Time Purchase" ─────────────────────────────
            logger.info("Step 2 — Clicking '%s'", ONE_TIME_PURCHASE_TEXT)
            clicked = await _click_one_time_purchase(page, PwTimeout)
            logger.info("Step 2 — click returned: %s", clicked)
            await page.wait_for_load_state("load", timeout=30_000)
            await asyncio.sleep(2)
            await page.screenshot(path="/tmp/dw_step2.png")
            logger.info("Step 2 complete — URL: %s", page.url)
            await _log_page_state(page, "step2")

            # ── Step 3: handle login if redirected to /apex/login ─────────────
            if "/login" in page.url or "/apex" in page.url:
                logger.info("Step 3 — Login required (URL contains /login or /apex)")
                await _handle_login(page, PwTimeout)

                logger.info("Step 3 complete — URL: %s", page.url)
                await page.screenshot(path="/tmp/dw_step3.png")
                await _log_page_state(page, "step3")

                # ── Step 4 (post-login): click "One Time Purchase" again ───────
                logger.info("Step 4 — Clicking '%s' again after login", ONE_TIME_PURCHASE_TEXT)
                clicked = await _click_one_time_purchase(page, PwTimeout)
                logger.info("Step 4 — click returned: %s", clicked)
                await page.wait_for_load_state("load", timeout=30_000)
                await asyncio.sleep(2)
                await page.screenshot(path="/tmp/dw_step4.png")
                logger.info("Step 4 complete — URL: %s", page.url)
                await _log_page_state(page, "step4")
            else:
                # Cached session — already on the menu page
                logger.info("Step 3 — Cached session, skipped login. URL: %s", page.url)

            # ── Step 5: scrape menu items across all date tabs ────────────────
            logger.info("Step 5 — Starting menu scrape")
            await page.screenshot(path="/tmp/dw_menu.png")
            await _log_page_state(page, "step5-before-scrape")
            items = await _scrape_items_from_page(page)

            if not items:
                logger.warning("No items scraped — saving full HTML for debugging")
                html = await page.content()
                with open("/tmp/dw_menu_page.html", "w") as f:
                    f.write(html)
                logger.info("Saved page HTML to /tmp/dw_menu_page.html")
                content = await page.inner_text("main, body")
                logger.info("Page text (first 3000):\n%s", content[:3000])
            else:
                logger.info("Step 5 complete — scraped %d unique menu items", len(items))
                for idx, item in enumerate(items[:5], 1):
                    logger.info("  Sample item %d: %s", idx, item)

            return items

        except Exception as exc:
            logger.error("Scrape failed at URL=%s: %s", page.url, exc, exc_info=True)
            try:
                title = await page.title()
                logger.error("Page title at failure: %r", title)
            except Exception:
                pass
            try:
                visible_text = await page.inner_text("body")
                logger.error("Page body text at failure (first 2000):\n%s", visible_text[:2000])
            except Exception:
                pass
            try:
                await page.screenshot(path="/tmp/dw_error.png")
                logger.error("Error screenshot saved to /tmp/dw_error.png")
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
