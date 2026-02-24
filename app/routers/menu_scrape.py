"""
menu_scrape.py — API endpoints for weekly menu scraping
========================================================
POST /api/menu-scrape/run       — trigger a fresh scrape (runs sync, up to 3 min)
GET  /api/menu-scrape/current   — return this week's scraped menu items
GET  /api/menu-scrape/week/{YYYY-MM-DD} — return menu items for a specific week
"""

import asyncio
import logging
import os
import sys
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException

from app.db import get_cursor

logger = logging.getLogger(__name__)
router = APIRouter()


def _current_week_start() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())


# ---------------------------------------------------------------------------
# GET /current
# ---------------------------------------------------------------------------

@router.get("/current")
def get_current_menu():
    """Return this week's scraped menu items."""
    week_start = _current_week_start()
    return _get_menu_for_week(week_start)


# ---------------------------------------------------------------------------
# GET /week/{week_start}
# ---------------------------------------------------------------------------

@router.get("/week/{week_start}")
def get_menu_for_week(week_start: str):
    """Return menu items for a specific week (YYYY-MM-DD format)."""
    try:
        ws = date.fromisoformat(week_start)
    except ValueError:
        raise HTTPException(status_code=400, detail="week_start must be YYYY-MM-DD")
    return _get_menu_for_week(ws)


def _get_menu_for_week(week_start: date) -> dict:
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT item_name, category, is_veg, description, image_url, scraped_at
            FROM dabbahwala.weekly_menu_schedule
            WHERE week_start = %s
            ORDER BY is_veg DESC NULLS LAST, item_name
            """,
            (week_start,),
        )
        rows = cur.fetchall()
    return {
        "week_start": str(week_start),
        "items": [dict(r) for r in rows],
        "count": len(rows),
    }


# ---------------------------------------------------------------------------
# POST /run — trigger a fresh scrape
# ---------------------------------------------------------------------------

@router.post("/run")
def run_scrape(week_start: str | None = None):
    """
    Trigger a full menu scrape for the given week (defaults to current week).
    Runs Playwright synchronously — allow up to 3 minutes in n8n HTTP node.
    """
    if week_start:
        try:
            ws = date.fromisoformat(week_start)
        except ValueError:
            raise HTTPException(status_code=400, detail="week_start must be YYYY-MM-DD")
    else:
        ws = _current_week_start()

    logger.info("Menu scrape triggered via API for week_start=%s", ws)

    # Import the scraper — it lives in scripts/, add to path
    scripts_dir = os.path.join(os.path.dirname(__file__), "..", "..", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    try:
        import scrape_menu as _sm
        result = asyncio.run(_sm.run(ws))
        logger.info("Menu scrape completed: %s", result)
        return result
    except Exception as exc:
        logger.error("Menu scrape failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Scrape failed: {exc}")
