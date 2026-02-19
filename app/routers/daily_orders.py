"""
Daily order processing endpoint.
n8n uploads CSV data -> this endpoint processes orders, creates contacts,
records orders, fires events, and detects opportunities.
"""
import csv
import io
import json
import logging
import re
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from app.db import get_cursor

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
    total_items: int
    total_revenue: float
    new_contacts: int
    existing_contacts: int
    opportunities_created: int
    lifecycle_updated: int
    campaigns_queued: int
    menu_items_matched: int
    menu_items_created: int


@router.post("/process", response_model=DailyOrderResult)
@router.post("/upload-csv", response_model=DailyOrderResult)
async def process_daily_orders(file: UploadFile = File(...)):
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
    order_date_str = ''

    with get_cursor(commit=True) as cur:
        for order_num, item_rows in orders_grouped.items():
            first = item_rows[0]
            phone = normalize_phone(_col(first, 'Customer Phone Number', 'Phone', 'Customer Phone', 'phone'))
            name = _col(first, 'Customer Name', 'Name', 'Customer', 'customer_name').strip()
            address = _col(first, 'Customer Address', 'Address', 'address').strip()
            date_raw = _col(first, 'Date', 'Order Date', 'date', 'order_date').strip()
            plan_name = _col(first, 'Plan Name', 'plan_name', 'Plan').strip()
            delivery_slot = _col(first, 'Delivery Slot Name', 'Delivery Slot', 'delivery_slot').strip()
            order_type = _col(first, 'Order Type', 'order_type', 'Type').strip()

            try:
                order_date = datetime.strptime(date_raw, '%d/%m/%Y').strftime('%Y-%m-%d')
            except Exception:
                order_date = datetime.now().strftime('%Y-%m-%d')
            order_date_str = order_date

            is_sub = 'plan' in plan_name.lower() or 'subscription' in order_type.lower() or 'scheduled' in order_type.lower()

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
                "INSERT INTO orders (contact_id, order_id_external, order_date, source, "
                "total_amount, order_type, delivery_slot, customer_name_raw) "
                "VALUES (%s, %s, %s, 'Website', %s, %s, %s, %s) RETURNING id",
                (contact_id, order_num, order_date, total_amount,
                 'SUBSCRIPTION' if is_sub else 'ONE_TIME', delivery_slot, name)
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
                    'order_id_external': order_num
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
    logger.info("Running lifecycle cycle after order ingestion")
    try:
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
    except Exception as e:
        logger.error(
            "Lifecycle cycle failed after order ingestion: %s — orders still saved",
            e,
            exc_info=True,
        )

    return DailyOrderResult(
        date=order_date_str,
        total_orders=order_count,
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
