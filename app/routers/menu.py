"""
Menu management router — Airtable ↔ PostgreSQL bidirectional sync.

Airtable (base appuy2VTIao6XVpIW, table "Menu Catalog") is the source of truth.
Each Airtable row = one menu item.  Deleting a row from Airtable marks it as
discarded in Postgres automatically (detected on next sync).

Routes:
  GET  /api/menu/items          — list active items from Postgres
  GET  /api/menu/items/inactive — list discarded items
  GET  /api/menu/items/{id}/history — change history for one item
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
AIRTABLE_TABLE = "Menu Catalog"
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
    price: Optional[float] = None
    added_date: Optional[date] = None


class MenuItemOut(MenuItemIn):
    id: int
    active: bool = True
    discarded_date: Optional[date] = None
    airtable_record_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


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
    """Convert a dict to Airtable field names for the Menu Catalog table."""
    fields: dict = {"Item Name": item.get("item_name", "")}
    if item.get("category") is not None:
        fields["Category"] = item["category"]
    if item.get("is_veg") is not None:
        fields["Is Veg"] = item["is_veg"]
    if item.get("description") is not None:
        fields["Description"] = item["description"]
    if item.get("image_url") is not None:
        fields["Image URL"] = item["image_url"]
    if item.get("price") is not None:
        fields["Price"] = float(item["price"])
    if item.get("added_date") is not None:
        fields["Added Date"] = str(item["added_date"])
    return fields


def _from_airtable_record(record: dict) -> dict:
    """Convert an Airtable record to a Postgres-compatible dict."""
    f = record.get("fields", {})
    added_raw = f.get("Added Date")
    return {
        "airtable_record_id": record["id"],
        "item_name": f.get("Item Name", ""),
        "category": f.get("Category"),
        "is_veg": f.get("Is Veg"),
        "description": f.get("Description"),
        "image_url": f.get("Image URL"),
        "price": f.get("Price"),
        "added_date": added_raw,  # ISO date string or None
    }


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


def _record_history(cur, menu_catalog_id: int, item_name: str,
                    change_type: str, field_changed: str = None,
                    old_value: str = None, new_value: str = None,
                    source: str = "airtable_sync") -> None:
    """Insert a row into menu_catalog_history."""
    cur.execute(
        """
        INSERT INTO menu_catalog_history
            (menu_catalog_id, item_name, change_type, field_changed, old_value, new_value, source)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (menu_catalog_id, item_name, change_type, field_changed, old_value, new_value, source),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/items")
def list_menu_items(active_only: bool = True):
    """Return menu items from Postgres (active by default)."""
    with get_cursor(commit=False) as cur:
        where = "WHERE active = TRUE" if active_only else ""
        cur.execute(
            f"""
            SELECT id, item_name, category, is_veg, description, image_url,
                   price, active, added_date, discarded_date, airtable_record_id,
                   updated_at::text, created_at::text
            FROM   menu_catalog
            {where}
            ORDER  BY category NULLS LAST, item_name
            """,
        )
        rows = cur.fetchall()
    return {"items": rows, "count": len(rows)}


@router.get("/items/inactive")
def list_inactive_items():
    """Return discarded menu items ordered by discard date."""
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT id, item_name, category, is_veg, price,
                   added_date, discarded_date, airtable_record_id,
                   updated_at::text
            FROM   menu_catalog
            WHERE  active = FALSE
            ORDER  BY discarded_date DESC NULLS LAST, item_name
            """,
        )
        rows = cur.fetchall()
    return {"items": rows, "count": len(rows)}


@router.get("/items/{item_id}/history")
def get_item_history(item_id: int):
    """Return change history for a single menu item."""
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT 1 FROM menu_catalog WHERE id = %s", (item_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Menu item not found")

        cur.execute(
            """
            SELECT id, change_type, field_changed, old_value, new_value,
                   changed_at::text, source
            FROM   menu_catalog_history
            WHERE  menu_catalog_id = %s
            ORDER  BY changed_at DESC
            """,
            (item_id,),
        )
        rows = cur.fetchall()
    return {"item_id": item_id, "history": rows, "count": len(rows)}


@router.post("/sync")
def sync_from_airtable():
    """
    Pull all records from Airtable 'Menu Catalog' and upsert into Postgres.

    Two-phase:
      1. Upsert — create/update each record from Airtable; record history on changes.
      2. Deletion detection — any active Postgres row whose airtable_record_id is
         no longer present in Airtable is marked active=false, discarded_date=today.

    Called by n8n on a daily schedule.
    """
    try:
        records = _airtable_list_all()
    except Exception as e:
        logger.error("Airtable list failed during sync: %s", e)
        raise HTTPException(status_code=502, detail=f"Airtable error: {e}")

    today = date.today()
    seen_airtable_ids: set[str] = set()
    upserted = 0
    discarded = 0
    history_events = 0
    errors = 0

    # ── Phase 1: Upsert ────────────────────────────────────────────────────
    for record in records:
        item = _from_airtable_record(record)
        if not item["item_name"]:
            continue
        at_id = item["airtable_record_id"]
        seen_airtable_ids.add(at_id)

        try:
            with get_cursor(commit=True) as cur:
                # Check for existing row
                cur.execute(
                    "SELECT id, item_name, price, category, is_veg, description, image_url "
                    "FROM menu_catalog WHERE airtable_record_id = %s",
                    (at_id,),
                )
                existing = cur.fetchone()

                if existing is None:
                    # New item — insert
                    cur.execute(
                        """
                        INSERT INTO menu_catalog
                            (airtable_record_id, item_name, category, is_veg,
                             description, image_url, price, added_date, active, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, now())
                        RETURNING id
                        """,
                        (
                            at_id, item["item_name"], item["category"], item["is_veg"],
                            item["description"], item["image_url"], item["price"],
                            item["added_date"] or str(today),
                        ),
                    )
                    new_id = cur.fetchone()["id"]
                    _record_history(cur, new_id, item["item_name"], "added",
                                    new_value=item["item_name"])
                    history_events += 1
                else:
                    # Existing item — update and diff
                    row_id = existing["id"]

                    # Detect price change
                    old_price = float(existing["price"]) if existing["price"] is not None else None
                    new_price = float(item["price"]) if item["price"] is not None else None
                    if old_price != new_price and new_price is not None:
                        _record_history(cur, row_id, item["item_name"], "price_change",
                                        field_changed="price",
                                        old_value=str(old_price),
                                        new_value=str(new_price))
                        history_events += 1

                    # Detect other field changes
                    for field in ("category", "is_veg", "description", "image_url"):
                        old_val = existing[field]
                        new_val = item[field]
                        if old_val != new_val and new_val is not None:
                            _record_history(cur, row_id, item["item_name"], "field_update",
                                            field_changed=field,
                                            old_value=str(old_val) if old_val is not None else None,
                                            new_value=str(new_val))
                            history_events += 1

                    cur.execute(
                        """
                        UPDATE menu_catalog
                        SET    item_name   = %s,
                               category   = %s,
                               is_veg     = %s,
                               description= %s,
                               image_url  = %s,
                               price      = %s,
                               active     = TRUE,
                               discarded_date = NULL,
                               updated_at = now()
                        WHERE  airtable_record_id = %s
                        """,
                        (
                            item["item_name"], item["category"], item["is_veg"],
                            item["description"], item["image_url"], item["price"],
                            at_id,
                        ),
                    )

            upserted += 1
        except Exception as e:
            logger.error("Failed to upsert airtable record %s: %s", at_id, e)
            errors += 1

    # ── Phase 2: Deletion detection ────────────────────────────────────────
    try:
        with get_cursor(commit=True) as cur:
            cur.execute(
                "SELECT id, item_name, airtable_record_id "
                "FROM menu_catalog WHERE active = TRUE AND airtable_record_id IS NOT NULL"
            )
            active_rows = cur.fetchall()

            for row in active_rows:
                if row["airtable_record_id"] not in seen_airtable_ids:
                    cur.execute(
                        """
                        UPDATE menu_catalog
                        SET    active = FALSE,
                               discarded_date = %s,
                               updated_at = now()
                        WHERE  id = %s
                        """,
                        (today, row["id"]),
                    )
                    _record_history(cur, row["id"], row["item_name"], "discarded",
                                    new_value=str(today))
                    discarded += 1
                    history_events += 1
    except Exception as e:
        logger.error("Deletion detection failed: %s", e)
        errors += 1

    logger.info(
        "Menu catalog sync — upserted=%d discarded=%d history_events=%d errors=%d",
        upserted, discarded, history_events, errors,
    )
    return {
        "status": "ok",
        "upserted": upserted,
        "discarded": discarded,
        "history_events": history_events,
        "errors": errors,
        "total": len(records),
    }
