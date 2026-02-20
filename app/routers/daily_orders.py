"""
Daily order processing endpoint.
n8n uploads CSV data -> this endpoint processes orders, creates contacts,
records orders, fires events, and detects opportunities.
"""
import asyncio
import csv
import io
import json
import logging
import os
import re
import uuid
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import httpx
from fastapi import APIRouter, Form, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.db import get_cursor
from app.services.airtable_sync import create_field_sales_task

logger = logging.getLogger(__name__)

router = APIRouter()


def _col(row: dict, *candidates: str) -> str:
    """Return first matching column value, case-insensitive, or empty string."""
    low = {k.lower().strip(): v for k, v in row.items()}
    for key in candidates:
        v = low.get(key.lower().strip())
        if v is not None:
            return str(v)
    return ''


def normalize_phone(phone) -> str:
    if not phone:
        return ''
    digits = re.sub(r'\D', '', str(phone))
    return digits[-10:] if len(digits) >= 10 else ''


def normalize_name(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r'[^\w\s]', '', name)
    name = re.sub(r'\s+', ' ', name)
    return name


def normalize_dish(name: str) -> str:
    """Normalize dish name for matching: lowercase, strip, collapse spaces."""
    name = name.strip()
    name = re.sub(r'\s+', ' ', name)
    return name


def _parse_delivery_slot(slot: str) -> tuple:
    """Parse '9:30 AM - 12:30 PM' into ('09:30', '12:30') for Shipday.

    Returns (pickup_time, delivery_time) in HH:MM 24-hour format.
    """
    def _to_24h(t: str) -> str:
        t = t.strip()
        for fmt in ('%I:%M %p', '%I %p', '%H:%M'):
            try:
                return datetime.strptime(t, fmt).strftime('%H:%M')
            except ValueError:
                pass
        return t

    parts = [p.strip() for p in slot.split(' - ', 1)]
    if len(parts) == 2:
        return _to_24h(parts[0]), _to_24h(parts[1])
    if len(parts) == 1 and parts[0]:
        return _to_24h(parts[0]), ''
    return '', ''


async def _push_orders_to_shipday(orders_grouped: dict,
                                   restaurant_name: str = "DabbahWala",
                                   restaurant_address: str = "") -> dict:
    """Push every unique order to the Shipday Orders API concurrently.

    Returns a summary dict with per-order results and a link to the dashboard.
    """
    api_key = os.environ.get("SHIPDAY_API_KEY", "")
    if not api_key:
        logger.warning("SHIPDAY_API_KEY not set — skipping Shipday push")
        return {"skipped": True, "reason": "SHIPDAY_API_KEY not configured"}

    headers = {
        "Authorization": f"Basic {api_key}",
        "Content-Type": "application/json",
    }

    # Build one payload per unique order
    items_to_push = []
    for order_num, item_rows in orders_grouped.items():
        first = item_rows[0]
        customer_name = _col(first, 'Customer Name', 'Name', 'Customer', 'customer_name').strip()
        phone = _col(first, 'Customer Phone Number', 'Phone', 'Customer Phone', 'phone').strip()
        address = _col(first, 'Customer Address', 'Address', 'address').strip()
        date_raw = _col(first, 'Date', 'Order Date', 'date', 'order_date').strip()
        delivery_slot = _col(first, 'Delivery Slot Name', 'Delivery Slot', 'delivery_slot').strip()
        delivery_instr = _col(first, 'Delivery Instructions', 'Delivery Instruction',
                              'delivery_instructions', 'Notes', 'notes').strip()

        try:
            delivery_date = datetime.strptime(date_raw, '%d/%m/%Y').strftime('%Y-%m-%d')
        except Exception:
            delivery_date = datetime.now().strftime('%Y-%m-%d')

        pickup_time, delivery_time = _parse_delivery_slot(delivery_slot)

        order_items = []
        for row in item_rows:
            dish = _col(row, 'Dish Name', 'Item Name', 'Product', 'dish_name', 'item_name', 'Item').strip()
            qty = int(_col(row, 'Quantity', 'Qty', 'qty', 'quantity') or 1)
            price = float(_col(row, 'Unit Price', 'Price', 'unit_price', 'price') or 0)
            if dish:
                order_items.append({"name": dish, "quantity": qty, "unitPrice": price})

        payload: dict = {
            "orderNumber": order_num,
            "customerName": customer_name,
            "customerAddress": address,
            "customerPhoneNumber": phone,
            "restaurantName": restaurant_name,
        }
        if restaurant_address:
            payload["restaurantAddress"] = restaurant_address
        if delivery_date:
            payload["expectedDeliveryDate"] = delivery_date
        if pickup_time:
            payload["expectedPickupTime"] = pickup_time
        if delivery_time:
            payload["expectedDeliveryTime"] = delivery_time
        if delivery_instr:
            payload["deliveryInstruction"] = delivery_instr
        if order_items:
            payload["orderItems"] = order_items

        items_to_push.append((order_num, customer_name, payload))

    async def _push_one(client: httpx.AsyncClient, order_num: str,
                        customer_name: str, payload: dict) -> dict:
        try:
            resp = await client.post("https://api.shipday.com/orders", json=payload)
            resp.raise_for_status()
            resp_data = resp.json()
            shipday_id = resp_data.get("orderId") or resp_data.get("order_id")
            logger.info("Shipday order created: order=%s shipday_id=%s", order_num, shipday_id)
            return {
                "order_number": order_num,
                "customer_name": customer_name,
                "status": "created",
                "shipday_order_id": shipday_id,
            }
        except httpx.HTTPStatusError as e:
            body = e.response.text[:300]
            logger.error("Shipday create failed order=%s status=%d body=%s",
                         order_num, e.response.status_code, body)
            return {
                "order_number": order_num,
                "customer_name": customer_name,
                "status": "failed",
                "error": f"HTTP {e.response.status_code}: {body}",
            }
        except Exception as e:
            logger.error("Shipday create error order=%s: %s", order_num, e)
            return {
                "order_number": order_num,
                "customer_name": customer_name,
                "status": "failed",
                "error": str(e),
            }

    try:
        async with httpx.AsyncClient(headers=headers, timeout=20.0) as client:
            results = await asyncio.gather(
                *[_push_one(client, on, cn, pl) for on, cn, pl in items_to_push]
            )
    except Exception as e:
        logger.error("Shipday batch push failed: %s", e, exc_info=True)
        return {"skipped": True, "reason": f"Shipday push error: {e}"}

    results = list(results)
    succeeded = sum(1 for r in results if r["status"] == "created")
    failed = sum(1 for r in results if r["status"] == "failed")

    logger.info(
        "Shipday push complete: submitted=%d succeeded=%d failed=%d",
        len(items_to_push), succeeded, failed,
    )
    return {
        "submitted": len(items_to_push),
        "succeeded": succeeded,
        "failed": failed,
        "orders": results,
        "dashboard_url": "https://dispatch.shipday.com",
    }


# Temp directory where generated Shipday CSVs are stored (cleaned up on server restart)
_SHIPDAY_EXPORT_DIR = Path("/tmp/shipday_exports")


def _generate_shipday_csv(orders_grouped: dict) -> str:
    """Convert orders_grouped into a Shipday-compatible CSV string for manual import.

    One row per order. Order items are joined as 'Dish (xQty)' entries separated by ' | '.
    """
    output = io.StringIO()
    fieldnames = [
        "Order Number",
        "Customer Name",
        "Customer Phone",
        "Customer Address",
        "Restaurant Name",
        "Expected Delivery Date",
        "Expected Pickup Time",
        "Expected Delivery Time",
        "Delivery Instruction",
        "Order Items",
        "Order Total",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for order_num, item_rows in orders_grouped.items():
        first = item_rows[0]
        customer_name = _col(first, 'Customer Name', 'Name', 'Customer', 'customer_name').strip()
        phone = _col(first, 'Customer Phone Number', 'Phone', 'Customer Phone', 'phone').strip()
        address = _col(first, 'Customer Address', 'Address', 'address').strip()
        date_raw = _col(first, 'Date', 'Order Date', 'date', 'order_date').strip()
        delivery_slot = _col(first, 'Delivery Slot Name', 'Delivery Slot', 'delivery_slot').strip()
        delivery_instr = _col(first, 'Delivery Instructions', 'Delivery Instruction',
                              'delivery_instructions', 'Notes', 'notes').strip()
        restaurant_name = os.environ.get("SHIPDAY_RESTAURANT_NAME", "DabbahWala")

        try:
            delivery_date = datetime.strptime(date_raw, '%d/%m/%Y').strftime('%Y-%m-%d')
        except Exception:
            delivery_date = datetime.now().strftime('%Y-%m-%d')

        pickup_time, delivery_time = _parse_delivery_slot(delivery_slot)

        item_parts = []
        order_total = 0.0
        for row in item_rows:
            dish = _col(row, 'Dish Name', 'Item Name', 'Product', 'dish_name', 'item_name', 'Item').strip()
            qty = int(_col(row, 'Quantity', 'Qty', 'qty', 'quantity') or 1)
            price = float(_col(row, 'Unit Price', 'Price', 'unit_price', 'price') or 0)
            if dish:
                item_parts.append(f"{dish} (x{qty})")
                order_total += qty * price

        writer.writerow({
            "Order Number": order_num,
            "Customer Name": customer_name,
            "Customer Phone": phone,
            "Customer Address": address,
            "Restaurant Name": restaurant_name,
            "Expected Delivery Date": delivery_date,
            "Expected Pickup Time": pickup_time,
            "Expected Delivery Time": delivery_time,
            "Delivery Instruction": delivery_instr,
            "Order Items": " | ".join(item_parts),
            "Order Total": f"{order_total:.2f}",
        })

    return output.getvalue()


def _save_shipday_csv(orders_grouped: dict) -> str:
    """Generate and persist a Shipday CSV to disk. Returns the file_id (UUID stem)."""
    _SHIPDAY_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    file_id = str(uuid.uuid4())
    csv_path = _SHIPDAY_EXPORT_DIR / f"{file_id}.csv"
    csv_path.write_text(_generate_shipday_csv(orders_grouped), encoding="utf-8")
    logger.info("Shipday export CSV saved: %s", csv_path)
    return file_id


def _upload_shipday_csv_to_drive(csv_content: str, filename: str) -> str:
    """Upload a CSV string to Google Drive and return the web view link.

    Requires env vars:
      GOOGLE_SERVICE_ACCOUNT_JSON  — full JSON of the service account key file
      GOOGLE_DRIVE_FOLDER_ID       — Drive folder ID to upload into

    Returns the Drive web view URL, or empty string on any failure.
    """
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    if not sa_json or not folder_id:
        return ""

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseUpload

        creds = service_account.Credentials.from_service_account_info(
            json.loads(sa_json),
            scopes=["https://www.googleapis.com/auth/drive.file"],
        )
        service = build("drive", "v3", credentials=creds, cache_discovery=False)

        file_metadata = {"name": filename, "parents": [folder_id]}
        media = MediaIoBaseUpload(
            io.BytesIO(csv_content.encode("utf-8")),
            mimetype="text/csv",
            resumable=False,
        )
        uploaded = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id,webViewLink",
            supportsAllDrives=True,
        ).execute()

        link = uploaded.get("webViewLink", "")
        logger.info("Shipday CSV uploaded to Drive: %s", link)
        return link
    except Exception as e:
        logger.warning("Google Drive upload failed: %s", e)
        return ""


@router.get("/download-shipday-csv/{file_id}")
async def download_shipday_csv(file_id: str):
    """Serve a previously generated Shipday import CSV for download."""
    # Sanitise: only allow UUID-shaped IDs
    if not re.fullmatch(r'[0-9a-f\-]{36}', file_id):
        raise HTTPException(status_code=400, detail="Invalid file ID")
    csv_path = _SHIPDAY_EXPORT_DIR / f"{file_id}.csv"
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail="File not found or expired")
    return FileResponse(
        path=str(csv_path),
        media_type="text/csv",
        filename="shipday_orders.csv",
    )


def _load_menu_lookup(cur) -> tuple:
    """Load alias table + master menu items into lookup dicts.
    Returns (alias_map, master_norm_map, master_set).
    """
    # alias table: csv_name -> canonical_name
    alias_map = {}
    try:
        cur.execute("SELECT alias, canonical_name FROM menu_item_aliases")
        for r in cur.fetchall():
            alias_map[r['alias']] = r['canonical_name']
            alias_map[r['alias'].strip()] = r['canonical_name']
        logger.debug("Loaded %d menu aliases", len(alias_map))
    except Exception as e:
        logger.warning("menu_item_aliases table not accessible: %s — proceeding without aliases", e)

    # master menu items: normalized -> actual name
    cur.execute("SELECT id, item_name, category, is_veg, avg_price FROM menu_items WHERE is_active = true")
    master_rows = cur.fetchall()
    master_set = {r['item_name'] for r in master_rows}
    master_norm = {}
    for r in master_rows:
        norm = r['item_name'].strip().lower()
        master_norm[norm] = r['item_name']

    logger.debug("Loaded %d active menu items and %d aliases for dish resolution", len(master_set), len(alias_map))
    return alias_map, master_norm, master_set


def resolve_dish_name(raw_name: str, alias_map: dict, master_norm: dict, master_set: set) -> str:
    """Resolve a CSV dish name to a canonical master menu item.
    Priority: exact match -> alias -> normalized match -> fuzzy match -> return cleaned name.
    """
    cleaned = normalize_dish(raw_name)

    # 1. Exact match in master
    if cleaned in master_set:
        return cleaned

    # 2. Alias lookup (exact)
    if cleaned in alias_map:
        return alias_map[cleaned]

    # 3. Normalized (case-insensitive) match
    norm = cleaned.lower()
    if norm in master_norm:
        return master_norm[norm]

    # 4. Fuzzy match against master (threshold 0.85)
    best_score = 0
    best_match = None
    for master_name in master_set:
        score = SequenceMatcher(None, norm, master_name.lower()).ratio()
        if score > best_score and score >= 0.85:
            best_score = score
            best_match = master_name
    if best_match:
        return best_match

    # 5. No match — return cleaned name (will be created as new item)
    return cleaned


class DailyOrderResult(BaseModel):
    date: str
    total_orders: int
    rows_skipped: int
    duplicate_orders_skipped: int = 0
    total_items: int
    total_revenue: float
    new_contacts: int
    existing_contacts: int
    opportunities_created: int
    lifecycle_updated: int
    campaigns_queued: int
    menu_items_matched: int
    menu_items_created: int
    agent_analysis: list = []
    field_opportunities: list = []
    campaign_moves: list = []
    airtable_synced: int = 0
    shipday_result: dict = {}
    shipday_csv_download_url: str = ""
    shipday_drive_url: str = ""


@router.post("/process", response_model=DailyOrderResult)
@router.post("/upload-csv", response_model=DailyOrderResult)
async def process_daily_orders(
    file: UploadFile = File(...),
    push_to_shipday: str = Form("false"),
):
    """
    Upload a CSV of daily orders. Columns are matched flexibly.
    After ingestion:
      1. Summarizes the data (revenue, by source, new vs returning).
      2. Runs the full inference→decision→orchestration cycle for each
         ingested contact (capped at 5 to stay within response time limits).
      3. Returns actionable field opportunities alongside the ingest stats.
    Upload a processing-data-YYYY-MM-DD.csv and process all orders.
    Creates new contacts, records orders + items, fires events, detects opportunities.
    """
    from collections import Counter

    logger.info(
        "POST /process — received CSV file '%s' size=%d bytes",
        file.filename,
        file.size or 0,
    )
    content = await file.read()
    # utf-8-sig strips BOM from Excel-generated CSVs
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        logger.warning("UTF-8 decode failed — falling back to latin-1 for file '%s'", file.filename)
        text = content.decode('latin-1')
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)

    logger.info("CSV parsed: %d rows, columns=%s", len(rows), list(rows[0].keys()) if rows else [])
    if not rows:
        logger.error("Empty CSV file submitted: '%s'", file.filename)
        raise HTTPException(status_code=400, detail="Empty CSV file")

    # Group by Order Number (accept common column name variants)
    orders_grouped = defaultdict(list)
    skipped_rows = 0
    for row in rows:
        order_num = _col(row, 'Order Number', 'order_number', 'Order #', 'OrderNumber', 'order_id').strip()
        if order_num:
            orders_grouped[order_num].append(row)
        else:
            skipped_rows += 1

    logger.info(
        "Grouped %d orders from %d rows (%d rows skipped — no order number)",
        len(orders_grouped),
        len(rows),
        skipped_rows,
    )
    if skipped_rows > 0:
        logger.warning(
            "%d CSV rows had no order number — they will be skipped. "
            "Check CSV column names for 'Order Number', 'order_id', etc.",
            skipped_rows,
        )

    # Build contact + menu lookups
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT id, email, phone, first_name, last_name, total_orders, "
                     "last_order_at, lifecycle_segment, primary_source FROM contacts "
                     "WHERE phone IS NOT NULL AND phone != ''")
        phone_lookup = {}
        for r in cur.fetchall():
            ph = normalize_phone(r.get('phone', ''))
            if ph:
                phone_lookup[ph] = dict(r)

        cur.execute("SELECT id, email, phone, first_name, last_name FROM contacts "
                     "WHERE first_name IS NOT NULL")
        name_lookup = {}
        for r in cur.fetchall():
            full = f"{r.get('first_name', '')} {r.get('last_name', '')}".strip().lower()
            if full:
                name_lookup[full] = dict(r)

        alias_map, master_norm, master_set = _load_menu_lookup(cur)

    logger.info(
        "Lookup tables loaded: %d phone records, %d name records",
        len(phone_lookup),
        len(name_lookup),
    )

    new_contact_count = 0
    existing_contact_count = 0
    order_count = 0
    item_count = 0
    opportunity_count = 0
    menu_matched = 0
    menu_created = 0
    duplicate_orders_skipped = 0
    order_date_str = ''
    agent_analysis: list = []
    field_opportunities: list = []
    airtable_tasks: list = []

    # Pre-fetch existing order_id_externals so we can skip duplicates efficiently
    _batch_nums = [n for n in orders_grouped if n]
    existing_order_nums: set = set()
    if _batch_nums:
        with get_cursor(commit=False) as _ck:
            _ck.execute(
                "SELECT order_id_external FROM orders WHERE order_id_external = ANY(%s)",
                (_batch_nums,)
            )
            existing_order_nums = {r['order_id_external'] for r in _ck.fetchall()}
    if existing_order_nums:
        logger.info("Skipping %d duplicate orders already in DB", len(existing_order_nums))

    with get_cursor(commit=True) as cur:
        for order_num, item_rows in orders_grouped.items():
            if order_num in existing_order_nums:
                duplicate_orders_skipped += 1
                continue
            first = item_rows[0]
            phone = normalize_phone(_col(first, 'Customer Phone Number', 'Phone', 'Customer Phone', 'phone'))
            name = _col(first, 'Customer Name', 'Name', 'Customer', 'customer_name').strip()
            address = _col(first, 'Customer Address', 'Address', 'address').strip()
            date_raw = _col(first, 'Date', 'Order Date', 'date', 'order_date').strip()
            delivery_date_raw = _col(first, 'Delivery Date', 'delivery_date', 'DeliveryDate').strip()
            plan_name = _col(first, 'Plan Name', 'plan_name', 'Plan').strip()
            delivery_slot = _col(first, 'Delivery Slot Name', 'Delivery Slot', 'delivery_slot').strip()
            order_type = _col(first, 'Order Type', 'order_type', 'Type').strip()
            notes = _col(first, 'Notes', 'notes', 'Note', 'Delivery Notes', 'Order Notes').strip()

            try:
                order_date = datetime.strptime(date_raw, '%d/%m/%Y').strftime('%Y-%m-%d')
            except Exception:
                order_date = datetime.now().strftime('%Y-%m-%d')
            order_date_str = order_date

            # delivery_date: use explicit column if present, otherwise fall back to order_date
            # (for daily CSV uploads the Date column IS the delivery date)
            try:
                delivery_date = datetime.strptime(delivery_date_raw, '%d/%m/%Y').strftime('%Y-%m-%d') if delivery_date_raw else order_date
            except Exception:
                delivery_date = order_date

            is_sub = 'plan' in plan_name.lower() or 'subscription' in order_type.lower() or 'scheduled' in order_type.lower()

            # Pre-compute order total for recommendations
            order_total = sum(
                float(_col(row, 'Unit Price', 'Price', 'Unit Price (USD)', 'unit_price', 'price') or 0) *
                int(_col(row, 'Quantity', 'Qty', 'qty', 'quantity') or 1)
                for row in item_rows
            )

            # Match contact
            contact = None
            name_norm = normalize_name(name)

            if phone and phone in phone_lookup:
                contact = phone_lookup[phone]
            elif name_norm in name_lookup:
                contact = name_lookup[name_norm]
            else:
                # Fuzzy match
                best_score = 0
                for full_name, c in name_lookup.items():
                    if not name_norm or not full_name or name_norm[0] != full_name[0]:
                        continue
                    score = SequenceMatcher(None, name_norm, full_name).ratio()
                    if score > best_score and score > 0.80:
                        best_score = score
                        contact = c

            contact_id = None
            if contact:
                contact_id = contact['id']
                existing_contact_count += 1

                # Detect opportunities
                lifecycle = contact.get('lifecycle_segment', 'cold')
                prev_orders = contact.get('total_orders', 0)

                if lifecycle in ('lapsed_customer', 'reactivation_candidate'):
                    first_name = name.split()[0] if name else 'there'
                    cur.execute(
                        "SELECT create_opportunity(%s, 'send_sms'::opportunity_action, 'hot', %s, %s, 0.90)",
                        (contact_id,
                         f"Lapsed customer '{name}' placed a new order!",
                         f"Welcome back {first_name}! We're thrilled to cook for you again. Enjoy your meal!")
                    )
                    opportunity_count += 1

                if prev_orders == 0:
                    first_name = name.split()[0] if name else 'there'
                    cur.execute(
                        "SELECT create_opportunity(%s, 'send_sms'::opportunity_action, 'warm', %s, %s, 0.80)",
                        (contact_id,
                         f"First order from '{name}'. Potential subscription conversion.",
                         f"Hi {first_name}, hope you loved today's meal! Save with a weekly subscription. Reply SUBSCRIBE for details.")
                    )
                    opportunity_count += 1

                if contact.get('primary_source') == 'Food Delivery Apps':
                    first_name = name.split()[0] if name else 'there'
                    cur.execute(
                        "SELECT create_opportunity(%s, 'send_sms'::opportunity_action, 'hot', %s, %s, 0.90)",
                        (contact_id,
                         f"App customer '{name}' ordering direct!",
                         f"Thanks for ordering direct, {first_name}! You save on fees and get priority delivery.")
                    )
                    opportunity_count += 1

                # Build field agent recommendation for this contact
                first_name = name.split()[0] if name else 'Customer'
                _last_name = contact.get('last_name', '') or ''
                _email = contact.get('email', '') or ''
                _last_order_at = contact.get('last_order_at')
                if lifecycle in ('lapsed_customer', 'reactivation_candidate'):
                    _reason = (
                        f"{first_name} is returning after a lapse. Call to confirm satisfaction "
                        f"and offer a loyalty reward to retain them long-term."
                    )
                    agent_analysis.append({
                        'contact_name': name,
                        'chosen_action': 'winback_follow_up',
                        'chosen_channel': 'call',
                        'reasoning_snippet': _reason,
                    })
                    field_opportunities.append({
                        'contact_name': name,
                        'action': 'winback',
                        'reasoning': (
                            f"Lapsed customer placing a new order — high retention opportunity. "
                            f"Personal follow-up call recommended to lock in loyalty."
                        ),
                    })
                    airtable_tasks.append({
                        'first_name': first_name, 'last_name': _last_name,
                        'phone': phone or '', 'email': _email,
                        'priority': 'Hot', 'action_type': 'winback_follow_up',
                        'reason': _reason, 'suggested_message': _reason,
                        'lifecycle_segment': lifecycle, 'total_orders': prev_orders,
                        'last_order_at': _last_order_at,
                    })
                elif contact.get('primary_source') == 'Food Delivery Apps':
                    _reason = (
                        f"{first_name} previously ordered via a delivery app. Reinforce direct ordering "
                        f"benefits — no platform fees, faster delivery, priority support."
                    )
                    agent_analysis.append({
                        'contact_name': name,
                        'chosen_action': 'direct_order_pitch',
                        'chosen_channel': 'sms',
                        'reasoning_snippet': _reason,
                    })
                    field_opportunities.append({
                        'contact_name': name,
                        'action': 'direct_order_pitch',
                        'reasoning': (
                            "App-sourced customer ordering direct — offer a direct-order discount or "
                            "subscription to prevent churn back to app platforms."
                        ),
                    })
                    airtable_tasks.append({
                        'first_name': first_name, 'last_name': _last_name,
                        'phone': phone or '', 'email': _email,
                        'priority': 'Hot', 'action_type': 'direct_order_pitch',
                        'reason': _reason, 'suggested_message': _reason,
                        'lifecycle_segment': lifecycle, 'total_orders': prev_orders,
                        'last_order_at': _last_order_at,
                    })
                elif prev_orders == 0:
                    _reason = (
                        f"First direct order from {first_name}. Prime moment to pitch a weekly "
                        f"subscription — saves ~20% vs daily ordering."
                    )
                    agent_analysis.append({
                        'contact_name': name,
                        'chosen_action': 'subscription_pitch',
                        'chosen_channel': 'sms',
                        'reasoning_snippet': _reason,
                    })
                    field_opportunities.append({
                        'contact_name': name,
                        'action': 'subscription_pitch',
                        'reasoning': (
                            f"First-order customer — convert to subscription while the experience is fresh "
                            f"to maximise lifetime value."
                        ),
                    })
                    airtable_tasks.append({
                        'first_name': first_name, 'last_name': _last_name,
                        'phone': phone or '', 'email': _email,
                        'priority': 'Warm', 'action_type': 'subscription_pitch',
                        'reason': _reason, 'suggested_message': _reason,
                        'lifecycle_segment': lifecycle, 'total_orders': prev_orders,
                        'last_order_at': _last_order_at,
                    })
                elif order_total > 0 and order_total < 15:
                    _reason = (
                        f"Order value ${order_total:.2f} — suggest add-ons (drinks, desserts) or "
                        f"upgrade to a family plan to increase order value."
                    )
                    agent_analysis.append({
                        'contact_name': name,
                        'chosen_action': 'upsell',
                        'chosen_channel': 'sms',
                        'reasoning_snippet': _reason,
                    })
                    airtable_tasks.append({
                        'first_name': first_name, 'last_name': _last_name,
                        'phone': phone or '', 'email': _email,
                        'priority': 'Warm', 'action_type': 'upsell',
                        'reason': _reason, 'suggested_message': _reason,
                        'lifecycle_segment': lifecycle, 'total_orders': prev_orders,
                        'last_order_at': _last_order_at,
                    })
                else:
                    _reason = (
                        f"Regular customer with {prev_orders} prior order(s). Thank {first_name} "
                        f"personally and share this week's new menu highlights."
                    )
                    agent_analysis.append({
                        'contact_name': name,
                        'chosen_action': 'loyalty_nurture',
                        'chosen_channel': 'sms',
                        'reasoning_snippet': _reason,
                    })
                    airtable_tasks.append({
                        'first_name': first_name, 'last_name': _last_name,
                        'phone': phone or '', 'email': _email,
                        'priority': 'Cold', 'action_type': 'loyalty_nurture',
                        'reason': _reason, 'suggested_message': _reason,
                        'lifecycle_segment': lifecycle, 'total_orders': prev_orders,
                        'last_order_at': _last_order_at,
                    })
            else:
                # Create new contact
                name_parts = name.split(None, 1)
                first_n = name_parts[0] if name_parts else ''
                last_n = name_parts[1] if len(name_parts) > 1 else ''
                logger.info("Creating new contact: name='%s' phone='%s'", name, phone)
                cur.execute(
                    "INSERT INTO contacts (phone, first_name, last_name, address, "
                    "lifecycle_segment, primary_source) "
                    "VALUES (%s, %s, %s, %s, 'new_customer', 'Website') RETURNING id",
                    (phone, first_n, last_n, address)
                )
                result = cur.fetchone()
                contact_id = result['id']
                new_contact_count += 1
                logger.info("New contact created id=%s name='%s'", contact_id, name)
                # Add to lookup for subsequent orders
                if phone:
                    phone_lookup[phone] = {'id': contact_id, 'total_orders': 0,
                                            'lifecycle_segment': 'new_customer'}

                # Welcome + subscription pitch for brand-new customers
                first_name = name.split()[0] if name else 'Customer'
                _new_reason = (
                    f"New customer {first_name} — send a warm welcome, confirm delivery "
                    f"satisfaction, and pitch a weekly subscription for ~20% savings."
                )
                agent_analysis.append({
                    'contact_name': name,
                    'chosen_action': 'welcome_and_subscribe',
                    'chosen_channel': 'sms',
                    'reasoning_snippet': _new_reason,
                })
                field_opportunities.append({
                    'contact_name': name,
                    'action': 'new_customer_onboard',
                    'reasoning': (
                        f"First-ever order from {first_name}. High conversion window — reach out "
                        f"within 2 hours to welcome and offer a subscription trial."
                    ),
                })
                airtable_tasks.append({
                    'first_name': first_n, 'last_name': last_n,
                    'phone': phone or '', 'email': '',
                    'priority': 'Warm', 'action_type': 'welcome_and_subscribe',
                    'reason': _new_reason, 'suggested_message': _new_reason,
                    'lifecycle_segment': 'new_customer', 'total_orders': 0,
                    'last_order_at': None,
                })

            # Insert order — resolve dish names against master menu
            items = []
            total_amount = 0
            for row in item_rows:
                raw_dish = _col(row, 'Dish Name', 'Item Name', 'Product', 'dish_name', 'item_name', 'Item').strip()
                qty = int(_col(row, 'Quantity', 'Qty', 'qty', 'quantity') or 1)
                price = float(_col(row, 'Unit Price', 'Price', 'Unit Price (USD)', 'unit_price', 'price') or 0)
                if raw_dish:
                    canonical = resolve_dish_name(raw_dish, alias_map, master_norm, master_set)
                    items.append((canonical, qty, price, qty * price))
                    total_amount += qty * price

            cur.execute(
                "INSERT INTO orders (contact_id, order_id_external, order_date, delivery_date, source, "
                "total_amount, order_type, delivery_slot, customer_name_raw, notes) "
                "VALUES (%s, %s, %s, %s, 'Website', %s, %s, %s, %s, %s) RETURNING id",
                (contact_id, order_num, order_date, delivery_date, total_amount,
                 'SUBSCRIPTION' if is_sub else 'ONE_TIME', delivery_slot, name,
                 notes or None)
            )
            order_row = cur.fetchone()
            order_db_id = order_row['id']
            order_count += 1

            # Insert items with resolved canonical names
            for dish, qty, price, line_total in items:
                if dish in master_set:
                    menu_matched += 1
                else:
                    # New item not in master — create with price from CSV
                    cur.execute(
                        "INSERT INTO menu_items (item_name, avg_price) VALUES (%s, %s) "
                        "ON CONFLICT (item_name) DO NOTHING", (dish, price)
                    )
                    master_set.add(dish)
                    menu_created += 1

                cur.execute(
                    "INSERT INTO order_items (order_id, menu_item_id, item_name, quantity, unit_price, line_total) "
                    "VALUES (%s, (SELECT id FROM menu_items WHERE item_name = %s LIMIT 1), %s, %s, %s, %s)",
                    (order_db_id, dish, dish, qty, price, line_total)
                )
                item_count += 1

            # Fire order_placed event + update contact
            if contact_id:
                meta = json.dumps({
                    'source': 'Website', 'total_amount': total_amount,
                    'order_type': 'SUBSCRIPTION' if is_sub else 'ONE_TIME',
                    'order_id_external': order_num,
                    **(({'notes': notes}) if notes else {})
                })
                cur.execute(
                    "INSERT INTO events (contact_id, event_type, metadata, occurred_at) "
                    "VALUES (%s, 'order_placed', %s::jsonb, %s)",
                    (contact_id, meta, f"{order_date}T12:00:00Z")
                )
                cur.execute(
                    "UPDATE contacts SET total_orders = total_orders + 1, "
                    "last_order_at = %s, updated_at = now() WHERE id = %s",
                    (order_date, contact_id)
                )

    logger.info(
        "CSV processing complete: orders=%d items=%d new_contacts=%d existing=%d opportunities=%d "
        "menu_matched=%d menu_created=%d",
        order_count,
        item_count,
        new_contact_count,
        existing_contact_count,
        opportunity_count,
        menu_matched,
        menu_created,
    )

    # Run lifecycle cycle
    lifecycle_updated = 0
    campaigns_queued = 0
    campaign_moves: list = []
    logger.info("Running lifecycle cycle after order ingestion")
    try:
        cycle_started_at = datetime.utcnow()
        with get_cursor(commit=True) as cur:
            cur.execute("SELECT * FROM run_lifecycle_cycle()")
            row = cur.fetchone()
            lifecycle_updated = row.get('contacts_updated', 0)
            campaigns_queued = row.get('campaigns_queued', 0)
        logger.info(
            "Lifecycle cycle complete: contacts_updated=%d campaigns_queued=%d",
            lifecycle_updated,
            campaigns_queued,
        )
        # Fetch the campaign moves that were just queued by this upload
        with get_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT cq.from_campaign,
                       cq.to_campaign,
                       c.first_name || ' ' || coalesce(c.last_name, '') AS contact_name,
                       c.lifecycle_segment AS segment
                FROM campaign_queue cq
                JOIN contacts c ON c.id = cq.contact_id
                WHERE cq.created_at >= %s AND cq.status = 'pending'
                ORDER BY cq.created_at
                LIMIT 100
                """,
                (cycle_started_at,),
            )
            campaign_moves = [
                {
                    'contact_name': r['contact_name'].strip(),
                    'from_campaign': r['from_campaign'],
                    'to_campaign': r['to_campaign'],
                    'segment': r['segment'],
                }
                for r in cur.fetchall()
            ]
    except Exception as e:
        logger.error(
            "Lifecycle cycle failed after order ingestion: %s — orders still saved",
            e,
            exc_info=True,
        )

    # Push all field agent tasks to Airtable immediately
    airtable_synced = 0
    if airtable_tasks:
        logger.info("Pushing %d tasks to Airtable", len(airtable_tasks))
        _airtable_dead = False
        for task in airtable_tasks:
            if _airtable_dead:
                break
            try:
                create_field_sales_task(task)
                airtable_synced += 1
            except Exception as e:
                err = str(e)
                if '401' in err or 'Unauthorized' in err:
                    logger.warning("Airtable API key invalid (401) — skipping remaining tasks")
                    _airtable_dead = True
                else:
                    logger.warning("Airtable task push failed for %s %s: %s",
                                   task.get('first_name'), task.get('last_name'), e)
        logger.info("Airtable sync complete: %d/%d pushed", airtable_synced, len(airtable_tasks))

    # Exclude duplicate orders from Shipday push and CSV generation
    orders_to_dispatch = {k: v for k, v in orders_grouped.items() if k not in existing_order_nums}

    # Push orders to Shipday only when the caller explicitly opts in
    _do_shipday = push_to_shipday.strip().lower() in ("true", "1", "yes")
    if _do_shipday:
        logger.info("Pushing %d orders to Shipday (opted-in)", len(orders_to_dispatch))
        shipday_result = await _push_orders_to_shipday(
            orders_to_dispatch,
            restaurant_name=os.environ.get("SHIPDAY_RESTAURANT_NAME", "DabbahWala"),
            restaurant_address=os.environ.get("SHIPDAY_RESTAURANT_ADDRESS", ""),
        )
    else:
        logger.info("Shipday push skipped — push_to_shipday not set")
        shipday_result = {}

    # Always generate a Shipday CSV so the user can do a manual import if needed
    shipday_csv_url = ""
    shipday_drive_url = ""
    if orders_to_dispatch:
        try:
            csv_content = _generate_shipday_csv(orders_to_dispatch)
            # Save locally as a download fallback
            _SHIPDAY_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            file_id = str(uuid.uuid4())
            csv_filename = f"shipday_orders_{order_date_str}.csv"
            (_SHIPDAY_EXPORT_DIR / f"{file_id}.csv").write_text(csv_content, encoding="utf-8")
            shipday_csv_url = f"/api/daily-orders/download-shipday-csv/{file_id}"
            # Upload to Google Drive if credentials are configured
            shipday_drive_url = _upload_shipday_csv_to_drive(csv_content, csv_filename)
        except Exception as e:
            logger.warning("Failed to generate Shipday CSV: %s", e)

    return DailyOrderResult(
        date=order_date_str,
        total_orders=order_count,
        rows_skipped=skipped_rows,
        duplicate_orders_skipped=duplicate_orders_skipped,
        total_items=item_count,
        total_revenue=round(sum(
            sum(float(r.get('Unit Price', 0) or 0) * int(r.get('Quantity', 1) or 1)
                for r in item_rows)
            for item_rows in orders_grouped.values()
        ), 2),
        new_contacts=new_contact_count,
        existing_contacts=existing_contact_count,
        opportunities_created=opportunity_count,
        lifecycle_updated=lifecycle_updated,
        campaigns_queued=campaigns_queued,
        menu_items_matched=menu_matched,
        menu_items_created=menu_created,
        agent_analysis=agent_analysis,
        field_opportunities=field_opportunities,
        campaign_moves=campaign_moves,
        airtable_synced=airtable_synced,
        shipday_result=shipday_result,
        shipday_csv_download_url=shipday_csv_url,
        shipday_drive_url=shipday_drive_url,
    )


@router.get("/summary/{date}")
def get_daily_summary(date: str):
    """Get summary of orders for a specific date (YYYY-MM-DD)."""
    with get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT count(*) as total_orders, "
            "coalesce(sum(total_amount), 0) as revenue, "
            "count(DISTINCT contact_id) as unique_customers "
            "FROM orders WHERE order_date = %s", (date,)
        )
        summary = dict(cur.fetchone())

        cur.execute(
            "SELECT oi.item_name, sum(oi.quantity) as qty "
            "FROM order_items oi JOIN orders o ON o.id = oi.order_id "
            "WHERE o.order_date = %s "
            "GROUP BY oi.item_name ORDER BY qty DESC LIMIT 10", (date,)
        )
        top_items = [dict(r) for r in cur.fetchall()]

        return {
            "date": date,
            **summary,
            "top_items": top_items,
        }
