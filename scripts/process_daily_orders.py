"""
Daily order processor for DabbahWala.
Reads processing-data-YYYY-MM-DD.csv files and:
  1. Upserts customers (new -> create contact, existing -> update evidence)
  2. Records orders + line items + menu items
  3. Fires order_placed events to trigger lifecycle engine
  4. Detects opportunities (app->direct conversion, subscription upsell, etc.)

Usage:
    python scripts/process_daily_orders.py data/processing-data-2026-02-18.csv
    python scripts/process_daily_orders.py data/processing-data-2026-02-18.csv --execute --via-api

Format expected: processing-data-YYYY-MM-DD.csv with columns:
  Purchase Number, Order Number, Date, SKU, Plan Name, Delivery Slot Name,
  Dish Name, Adds-Ons, Quantity, Allergies & Preferences, Comments,
  Kitchen Instructions, Delivery Instructions, Unit Price, Order Type,
  Customer Name, Customer Phone Number, Customer Address, Geo-Location,
  Pincode, State Name
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher

import pandas as pd

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
RENDER_URL = os.environ.get("DABBAHWALA_API_URL", "https://dabbahwala-latest.onrender.com")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------
def exec_sql(sql: str, read=False):
    """Execute SQL via API using JSON body."""
    endpoint = "admin/query" if read else "admin/exec"
    url = f"{RENDER_URL}/{endpoint}"
    payload = json.dumps({"secret": ADMIN_SECRET, "sql": sql}).encode('utf-8')
    req = urllib.request.Request(url, data=payload, method='POST',
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def esc(val: str) -> str:
    """Escape single quotes for SQL."""
    return val.replace("'", "''") if val else ''


# ---------------------------------------------------------------------------
# Name matching helpers (for fuzzy matching app customers)
# ---------------------------------------------------------------------------
def normalize_name(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r'[^\w\s]', '', name)
    name = re.sub(r'\s+', ' ', name)
    return name


def normalize_phone(phone) -> str:
    """Extract last 10 digits from phone."""
    if pd.isna(phone):
        return ''
    phone_str = str(phone).strip()
    digits = re.sub(r'\D', '', phone_str)
    return digits[-10:] if len(digits) >= 10 else ''


# ---------------------------------------------------------------------------
# Parse daily CSV
# ---------------------------------------------------------------------------
def parse_daily_csv(filepath: str) -> tuple:
    """
    Parse a processing-data CSV into grouped orders.
    Returns: list of order dicts, each with 'items' list.
    """
    df = pd.read_csv(filepath)

    # Group by Order Number
    orders_grouped = defaultdict(list)
    for _, row in df.iterrows():
        order_num = str(row.get('Order Number', '')).strip()
        if not order_num:
            continue
        orders_grouped[order_num].append(row)

    orders = []
    for order_num, rows in orders_grouped.items():
        first = rows[0]
        phone = normalize_phone(first.get('Customer Phone Number'))
        name = str(first.get('Customer Name', '')).strip()
        address = str(first.get('Customer Address', '')).strip() if pd.notna(first.get('Customer Address')) else ''
        date_str = str(first.get('Date', '')).strip()
        plan_name = str(first.get('Plan Name', '')).strip() if pd.notna(first.get('Plan Name')) else ''
        delivery_slot = str(first.get('Delivery Slot Name', '')).strip() if pd.notna(first.get('Delivery Slot Name')) else ''
        order_type = str(first.get('Order Type', '')).strip() if pd.notna(first.get('Order Type')) else ''
        purchase_num = str(first.get('Purchase Number', '')).strip() if pd.notna(first.get('Purchase Number')) else ''
        geo = str(first.get('Geo-Location', '')).strip() if pd.notna(first.get('Geo-Location')) else ''
        pincode = str(first.get('Pincode', '')).strip() if pd.notna(first.get('Pincode')) else ''
        state = str(first.get('State Name', '')).strip() if pd.notna(first.get('State Name')) else ''

        # Parse date (DD/MM/YYYY format)
        try:
            order_date = datetime.strptime(date_str, '%d/%m/%Y').strftime('%Y-%m-%d')
        except Exception:
            order_date = datetime.now().strftime('%Y-%m-%d')

        # Determine if subscription based on Plan Name or Order Type
        is_subscription = False
        if 'plan' in plan_name.lower() or 'subscription' in order_type.lower() or 'scheduled' in order_type.lower():
            is_subscription = True

        items = []
        total_amount = 0
        for row in rows:
            dish = str(row.get('Dish Name', '')).strip() if pd.notna(row.get('Dish Name')) else ''
            addons = str(row.get('Adds-Ons', '')).strip() if pd.notna(row.get('Adds-Ons')) else ''
            qty = int(row.get('Quantity', 1)) if pd.notna(row.get('Quantity')) else 1
            price = float(row.get('Unit Price', 0)) if pd.notna(row.get('Unit Price')) else 0

            if dish:
                items.append({
                    'dish_name': dish,
                    'addons': addons,
                    'quantity': qty,
                    'unit_price': price,
                    'line_total': qty * price,
                    'sku': str(row.get('SKU', '')).strip() if pd.notna(row.get('SKU')) else '',
                })
                total_amount += qty * price

        orders.append({
            'order_number': order_num,
            'purchase_number': purchase_num,
            'order_date': order_date,
            'customer_name': name,
            'phone': phone,
            'address': address,
            'plan_name': plan_name,
            'delivery_slot': delivery_slot,
            'order_type': 'SUBSCRIPTION' if is_subscription else 'ONE_TIME',
            'total_amount': total_amount,
            'geo_location': geo,
            'pincode': pincode,
            'state': state,
            'items': items,
            'source': 'Website',
        })

    return orders


# ---------------------------------------------------------------------------
# Main processing logic
# ---------------------------------------------------------------------------
def process_orders(orders: list, execute: bool = False):
    """Process parsed orders: upsert contacts, record orders, detect opportunities."""

    print(f"\n[1/5] Fetching existing contacts from DB...")
    # Build phone -> contact lookup
    result = exec_sql("SELECT id, email, phone, first_name, last_name, total_orders, "
                       "last_order_at, lifecycle_segment, primary_source, subscription_type "
                       "FROM dabbahwala.contacts WHERE phone IS NOT NULL AND phone != ''", read=True)
    phone_to_contact = {}
    if result.get('rows'):
        for r in result['rows']:
            phone = normalize_phone(r.get('phone', ''))
            if phone:
                phone_to_contact[phone] = r
    print(f"  {len(phone_to_contact)} existing contacts by phone")

    # Also build name lookup for fuzzy matching
    result2 = exec_sql("SELECT id, email, phone, first_name, last_name FROM dabbahwala.contacts "
                         "WHERE first_name IS NOT NULL", read=True)
    name_to_contact = {}
    if result2.get('rows'):
        for r in result2['rows']:
            full = f"{r.get('first_name', '')} {r.get('last_name', '')}".strip().lower()
            if full:
                name_to_contact[full] = r

    # Stats
    new_contacts = []
    existing_contacts = []
    orders_to_insert = []
    items_to_insert = []
    menu_items_new = set()
    opportunities = []

    print(f"\n[2/5] Matching {len(orders)} orders to contacts...")

    for order in orders:
        phone = order['phone']
        name = order['customer_name']
        name_norm = normalize_name(name)

        contact = None
        match_type = None

        # 1. Exact phone match
        if phone and phone in phone_to_contact:
            contact = phone_to_contact[phone]
            match_type = 'phone'
        # 2. Exact name match
        elif name_norm in name_to_contact:
            contact = name_to_contact[name_norm]
            match_type = 'name'
        # 3. Fuzzy name match
        else:
            best_score = 0
            best_contact = None
            for full_name, c in name_to_contact.items():
                if not name_norm or not full_name:
                    continue
                # First letter must match
                if name_norm[0] != full_name[0]:
                    continue
                score = SequenceMatcher(None, name_norm, full_name).ratio()
                if score > best_score and score > 0.80:
                    best_score = score
                    best_contact = c
            if best_contact:
                contact = best_contact
                match_type = 'fuzzy'

        if contact:
            existing_contacts.append({
                'contact_id': contact['id'],
                'name': name,
                'phone': phone,
                'match_type': match_type,
                'prev_orders': contact.get('total_orders', 0),
                'lifecycle': contact.get('lifecycle_segment', 'cold'),
                'primary_source': contact.get('primary_source'),
            })
            contact_id = contact['id']
        else:
            new_contacts.append({
                'name': name,
                'phone': phone,
                'address': order['address'],
            })
            contact_id = None  # will be created

        orders_to_insert.append({
            **order,
            'contact_id': contact_id,
            'is_new_contact': contact is None,
        })

    unique_new = {c['phone']: c for c in new_contacts if c['phone']}
    print(f"  Existing: {len(existing_contacts)}, New: {len(unique_new)}")

    # Collect all menu items
    for order in orders:
        for item in order['items']:
            menu_items_new.add(item['dish_name'])
    print(f"  Menu items in today's orders: {len(menu_items_new)}")

    # Detect opportunities
    print(f"\n[3/5] Detecting opportunities...")

    for ec in existing_contacts:
        # Opportunity: lapsed customer returned
        if ec['lifecycle'] in ('lapsed_customer', 'reactivation_candidate'):
            opportunities.append({
                'contact_id': ec['contact_id'],
                'action': 'send_sms',
                'priority': 'hot',
                'reason': f"Lapsed customer '{ec['name']}' placed a new order! Welcome them back.",
                'suggested_message': f"Welcome back {ec['name'].split()[0]}! We're thrilled to cook for you again. Enjoy your meal!",
            })

        # Opportunity: first-time customer -> subscription pitch
        if ec['prev_orders'] == 0:
            opportunities.append({
                'contact_id': ec['contact_id'],
                'action': 'send_sms',
                'priority': 'warm',
                'reason': f"First order from '{ec['name']}'. Potential subscription conversion.",
                'suggested_message': f"Hi {ec['name'].split()[0]}, hope you loved today's meal! Save with a weekly subscription - fresh food delivered daily. Reply SUBSCRIBE for details.",
            })

        # Opportunity: app customer now ordering direct
        if ec.get('primary_source') == 'Food Delivery Apps':
            opportunities.append({
                'contact_id': ec['contact_id'],
                'action': 'send_sms',
                'priority': 'hot',
                'reason': f"App customer '{ec['name']}' is now ordering direct! Reinforce the behavior.",
                'suggested_message': f"Thanks for ordering direct, {ec['name'].split()[0]}! You save on fees and get priority delivery. We appreciate you!",
            })

    print(f"  Opportunities detected: {len(opportunities)}")
    for opp in opportunities[:10]:
        print(f"    [{opp['priority']}] {opp['reason'][:80]}...")

    # Summary
    print(f"\n{'='*60}")
    print(f"DAILY ORDER SUMMARY — {orders[0]['order_date'] if orders else 'N/A'}")
    print(f"{'='*60}")
    print(f"Total orders:       {len(orders)}")
    print(f"Total items:        {sum(len(o['items']) for o in orders)}")
    print(f"Total revenue:      ${sum(o['total_amount'] for o in orders):,.2f}")
    print(f"Existing customers: {len(existing_contacts)}")
    print(f"New customers:      {len(unique_new)}")
    print(f"Opportunities:      {len(opportunities)}")

    # Subscription breakdown
    subs = [o for o in orders if o['order_type'] == 'SUBSCRIPTION']
    onetime = [o for o in orders if o['order_type'] == 'ONE_TIME']
    print(f"Subscriptions:      {len(subs)} orders (${sum(o['total_amount'] for o in subs):,.2f})")
    print(f"One-time:           {len(onetime)} orders (${sum(o['total_amount'] for o in onetime):,.2f})")

    if not execute:
        print(f"\n*** DRY RUN — pass --execute to insert into DB ***")
        return

    # Execute inserts
    print(f"\n[4/5] Inserting into database...")

    # Insert new menu items
    for dish in menu_items_new:
        sql = (f"INSERT INTO dabbahwala.menu_items (item_name) "
               f"VALUES ('{esc(dish)}') ON CONFLICT (item_name) DO NOTHING")
        exec_sql(sql)

    # Insert new contacts
    new_contact_ids = {}
    for phone, nc in unique_new.items():
        name_parts = nc['name'].split(None, 1)
        first = esc(name_parts[0]) if name_parts else ''
        last = esc(name_parts[1]) if len(name_parts) > 1 else ''
        addr = esc(nc['address'])
        sql = (f"INSERT INTO dabbahwala.contacts "
               f"(phone, first_name, last_name, address, lifecycle_segment, primary_source) "
               f"VALUES ('{phone}', '{first}', '{last}', '{addr}', 'new_customer', 'Website') "
               f"RETURNING id")
        result = exec_sql(sql, read=True)
        if result.get('rows'):
            new_contact_ids[phone] = result['rows'][0]['id']

    print(f"  Created {len(new_contact_ids)} new contacts")

    # Insert orders + items
    order_count = 0
    item_count = 0
    for order in orders_to_insert:
        cid = order['contact_id']
        if cid is None and order['phone'] in new_contact_ids:
            cid = new_contact_ids[order['phone']]

        cid_val = str(cid) if cid else 'NULL'
        meta_dict = {"plan": order["plan_name"], "purchase": order["purchase_number"],
                     "geo": order["geo_location"], "pincode": order["pincode"], "state": order["state"]}
        meta_json = esc(json.dumps(meta_dict))
        sql = (
            f"INSERT INTO dabbahwala.orders "
            f"(contact_id, order_id_external, order_date, source, total_amount, "
            f"order_type, delivery_slot, customer_name_raw, metadata) "
            f"VALUES ({cid_val}, '{esc(order['order_number'])}', '{order['order_date']}', "
            f"'Website', {order['total_amount']}, '{esc(order['order_type'])}', "
            f"'{esc(order['delivery_slot'])}', '{esc(order['customer_name'])}', "
            f"'{meta_json}') "
            f"RETURNING id"
        )
        result = exec_sql(sql, read=True)
        order_db_id = None
        if result.get('rows'):
            order_db_id = result['rows'][0]['id']
            order_count += 1

        if order_db_id:
            for item in order['items']:
                isql = (
                    f"INSERT INTO dabbahwala.order_items "
                    f"(order_id, menu_item_id, item_name, quantity, unit_price, line_total) "
                    f"VALUES ({order_db_id}, "
                    f"(SELECT id FROM dabbahwala.menu_items WHERE item_name = '{esc(item['dish_name'])}' LIMIT 1), "
                    f"'{esc(item['dish_name'])}', {item['quantity']}, {item['unit_price']}, {item['line_total']})"
                )
                r = exec_sql(isql)
                if 'error' not in r:
                    item_count += 1

        # Fire order_placed event
        if cid:
            email_result = exec_sql(f"SELECT email FROM dabbahwala.contacts WHERE id = {cid}", read=True)
            email = None
            if email_result.get('rows') and email_result['rows'][0].get('email'):
                email = email_result['rows'][0]['email']
            if email:
                meta = json.dumps({
                    'source': 'Website', 'total_amount': order['total_amount'],
                    'order_type': order['order_type'], 'order_id_external': order['order_number']
                }).replace("'", "''")
                exec_sql(
                    f"INSERT INTO dabbahwala.events (contact_id, event_type, metadata, occurred_at) "
                    f"VALUES ({cid}, 'order_placed', '{meta}', '{order['order_date']}T12:00:00Z')"
                )
            # Update contact order stats
            exec_sql(
                f"UPDATE dabbahwala.contacts SET "
                f"total_orders = total_orders + 1, "
                f"last_order_at = '{order['order_date']}', "
                f"updated_at = now() "
                f"WHERE id = {cid}"
            )

    print(f"  Orders: {order_count}, Items: {item_count}")

    # Insert opportunities
    opp_count = 0
    for opp in opportunities:
        sql = (
            f"SELECT dabbahwala.create_opportunity("
            f"{opp['contact_id']}, '{opp['action']}'::dabbahwala.opportunity_action, "
            f"'{opp['priority']}', '{esc(opp['reason'])}', '{esc(opp['suggested_message'])}', 0.85)"
        )
        r = exec_sql(sql, read=True)
        if r.get('rows'):
            opp_count += 1
    print(f"  Opportunities created: {opp_count}")

    # Run lifecycle cycle
    print(f"\n[5/5] Running lifecycle engine...")
    r = exec_sql("SELECT * FROM dabbahwala.run_lifecycle_cycle()", read=True)
    if r.get('rows'):
        row = r['rows'][0]
        print(f"  Contacts updated: {row.get('contacts_updated', '?')}")
        print(f"  Campaigns queued: {row.get('campaigns_queued', '?')}")

    print(f"\nDone! Daily processing complete.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description='Process daily DabbahWala orders')
    parser.add_argument('file', help='Path to processing-data CSV file')
    parser.add_argument('--execute', action='store_true', help='Actually insert data')
    parser.add_argument('--via-api', action='store_true', help='Use Render API (default)')
    args = parser.parse_args()

    print("=" * 60)
    print("DabbahWala Daily Order Processor")
    print("=" * 60)

    if not os.path.exists(args.file):
        print(f"ERROR: File not found: {args.file}")
        sys.exit(1)

    print(f"File: {args.file}")
    orders = parse_daily_csv(args.file)
    print(f"Parsed {len(orders)} orders with {sum(len(o['items']) for o in orders)} items")

    process_orders(orders, execute=args.execute)


if __name__ == '__main__':
    main()
