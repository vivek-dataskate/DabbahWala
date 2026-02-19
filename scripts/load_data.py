"""
Load customer and order data from Excel files into Supabase.
Includes fuzzy matching to merge food delivery app customers with direct customers.

Usage:
    python scripts/load_data.py                    # dry run (preview only)
    python scripts/load_data.py --execute          # actually insert into DB
    python scripts/load_data.py --execute --via-api # insert via Render admin API
"""
import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from difflib import SequenceMatcher

import pandas as pd

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
RENDER_URL = os.environ.get("DABBAHWALA_API_URL", "https://dabbahwala-latest.onrender.com")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")
CUSTOMER_FILE = "data/DW Costumers-latest.xlsx"
ORDER_FILE = "data/DabbahWala OrderData-2025.xlsx"
FUZZY_THRESHOLD = 0.72  # similarity threshold for name matching

# ---------------------------------------------------------------------------
# Category classification for menu items
# ---------------------------------------------------------------------------
CATEGORY_RULES = [
    (r'thali', 'thali'),
    (r'biryani|pulav', 'biryani'),
    (r'curry|masala|paneer|chicken|goat|shrimp|butter|palak|saag|aloo gobi|mutter|dal\b', 'curry'),
    (r'roti|naan|poori|rumali', 'roti'),
    (r'rice\b|curd|yogurt|raita|pickle|chutney', 'sides'),
    (r'lassi|chai|tea|coffee|beverage|juice', 'beverages'),
    (r'dessert|gulab|halwa|kheer|sweet|laddu|jalebi', 'dessert'),
    (r'combo|dabbah', 'combo'),
    (r'idly|idli|sambar|dosa|vada|pungulu|upma|pesarattu', 'breakfast'),
    (r'kabob|kebab|tikka(?!\s*masala)|tandoori', 'appetizer'),
    (r'burger|sandwich|wrap', 'snack'),
]

VEG_KEYWORDS = ['veg', 'paneer', 'aloo', 'palak', 'saag', 'mutter', 'dal', 'idly', 'dosa',
                 'curd', 'yogurt', 'raita', 'rice', 'roti', 'lassi', 'poori', 'sambar',
                 'pungulu', 'upma', 'pesarattu', 'gobi']
NON_VEG_KEYWORDS = ['non-veg', 'non veg', 'nonveg', 'chicken', 'goat', 'shrimp', 'mutton', 'egg',
                     'fish', 'lamb', 'meat', 'tandoori chicken', 'butter chicken',
                     'chicken tikka', 'chicken malai', 'chicken curry', 'chicken dum']


def classify_item(name: str) -> tuple:
    """Return (category, is_veg) for a menu item name."""
    lower = name.lower()

    # Category
    category = 'other'
    for pattern, cat in CATEGORY_RULES:
        if re.search(pattern, lower):
            category = cat
            break

    # Veg/Non-veg
    is_veg = None
    if any(kw in lower for kw in NON_VEG_KEYWORDS):
        is_veg = False
    elif any(kw in lower for kw in VEG_KEYWORDS):
        is_veg = True

    return category, is_veg


# ---------------------------------------------------------------------------
# Fuzzy matching
# ---------------------------------------------------------------------------
def normalize_name(name: str) -> str:
    """Normalize a name for comparison."""
    name = name.strip().lower()
    name = re.sub(r'[^\w\s]', '', name)  # remove punctuation
    name = re.sub(r'\s+', ' ', name)
    return name


def extract_first_name(name: str) -> str:
    """Extract first name/word from a full name."""
    parts = name.strip().split()
    return parts[0].lower() if parts else ''


def fuzzy_match_name(app_name: str, customer_names: dict) -> tuple:
    """
    Try to match an abbreviated app name (e.g., 'Dilpreet S.') to a full customer name.
    Returns (matched_email, confidence) or (None, 0).

    Strategy:
    1. Extract first name from app name
    2. Find all customers whose first name matches exactly
    3. If exactly one match -> high confidence
    4. If multiple -> use last initial to disambiguate
    """
    norm = normalize_name(app_name)
    parts = norm.split()
    if not parts:
        return None, 0

    first = parts[0]
    last_initial = ''
    if len(parts) > 1:
        last_initial = parts[-1].rstrip('.')[0] if parts[-1].rstrip('.') else ''

    candidates = []
    for full_name, email in customer_names.items():
        if not email or '@' not in email:  # skip customers with no valid email
            continue
        fn_parts = full_name.split()
        if not fn_parts:
            continue
        cust_first = fn_parts[0]

        # First letter MUST match (avoids manish/anish type errors)
        if not cust_first or not first or cust_first[0] != first[0]:
            continue

        # First name must match closely
        if cust_first == first or SequenceMatcher(None, cust_first, first).ratio() > 0.88:
            # Check last initial if available
            if last_initial and len(fn_parts) > 1:
                cust_last_initial = fn_parts[-1][0] if fn_parts[-1] else ''
                if cust_last_initial == last_initial:
                    candidates.append((full_name, email, 0.95))
                else:
                    candidates.append((full_name, email, 0.60))
            else:
                candidates.append((full_name, email, 0.70))

    if not candidates:
        return None, 0

    # Sort by confidence
    candidates.sort(key=lambda x: -x[2])
    best = candidates[0]

    # Only return if above threshold
    if best[2] >= FUZZY_THRESHOLD:
        return best[1], best[2]

    return None, 0


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_customers(filepath: str) -> pd.DataFrame:
    """Load and normalize customer master."""
    df = pd.read_excel(filepath, sheet_name="Sheet5")
    df['email_norm'] = df['Email'].fillna('').astype(str).str.strip().str.lower()
    df['phone_norm'] = df['Phone Number'].fillna('').astype(str).apply(
        lambda x: re.sub(r'\D', '', x)[-10:] if len(re.sub(r'\D', '', x)) >= 10 else ''
    )
    df['name_norm'] = df['Customer Name'].fillna('').astype(str).str.strip().str.lower()
    return df


def load_orders(filepath: str) -> tuple:
    """Load all order sheets, return (order_summaries, app_item_rows)."""
    xls = pd.ExcelFile(filepath)
    summaries = []
    items = []

    for sheet in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet)
        df.columns = ['Source'] + list(df.columns[1:])
        # Normalize email column
        for c in df.columns:
            if c.lower().startswith('email'):
                df.rename(columns={c: 'Email address'}, inplace=True)
        # Skip sub-header row
        df = df.iloc[1:].reset_index(drop=True)

        for _, row in df.iterrows():
            cname = row.get('Customer name')
            if pd.isna(cname) or not str(cname).strip():
                continue

            source = str(row.get('Source', '')).strip() if pd.notna(row.get('Source')) else 'Website'
            item_ordered = row.get('Item Ordered')
            is_app = source == 'Food Delivery Apps'

            # Parse order_id and app_platform
            order_id_raw = str(row.get('OrderID', '')).strip() if pd.notna(row.get('OrderID')) else ''
            app_platform = None
            if is_app:
                for platform in ['DoorDash', 'Uber Eats', 'Grubhub']:
                    if platform.lower() in order_id_raw.lower():
                        app_platform = platform
                        break
                if not app_platform:
                    app_platform = 'Unknown'

            # Parse phone
            phone_raw = row.get('Phone number')
            phone = ''
            if pd.notna(phone_raw):
                phone_str = str(int(phone_raw)) if isinstance(phone_raw, float) else str(phone_raw)
                phone = re.sub(r'\D', '', phone_str)
                if len(phone) >= 10:
                    phone = phone[-10:]
                elif '#ERROR' in str(phone_raw):
                    phone = ''

            email = str(row.get('Email address', '')).strip().lower() if pd.notna(row.get('Email address')) else ''
            address = str(row.get('Physical address', '')).strip() if pd.notna(row.get('Physical address')) else ''
            order_date = row.get('Order Date')
            total = float(row.get('Total Amount', 0)) if pd.notna(row.get('Total Amount')) else 0
            order_type = str(row.get('onetime /subscription', '')).strip() if pd.notna(row.get('onetime /subscription')) else ''
            delivery_slot = str(row.get('Delivery Slot', '')).strip() if pd.notna(row.get('Delivery Slot')) else ''

            if is_app and pd.notna(item_ordered) and str(item_ordered).strip():
                # App orders: each row is an item line
                qty = row.get('Unnamed: 8', 1)
                qty = int(qty) if pd.notna(qty) and str(qty) != 'Quantity' else 1
                price = row.get('Unnamed: 9', 0)
                price = float(price) if pd.notna(price) and str(price) != 'Price' else 0

                items.append({
                    'order_id_external': order_id_raw,
                    'order_date': order_date,
                    'customer_name': str(cname).strip(),
                    'source': source,
                    'app_platform': app_platform,
                    'item_name': str(item_ordered).strip(),
                    'quantity': qty,
                    'unit_price': price,
                    'line_total': total,
                    'email': email,
                    'phone': phone,
                    'address': address,
                })
            else:
                # Website orders: summary row
                summaries.append({
                    'order_id_external': order_id_raw,
                    'order_date': order_date,
                    'customer_name': str(cname).strip(),
                    'source': source or 'Website',
                    'app_platform': app_platform,
                    'email': email,
                    'phone': phone,
                    'address': address,
                    'total_amount': total,
                    'order_type': order_type,
                    'delivery_slot': delivery_slot,
                })

    return summaries, items


# ---------------------------------------------------------------------------
# SQL execution helpers
# ---------------------------------------------------------------------------
def exec_sql_api(sql: str, read=False):
    """Execute SQL via Render admin API using JSON body."""
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


def exec_sql_batch_api(statements: list, label: str = ""):
    """Execute multiple SQL statements via API, one at a time."""
    success = 0
    errors = 0
    for i, sql in enumerate(statements):
        if i % 100 == 0 and label:
            print(f"  {label}: {i}/{len(statements)}...")
        result = exec_sql_api(sql, read=False)
        if 'error' in result:
            errors += 1
            if errors <= 3:
                print(f"  ERROR: {result['error'][:200]}")
        else:
            success += 1
    print(f"  {label}: {success} ok, {errors} errors out of {len(statements)}")
    return success, errors


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true', help='Actually insert data')
    parser.add_argument('--via-api', action='store_true', help='Use Render API instead of direct DB')
    args = parser.parse_args()

    print("=" * 70)
    print("DabbahWala Data Loader")
    print("=" * 70)

    # 1. Load customer master
    print("\n[1/6] Loading customer master...")
    customers = load_customers(CUSTOMER_FILE)
    print(f"  {len(customers)} customers loaded")

    # Build lookup maps
    email_to_cust = {}
    phone_to_cust = {}
    name_to_email = {}  # normalized name -> email (for fuzzy matching)

    for _, row in customers.iterrows():
        email = row['email_norm']
        phone = row['phone_norm']
        name = row['name_norm']
        cust_name = str(row['Customer Name']).strip()
        address = str(row['Address']).strip() if pd.notna(row['Address']) else ''

        if email:
            email_to_cust[email] = {
                'name': cust_name, 'email': email, 'phone': phone, 'address': address
            }
        if phone:
            phone_to_cust[phone] = email
        if name and email:
            name_to_email[name] = email

    print(f"  {len(email_to_cust)} by email, {len(phone_to_cust)} by phone, {len(name_to_email)} by name")

    # 2. Load orders
    print("\n[2/6] Loading order data...")
    web_orders, app_items = load_orders(ORDER_FILE)
    print(f"  {len(web_orders)} website orders, {len(app_items)} app item rows")

    # Group app items into orders
    app_orders_grouped = defaultdict(list)
    for item in app_items:
        key = (item['order_id_external'], item['order_date'], item['customer_name'])
        app_orders_grouped[key].append(item)

    print(f"  {len(app_orders_grouped)} unique app orders (grouped)")

    # 3. Match & merge
    print("\n[3/6] Matching orders to customers...")

    # Track which contacts we'll create
    contacts_by_email = {}  # email -> contact dict
    orders_to_insert = []
    items_to_insert = []
    menu_items_set = {}  # item_name -> {prices, category, is_veg}

    # -- Process WEBSITE orders --
    web_matched = 0
    web_new = 0
    for order in web_orders:
        email = order['email']
        phone = order['phone']
        name = order['customer_name']

        # Try to find in customer master
        contact_email = None
        if email and email in email_to_cust:
            contact_email = email
        elif phone and phone in phone_to_cust:
            contact_email = phone_to_cust[phone]
        elif normalize_name(name) in name_to_email:
            contact_email = name_to_email[normalize_name(name)]

        if contact_email and contact_email in email_to_cust:
            web_matched += 1
            cust = email_to_cust[contact_email]
            # Update address if we have one from order
            if order['address'] and not cust.get('address'):
                cust['address'] = order['address']
        else:
            # Create new contact from order data
            web_new += 1
            if email:
                contact_email = email
            else:
                contact_email = f"no-email-{normalize_name(name).replace(' ', '-')}@placeholder.local"

            if contact_email not in email_to_cust:
                name_parts = name.split(None, 1)
                email_to_cust[contact_email] = {
                    'name': name,
                    'email': contact_email,
                    'phone': phone,
                    'address': order['address'],
                }

        # Track subscription type
        cust = email_to_cust.get(contact_email, {})
        existing_type = cust.get('subscription_type', '')
        new_type = order.get('order_type', '')
        if existing_type and new_type and existing_type != new_type:
            cust['subscription_type'] = 'BOTH'
        elif new_type:
            cust['subscription_type'] = new_type
        cust['primary_source'] = cust.get('primary_source', 'Website')

        orders_to_insert.append({
            'contact_email': contact_email,
            'order_id_external': order['order_id_external'],
            'order_date': order['order_date'],
            'source': 'Website',
            'app_platform': None,
            'total_amount': order['total_amount'],
            'order_type': order['order_type'],
            'delivery_slot': order['delivery_slot'],
            'customer_name_raw': name,
            'items': [],
        })

    print(f"  Website: {web_matched} matched, {web_new} new contacts")

    # -- Process APP orders with fuzzy matching --
    app_matched_exact = 0
    app_matched_fuzzy = 0
    app_new = 0
    fuzzy_merges = []

    for (oid, odate, cname), item_list in app_orders_grouped.items():
        first_item = item_list[0]
        email = first_item['email']
        phone = first_item['phone']

        # Try exact match first
        contact_email = None
        if email and email in email_to_cust:
            contact_email = email
            app_matched_exact += 1
        elif phone and phone in phone_to_cust:
            contact_email = phone_to_cust[phone]
            app_matched_exact += 1
        elif normalize_name(cname) in name_to_email:
            contact_email = name_to_email[normalize_name(cname)]
            app_matched_exact += 1
        else:
            # Fuzzy match
            matched_email, confidence = fuzzy_match_name(cname, name_to_email)
            if matched_email:
                contact_email = matched_email
                app_matched_fuzzy += 1
                fuzzy_merges.append({
                    'app_name': cname,
                    'matched_to': email_to_cust.get(matched_email, {}).get('name', ''),
                    'email': matched_email,
                    'confidence': confidence,
                })
            else:
                app_new += 1
                contact_email = f"app-{normalize_name(cname).replace(' ', '-')}@app.placeholder.local"
                if contact_email not in email_to_cust:
                    email_to_cust[contact_email] = {
                        'name': cname,
                        'email': contact_email,
                        'phone': phone,
                        'address': first_item.get('address', ''),
                    }

        # Mark as app source
        cust = email_to_cust.get(contact_email, {})
        existing_source = cust.get('primary_source', '')
        if existing_source == 'Website':
            cust['primary_source'] = 'BOTH'
        elif not existing_source:
            cust['primary_source'] = 'Food Delivery Apps'

        total_amount = sum(i['line_total'] for i in item_list)
        order_items = []
        for item in item_list:
            iname = item['item_name']
            if iname not in menu_items_set:
                cat, is_veg = classify_item(iname)
                menu_items_set[iname] = {'prices': [], 'category': cat, 'is_veg': is_veg}
            menu_items_set[iname]['prices'].append(item['unit_price'])
            order_items.append({
                'item_name': iname,
                'quantity': item['quantity'],
                'unit_price': item['unit_price'],
                'line_total': item['line_total'],
            })

        orders_to_insert.append({
            'contact_email': contact_email,
            'order_id_external': oid,
            'order_date': odate,
            'source': 'Food Delivery Apps',
            'app_platform': first_item.get('app_platform'),
            'total_amount': total_amount,
            'order_type': '',
            'delivery_slot': '',
            'customer_name_raw': cname,
            'items': order_items,
        })

    print(f"  App orders: {app_matched_exact} exact match, {app_matched_fuzzy} fuzzy matched, {app_new} new")
    print(f"  Fuzzy merges (app -> website customer):")
    for m in fuzzy_merges[:20]:
        print(f"    '{m['app_name']}' -> '{m['matched_to']}' ({m['email']}) conf={m['confidence']:.2f}")
    if len(fuzzy_merges) > 20:
        print(f"    ... and {len(fuzzy_merges) - 20} more")

    # 4. Compute contact stats
    print("\n[4/6] Computing contact stats...")
    contact_order_counts = defaultdict(int)
    contact_last_order = {}
    for order in orders_to_insert:
        ce = order['contact_email']
        contact_order_counts[ce] += 1
        od = order['order_date']
        if pd.notna(od):
            if ce not in contact_last_order or od > contact_last_order[ce]:
                contact_last_order[ce] = od

    total_contacts = len(email_to_cust)
    with_orders = len(contact_order_counts)
    print(f"  {total_contacts} total contacts, {with_orders} with orders")
    print(f"  {len(menu_items_set)} unique menu items")
    print(f"  {len(orders_to_insert)} total orders to insert")

    # Compute lifecycle segments
    from datetime import datetime, timedelta
    now = datetime.now()
    for email, cust in email_to_cust.items():
        order_count = contact_order_counts.get(email, 0)
        last_order = contact_last_order.get(email)

        if order_count == 0:
            cust['lifecycle'] = 'cold'
        elif order_count == 1 and last_order and (now - pd.Timestamp(last_order).to_pydatetime().replace(tzinfo=None)).days <= 30:
            cust['lifecycle'] = 'new_customer'
        elif order_count >= 3:
            cust['lifecycle'] = 'active_customer'
        elif last_order and (now - pd.Timestamp(last_order).to_pydatetime().replace(tzinfo=None)).days > 60:
            cust['lifecycle'] = 'lapsed_customer'
        elif order_count >= 1:
            cust['lifecycle'] = 'engaged'
        else:
            cust['lifecycle'] = 'cold'

        cust['total_orders'] = order_count
        cust['last_order_at'] = last_order

    # Lifecycle distribution
    lifecycle_dist = defaultdict(int)
    for cust in email_to_cust.values():
        lifecycle_dist[cust.get('lifecycle', 'cold')] += 1
    print(f"  Lifecycle distribution:")
    for seg, cnt in sorted(lifecycle_dist.items(), key=lambda x: -x[1]):
        print(f"    {seg}: {cnt}")

    # 5. Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Contacts to load:     {total_contacts}")
    print(f"Menu items to load:   {len(menu_items_set)}")
    print(f"Orders to load:       {len(orders_to_insert)}")
    print(f"Order items to load:  {sum(len(o['items']) for o in orders_to_insert)}")
    print(f"Fuzzy merges:         {len(fuzzy_merges)}")

    if not args.execute:
        print("\n*** DRY RUN — pass --execute to actually insert data ***")
        return

    # 6. Execute inserts
    print("\n[5/6] Inserting data...")

    if not args.via_api:
        print("ERROR: Direct DB not available in this environment. Use --via-api")
        return

    # Run migration first
    print("  Running migration 024...")
    result = exec_sql_api("SELECT 1", read=True)
    if 'error' in result:
        print(f"  DB connection failed: {result['error']}")
        return

    # Read and execute migration
    with open("migrations/024_menu_orders.sql") as f:
        migration_sql = f.read()
    # Split by semicolons and execute each statement
    for stmt in migration_sql.split(';'):
        stmt = stmt.strip()
        if stmt and not stmt.startswith('--'):
            r = exec_sql_api(stmt, read=False)
            if 'error' in r and 'already exists' not in str(r.get('error', '')):
                print(f"  Migration warning: {r}")

    print("  Migration 024 done.")

    # Insert menu items
    print("  Inserting menu items...")
    menu_stmts = []
    for item_name, info in menu_items_set.items():
        avg_p = sum(info['prices']) / len(info['prices']) if info['prices'] else 0
        cat = info['category']
        is_veg = 'true' if info['is_veg'] else ('false' if info['is_veg'] is False else 'NULL')
        esc_name = item_name.replace("'", "''")
        menu_stmts.append(
            f"INSERT INTO dabbahwala.menu_items (item_name, category, is_veg, avg_price) "
            f"VALUES ('{esc_name}', '{cat}', {is_veg}, {avg_p:.2f}) "
            f"ON CONFLICT (item_name) DO UPDATE SET avg_price = {avg_p:.2f}, category = '{cat}'"
        )
    exec_sql_batch_api(menu_stmts, "menu_items")

    # Insert contacts
    print("  Inserting contacts...")
    contact_stmts = []
    for email, cust in email_to_cust.items():
        if '@placeholder.local' in email:
            email_val = 'NULL'
        else:
            email_val = f"'{email.replace(chr(39), chr(39)+chr(39))}'"

        name = cust.get('name', '')
        parts = name.split(None, 1)
        first = parts[0].replace("'", "''") if parts else ''
        last = parts[1].replace("'", "''") if len(parts) > 1 else ''
        phone = cust.get('phone', '')
        address = cust.get('address', '').replace("'", "''")
        lifecycle = cust.get('lifecycle', 'cold')
        total_orders = cust.get('total_orders', 0)
        last_order = cust.get('last_order_at')
        sub_type = cust.get('subscription_type', '')
        primary_src = cust.get('primary_source', 'Website')
        merged = 'true' if cust.get('merged_from_app') else 'false'

        if last_order is not None and pd.notna(last_order):
            last_order_val = f"'{pd.Timestamp(last_order).strftime('%Y-%m-%d')}'"
        else:
            last_order_val = 'NULL'

        contact_stmts.append(
            f"INSERT INTO dabbahwala.contacts "
            f"(email, phone, first_name, last_name, lifecycle_segment, total_orders, last_order_at, "
            f"address, subscription_type, primary_source, merged_from_app) "
            f"VALUES ({email_val}, '{phone}', '{first}', '{last}', '{lifecycle}', {total_orders}, "
            f"{last_order_val}, '{address}', '{sub_type}', '{primary_src}', {merged}) "
            f"ON CONFLICT (email) DO UPDATE SET "
            f"phone = COALESCE(NULLIF(EXCLUDED.phone, ''), dabbahwala.contacts.phone), "
            f"first_name = COALESCE(NULLIF(EXCLUDED.first_name, ''), dabbahwala.contacts.first_name), "
            f"last_name = COALESCE(NULLIF(EXCLUDED.last_name, ''), dabbahwala.contacts.last_name), "
            f"address = COALESCE(NULLIF(EXCLUDED.address, ''), dabbahwala.contacts.address), "
            f"total_orders = EXCLUDED.total_orders, "
            f"last_order_at = EXCLUDED.last_order_at, "
            f"lifecycle_segment = EXCLUDED.lifecycle_segment, "
            f"subscription_type = EXCLUDED.subscription_type, "
            f"primary_source = EXCLUDED.primary_source"
        )
    exec_sql_batch_api(contact_stmts, "contacts")

    # Build email -> contact_id map
    print("  Fetching contact IDs...")
    result = exec_sql_api("SELECT id, email FROM dabbahwala.contacts", read=True)
    email_to_id = {}
    if result.get('rows'):
        for r in result['rows']:
            if r['email']:
                email_to_id[r['email'].lower()] = r['id']
    print(f"  {len(email_to_id)} contacts in DB")

    # Insert orders + order_items
    print("  Inserting orders...")
    order_count = 0
    item_count = 0
    for i, order in enumerate(orders_to_insert):
        if i % 500 == 0:
            print(f"  orders: {i}/{len(orders_to_insert)}...")

        ce = order['contact_email']
        cid = email_to_id.get(ce)
        cid_val = str(cid) if cid else 'NULL'

        odate = pd.Timestamp(order['order_date']).strftime('%Y-%m-%d') if pd.notna(order['order_date']) else '2025-01-01'
        oid_ext = order['order_id_external'].replace("'", "''")
        src = order['source'].replace("'", "''")
        app_plat = f"'{order['app_platform']}'" if order.get('app_platform') else 'NULL'
        total = order['total_amount']
        otype = order.get('order_type', '').replace("'", "''")
        dslot = order.get('delivery_slot', '').replace("'", "''")
        cname_raw = order['customer_name_raw'].replace("'", "''")

        sql = (
            f"INSERT INTO dabbahwala.orders "
            f"(contact_id, order_id_external, order_date, source, app_platform, "
            f"total_amount, order_type, delivery_slot, customer_name_raw) "
            f"VALUES ({cid_val}, '{oid_ext}', '{odate}', '{src}', {app_plat}, "
            f"{total}, '{otype}', '{dslot}', '{cname_raw}') "
            f"RETURNING id"
        )
        result = exec_sql_api(sql, read=True)
        order_db_id = None
        if result.get('rows'):
            order_db_id = result['rows'][0]['id']
            order_count += 1

        # Insert order items
        if order_db_id and order['items']:
            for item in order['items']:
                iname = item['item_name'].replace("'", "''")
                qty = item['quantity']
                uprice = item['unit_price']
                ltotal = item['line_total']
                isql = (
                    f"INSERT INTO dabbahwala.order_items "
                    f"(order_id, menu_item_id, item_name, quantity, unit_price, line_total) "
                    f"VALUES ({order_db_id}, "
                    f"(SELECT id FROM dabbahwala.menu_items WHERE item_name = '{iname}' LIMIT 1), "
                    f"'{iname}', {qty}, {uprice}, {ltotal})"
                )
                r = exec_sql_api(isql, read=False)
                if 'error' not in r:
                    item_count += 1

    print(f"  Orders inserted: {order_count}")
    print(f"  Order items inserted: {item_count}")

    # Also insert order_placed events for lifecycle engine
    print("  Inserting order_placed events...")
    event_stmts = []
    for order in orders_to_insert:
        ce = order['contact_email']
        cid = email_to_id.get(ce)
        if not cid:
            continue
        odate = pd.Timestamp(order['order_date']).strftime('%Y-%m-%dT00:00:00Z') if pd.notna(order['order_date']) else '2025-01-01T00:00:00Z'
        meta = json.dumps({
            'source': order['source'],
            'total_amount': order['total_amount'],
            'order_type': order.get('order_type', ''),
            'order_id_external': order['order_id_external'],
        }).replace("'", "''")
        event_stmts.append(
            f"INSERT INTO dabbahwala.events (contact_id, event_type, metadata, occurred_at) "
            f"VALUES ({cid}, 'order_placed', '{meta}', '{odate}')"
        )
    exec_sql_batch_api(event_stmts, "events")

    print("\n[6/6] Verifying...")
    for table in ['contacts', 'menu_items', 'orders', 'order_items', 'events']:
        r = exec_sql_api(f"SELECT COUNT(*) as cnt FROM dabbahwala.{table}", read=True)
        cnt = r.get('rows', [{}])[0].get('cnt', '?')
        print(f"  {table}: {cnt} rows")

    print("\nDone!")


if __name__ == '__main__':
    main()
