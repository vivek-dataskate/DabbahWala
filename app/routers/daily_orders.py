"""
Daily order processing endpoint.
n8n uploads CSV data -> this endpoint processes orders, creates contacts,
records orders, fires events, and detects opportunities.
"""
import csv
import io
import json
import re
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from app.db import get_cursor

router = APIRouter()


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


@router.post("/process", response_model=DailyOrderResult)
async def process_daily_orders(file: UploadFile = File(...)):
    """
    Upload a processing-data-YYYY-MM-DD.csv and process all orders.
    Creates new contacts, records orders + items, fires events, detects opportunities.
    """
    content = await file.read()
    reader = csv.DictReader(io.StringIO(content.decode('utf-8')))
    rows = list(reader)

    if not rows:
        raise HTTPException(status_code=400, detail="Empty CSV file")

    # Group by Order Number
    orders_grouped = defaultdict(list)
    for row in rows:
        order_num = row.get('Order Number', '').strip()
        if order_num:
            orders_grouped[order_num].append(row)

    # Build contact lookup
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

    new_contact_count = 0
    existing_contact_count = 0
    order_count = 0
    item_count = 0
    opportunity_count = 0
    order_date_str = ''

    with get_cursor(commit=True) as cur:
        for order_num, item_rows in orders_grouped.items():
            first = item_rows[0]
            phone = normalize_phone(first.get('Customer Phone Number', ''))
            name = first.get('Customer Name', '').strip()
            address = first.get('Customer Address', '').strip()
            date_raw = first.get('Date', '').strip()
            plan_name = first.get('Plan Name', '').strip()
            delivery_slot = first.get('Delivery Slot Name', '').strip()
            order_type = first.get('Order Type', '').strip()

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
                cur.execute(
                    "INSERT INTO contacts (phone, first_name, last_name, address, "
                    "lifecycle_segment, primary_source) "
                    "VALUES (%s, %s, %s, %s, 'new_customer', 'Website') RETURNING id",
                    (phone, first_n, last_n, address)
                )
                result = cur.fetchone()
                contact_id = result['id']
                new_contact_count += 1
                # Add to lookup for subsequent orders
                if phone:
                    phone_lookup[phone] = {'id': contact_id, 'total_orders': 0,
                                            'lifecycle_segment': 'new_customer'}

            # Insert order
            items = []
            total_amount = 0
            for row in item_rows:
                dish = row.get('Dish Name', '').strip()
                qty = int(row.get('Quantity', 1) or 1)
                price = float(row.get('Unit Price', 0) or 0)
                if dish:
                    items.append((dish, qty, price, qty * price))
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

            # Insert items
            for dish, qty, price, line_total in items:
                # Upsert menu item
                cur.execute(
                    "INSERT INTO menu_items (item_name) VALUES (%s) "
                    "ON CONFLICT (item_name) DO NOTHING", (dish,)
                )
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

    # Run lifecycle cycle
    lifecycle_updated = 0
    campaigns_queued = 0
    try:
        with get_cursor(commit=True) as cur:
            cur.execute("SELECT * FROM run_lifecycle_cycle()")
            row = cur.fetchone()
            lifecycle_updated = row.get('contacts_updated', 0)
            campaigns_queued = row.get('campaigns_queued', 0)
    except Exception:
        pass

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
