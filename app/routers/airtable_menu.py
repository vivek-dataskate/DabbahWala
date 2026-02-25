"""
Menu management router — Airtable ↔ PostgreSQL bidirectional sync.

Airtable (base appuy2VTIao6XVpIW, table "Weekly Menu") is the source of truth
for human-managed edits.  This router provides:

  GET  /api/menu/items          — list items from Postgres
  POST /api/menu/items          — create item in Airtable + Postgres
  PUT  /api/menu/items/{id}     — update item in Airtable + Postgres
  DELETE /api/menu/items/{id}   — delete item from Airtable + Postgres
  POST /api/menu/sync           — pull all records from Airtable → upsert Postgres
                                  (called by n8n on a schedule)
"""

import logging
import os
from datetime import date
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_cursor

logger = logging.getLogger(__name__)
router = APIRouter()

AIRTABLE_BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "appuy2VTIao6XVpIW")
AIRTABLE_TABLE = "Weekly Menu"
AIRTABLE_API_KEY = os.environ.get("AIRTABLE_API_KEY", "")
AIRTABLE_BASE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class MenuItemIn(BaseModel):
    item_name: str
    category: Optional[str] = None
    is_veg: Optional[bool] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    week_start: Optional[date] = None
    active: Optional[bool] = True
    price: Optional[float] = None


class MenuItemOut(MenuItemIn):
    id: int
    airtable_record_id: Optional[str] = None
    scraped_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Internal Airtable helpers
# ---------------------------------------------------------------------------

def _airtable_headers() -> dict:
    key = AIRTABLE_API_KEY or os.environ.get("AIRTABLE_API_KEY", "")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _to_airtable_fields(item: dict) -> dict:
    """Convert Postgres row / Pydantic dict to Airtable field names."""
    fields: dict = {"Name": item.get("item_name", "")}
    if item.get("category") is not None:
        fields["Category"] = item["category"]
    if item.get("is_veg") is not None:
        fields["Is Veg"] = item["is_veg"]
    if item.get("description") is not None:
        fields["Description"] = item["description"]
    if item.get("image_url") is not None:
        fields["Image URL"] = item["image_url"]
    if item.get("week_start") is not None:
        fields["Week Start"] = str(item["week_start"])
    if item.get("active") is not None:
        fields["Active"] = item["active"]
    if item.get("price") is not None:
        fields["Price"] = float(item["price"])
    return fields


def _from_airtable_record(record: dict) -> dict:
    """Convert Airtable record to Postgres-compatible dict."""
    f = record.get("fields", {})
    return {
        "airtable_record_id": record["id"],
        "item_name": f.get("Name", ""),
        "category": f.get("Category"),
        "is_veg": f.get("Is Veg"),
        "description": f.get("Description"),
        "image_url": f.get("Image URL"),
        "week_start": f.get("Week Start"),
        "active": f.get("Active", True),
        "price": f.get("Price"),
    }


def _airtable_create(fields: dict) -> dict:
    url = f"{AIRTABLE_BASE_URL}/{AIRTABLE_TABLE}"
    resp = httpx.post(url, headers=_airtable_headers(), json={"fields": fields}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _airtable_update(record_id: str, fields: dict) -> dict:
    url = f"{AIRTABLE_BASE_URL}/{AIRTABLE_TABLE}/{record_id}"
    resp = httpx.patch(url, headers=_airtable_headers(), json={"fields": fields}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _airtable_delete(record_id: str) -> None:
    url = f"{AIRTABLE_BASE_URL}/{AIRTABLE_TABLE}/{record_id}"
    resp = httpx.delete(url, headers=_airtable_headers(), timeout=15)
    resp.raise_for_status()


def _airtable_list_all() -> list[dict]:
    """Fetch all records from Airtable with pagination."""
    records = []
    params: dict = {"pageSize": 100}
    while True:
        resp = httpx.get(
            f"{AIRTABLE_BASE_URL}/{AIRTABLE_TABLE}",
            headers=_airtable_headers(),
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        records.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break
        params["offset"] = offset
    return records


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/items")
def list_menu_items(week_start: Optional[str] = None, active_only: bool = False):
    """Return all menu items from Postgres, optionally filtered."""
    with get_cursor(commit=False) as cur:
        conditions = []
        args: list = []
        if week_start:
            conditions.append("week_start = %s")
            args.append(week_start)
        if active_only:
            conditions.append("active = TRUE")
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        cur.execute(
            f"""
            SELECT id, item_name, category, is_veg, description, image_url,
                   week_start, active, price, airtable_record_id,
                   scraped_at::text
            FROM   weekly_menu_schedule
            {where}
            ORDER  BY week_start DESC NULLS LAST, item_name
            """,
            args,
        )
        rows = cur.fetchall()
    return {"items": rows, "count": len(rows)}


@router.post("/items", status_code=201)
def create_menu_item(item: MenuItemIn):
    """Create a new menu item in Airtable and upsert into Postgres."""
    fields = _to_airtable_fields(item.model_dump())
    try:
        at_record = _airtable_create(fields)
        airtable_record_id = at_record["id"]
    except Exception as e:
        logger.error("Airtable create failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Airtable error: {e}")

    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO weekly_menu_schedule
                (item_name, category, is_veg, description, image_url,
                 week_start, active, price, airtable_record_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                item.item_name, item.category, item.is_veg, item.description,
                item.image_url, item.week_start, item.active, item.price,
                airtable_record_id,
            ),
        )
        row = cur.fetchone()

    logger.info("Created menu item id=%s airtable=%s", row["id"], airtable_record_id)
    return {"id": row["id"], "airtable_record_id": airtable_record_id, "status": "created"}


@router.put("/items/{item_id}")
def update_menu_item(item_id: int, item: MenuItemIn):
    """Update a menu item in both Airtable and Postgres."""
    with get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT airtable_record_id FROM weekly_menu_schedule WHERE id = %s",
            (item_id,),
        )
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Menu item not found")

    airtable_record_id = row["airtable_record_id"]
    fields = _to_airtable_fields(item.model_dump())

    if airtable_record_id:
        try:
            _airtable_update(airtable_record_id, fields)
        except Exception as e:
            logger.error("Airtable update failed for %s: %s", airtable_record_id, e)
            raise HTTPException(status_code=502, detail=f"Airtable error: {e}")
    else:
        # Item wasn't in Airtable yet — create it now
        try:
            at_record = _airtable_create(fields)
            airtable_record_id = at_record["id"]
        except Exception as e:
            logger.error("Airtable create-on-update failed: %s", e)
            raise HTTPException(status_code=502, detail=f"Airtable error: {e}")

    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE weekly_menu_schedule
            SET    item_name = %s, category = %s, is_veg = %s,
                   description = %s, image_url = %s, week_start = %s,
                   active = %s, price = %s, airtable_record_id = %s
            WHERE  id = %s
            """,
            (
                item.item_name, item.category, item.is_veg, item.description,
                item.image_url, item.week_start, item.active, item.price,
                airtable_record_id, item_id,
            ),
        )

    return {"id": item_id, "airtable_record_id": airtable_record_id, "status": "updated"}


@router.delete("/items/{item_id}")
def delete_menu_item(item_id: int):
    """Delete a menu item from Airtable and Postgres."""
    with get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT airtable_record_id FROM weekly_menu_schedule WHERE id = %s",
            (item_id,),
        )
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Menu item not found")

    airtable_record_id = row["airtable_record_id"]
    if airtable_record_id:
        try:
            _airtable_delete(airtable_record_id)
        except Exception as e:
            logger.warning("Airtable delete failed for %s: %s — removing from Postgres anyway", airtable_record_id, e)

    with get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM weekly_menu_schedule WHERE id = %s", (item_id,))

    return {"id": item_id, "status": "deleted"}


@router.post("/sync")
def sync_from_airtable():
    """
    Pull all records from Airtable 'Weekly Menu' table and upsert into Postgres.
    Called by n8n on a schedule (hourly).
    """
    try:
        records = _airtable_list_all()
    except Exception as e:
        logger.error("Airtable list failed during sync: %s", e)
        raise HTTPException(status_code=502, detail=f"Airtable error: {e}")

    upserted = 0
    errors = 0
    for record in records:
        item = _from_airtable_record(record)
        if not item["item_name"]:
            continue
        try:
            with get_cursor(commit=True) as cur:
                cur.execute(
                    """
                    INSERT INTO weekly_menu_schedule
                        (airtable_record_id, item_name, category, is_veg,
                         description, image_url, week_start, active, price)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (airtable_record_id)
                        WHERE airtable_record_id IS NOT NULL
                    DO UPDATE SET
                        item_name   = EXCLUDED.item_name,
                        category    = EXCLUDED.category,
                        is_veg      = EXCLUDED.is_veg,
                        description = EXCLUDED.description,
                        image_url   = EXCLUDED.image_url,
                        week_start  = EXCLUDED.week_start,
                        active      = EXCLUDED.active,
                        price       = EXCLUDED.price,
                        scraped_at  = now()
                    """,
                    (
                        item["airtable_record_id"], item["item_name"],
                        item["category"], item["is_veg"], item["description"],
                        item["image_url"], item["week_start"],
                        item["active"], item["price"],
                    ),
                )
            upserted += 1
        except Exception as e:
            logger.error("Failed to upsert airtable record %s: %s", item["airtable_record_id"], e)
            errors += 1

    logger.info("Airtable menu sync — upserted=%d errors=%d", upserted, errors)
    return {"status": "ok", "upserted": upserted, "errors": errors, "total": len(records)}
