"""
Menu Sync — keeps the weekly menu catalog fresh.

Endpoints:
  POST /api/menu/sync          — Receive and store this week's menu (from scraper or manual)
  GET  /api/menu/current       — Return this week's active menu (used by Claude agent)
  POST /api/menu/scrape-trigger — Trigger the Playwright scraper via subprocess

The scraper (scripts/scrape_menu.py) navigates dabbahwala.com, uses the Telnyx
business number to receive an OTP, completes verification, extracts menu items,
and POSTs them to /api/menu/sync.
"""
import json
import logging
import os
import subprocess
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.db import get_cursor

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _current_week_start() -> date:
    """Return the Monday of the current week."""
    today = date.today()
    return today - timedelta(days=today.weekday())


def _resolve_menu_item_id(cur, item_name: str) -> Optional[int]:
    """Find a menu_item id by name or alias, creating a new entry if needed."""
    # Try direct match
    cur.execute(
        "SELECT id FROM menu_items WHERE LOWER(item_name) = LOWER(%s)", (item_name,)
    )
    row = cur.fetchone()
    if row:
        return row["id"]

    # Try alias
    cur.execute(
        "SELECT mi.id FROM menu_item_aliases mia "
        "JOIN menu_items mi ON mi.item_name = mia.canonical_name "
        "WHERE LOWER(mia.alias) = LOWER(%s)",
        (item_name,),
    )
    row = cur.fetchone()
    if row:
        return row["id"]

    # Auto-create a new menu item so it can be tracked going forward
    cur.execute(
        "INSERT INTO menu_items (item_name, is_active) VALUES (%s, true) "
        "ON CONFLICT (item_name) DO UPDATE SET is_active = true RETURNING id",
        (item_name,),
    )
    row = cur.fetchone()
    return row["id"] if row else None


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class MenuItem(BaseModel):
    item_name: str
    category: Optional[str] = None
    is_veg: Optional[bool] = None
    price: Optional[float] = None
    is_featured: bool = False
    display_order: int = 0


class MenuSyncPayload(BaseModel):
    week_start: Optional[str] = None   # YYYY-MM-DD; defaults to current Monday
    items: list[MenuItem]
    source: str = "manual"             # 'scraper' | 'manual' | 'api'


# ---------------------------------------------------------------------------
# POST /api/menu/sync — store this week's menu
# ---------------------------------------------------------------------------

@router.post("/sync")
def sync_menu(payload: MenuSyncPayload):
    """
    Accept a menu payload and store it in weekly_menu.
    Called by the Playwright scraper or manually by the team.
    """
    week_start = (
        date.fromisoformat(payload.week_start)
        if payload.week_start
        else _current_week_start()
    )

    upserted = 0
    errors = []

    with get_cursor(commit=True) as cur:
        # Clear old entries for this week so we get a clean snapshot
        cur.execute("DELETE FROM weekly_menu WHERE week_start = %s", (week_start,))

        for idx, item in enumerate(payload.items):
            try:
                menu_item_id = _resolve_menu_item_id(cur, item.item_name)

                # Update the base menu_items catalog with latest info
                if item.price is not None or item.category is not None or item.is_veg is not None:
                    cur.execute(
                        """UPDATE menu_items SET
                            category  = COALESCE(%s, category),
                            is_veg    = COALESCE(%s, is_veg),
                            avg_price = COALESCE(%s, avg_price),
                            is_active = true
                           WHERE id = %s""",
                        (item.category, item.is_veg, item.price, menu_item_id),
                    )

                cur.execute(
                    """INSERT INTO weekly_menu
                        (week_start, menu_item_id, item_name, category, is_veg,
                         price, is_featured, display_order)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (week_start, item_name) DO UPDATE SET
                           category      = EXCLUDED.category,
                           is_veg        = EXCLUDED.is_veg,
                           price         = EXCLUDED.price,
                           is_featured   = EXCLUDED.is_featured,
                           display_order = EXCLUDED.display_order""",
                    (
                        week_start,
                        menu_item_id,
                        item.item_name,
                        item.category,
                        item.is_veg,
                        item.price,
                        item.is_featured,
                        item.display_order or idx,
                    ),
                )
                upserted += 1
            except Exception as e:
                errors.append({"item": item.item_name, "error": str(e)})
                logger.warning("menu_sync: failed to upsert %s — %s", item.item_name, e)

        # Log the sync
        status = "ok" if not errors else ("partial" if upserted else "error")
        cur.execute(
            """INSERT INTO menu_sync_log
                (week_start, source, items_found, items_upserted, status, error_msg, raw_snapshot)
               VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)""",
            (
                week_start,
                payload.source,
                len(payload.items),
                upserted,
                status,
                json.dumps(errors) if errors else None,
                json.dumps([i.dict() for i in payload.items]),
            ),
        )

    logger.info(
        "menu_sync: week=%s source=%s upserted=%d errors=%d",
        week_start, payload.source, upserted, len(errors),
    )
    return {
        "week_start": str(week_start),
        "items_received": len(payload.items),
        "items_upserted": upserted,
        "errors": errors,
        "status": status,
    }


# ---------------------------------------------------------------------------
# GET /api/menu/current — return this week's menu for agents
# ---------------------------------------------------------------------------

@router.get("/current")
def get_current_menu():
    """
    Return this week's menu items.
    Falls back to the full active catalog if no weekly snapshot exists.
    """
    week_start = _current_week_start()

    with get_cursor(commit=False) as cur:
        # Try weekly snapshot first
        cur.execute(
            """SELECT item_name, category, is_veg, price, is_featured
               FROM weekly_menu
               WHERE week_start = %s
               ORDER BY is_featured DESC, display_order""",
            (week_start,),
        )
        rows = cur.fetchall()

        source = "weekly_snapshot"
        if not rows:
            # Fall back to full active catalog
            cur.execute(
                """SELECT item_name, category, is_veg, avg_price AS price, false AS is_featured
                   FROM menu_items WHERE is_active = true
                   ORDER BY category, item_name"""
            )
            rows = cur.fetchall()
            source = "full_catalog"

        items = [dict(r) for r in rows]

        # Last sync info
        cur.execute(
            "SELECT synced_at, items_upserted, status FROM menu_sync_log "
            "ORDER BY synced_at DESC LIMIT 1"
        )
        last_sync = dict(cur.fetchone()) if cur.rowcount else {}

    return {
        "week_start": str(week_start),
        "source": source,
        "item_count": len(items),
        "items": items,
        "last_sync": last_sync,
    }


# ---------------------------------------------------------------------------
# GET /api/menu/history — last N syncs (for debugging)
# ---------------------------------------------------------------------------

@router.get("/history")
def get_sync_history(limit: int = 10):
    with get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT synced_at, week_start, source, items_found, items_upserted, status, error_msg "
            "FROM menu_sync_log ORDER BY synced_at DESC LIMIT %s",
            (limit,),
        )
        return {"syncs": [dict(r) for r in cur.fetchall()]}


# ---------------------------------------------------------------------------
# POST /api/menu/scrape-trigger — kick off the Playwright scraper
# ---------------------------------------------------------------------------

@router.post("/scrape-trigger")
def trigger_scrape(background: bool = True):
    """
    Launch scripts/scrape_menu.py as a background process.
    The script handles OTP, scrapes the site, and calls POST /api/menu/sync.
    Returns immediately; check /api/menu/history for results.
    """
    script = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "scrape_menu.py")
    script = os.path.abspath(script)

    if not os.path.exists(script):
        raise HTTPException(status_code=500, detail="scrape_menu.py not found")

    try:
        if background:
            subprocess.Popen(
                ["python", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={**os.environ},
            )
            return {"status": "triggered", "mode": "background", "script": script}
        else:
            result = subprocess.run(
                ["python", script],
                capture_output=True,
                text=True,
                timeout=120,
                env={**os.environ},
            )
            return {
                "status": "completed" if result.returncode == 0 else "failed",
                "returncode": result.returncode,
                "stdout": result.stdout[-2000:],
                "stderr": result.stderr[-2000:],
            }
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "error": "Script exceeded 120s"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
