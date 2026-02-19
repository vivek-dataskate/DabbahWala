"""
Generate a bulk SQL file from DabbahWala Excel data.

Replicates ALL the processing logic from load_data.py (fuzzy matching,
lifecycle computation, menu-item classification, etc.) but instead of
hitting the HTTP API, emits a single SQL file that can be piped directly
into psql:

    psql $DATABASE_URL < scripts/bulk_load.sql

Usage:
    python scripts/generate_sql.py
"""

import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from difflib import SequenceMatcher

import pandas as pd

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CUSTOMER_FILE = "data/DW Costumers-latest.xlsx"
ORDER_FILE = "data/DabbahWala OrderData-2025.xlsx"
OUTPUT_FILE = "scripts/bulk_load.sql"
FUZZY_THRESHOLD = 0.72  # same as load_data.py

# ---------------------------------------------------------------------------
# Category classification for menu items (identical to load_data.py)
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

VEG_KEYWORDS = [
    'veg', 'paneer', 'aloo', 'palak', 'saag', 'mutter', 'dal', 'idly',
    'dosa', 'curd', 'yogurt', 'raita', 'rice', 'roti', 'lassi', 'poori',
    'sambar', 'pungulu', 'upma', 'pesarattu', 'gobi',
]
NON_VEG_KEYWORDS = [
    'non-veg', 'nonveg', 'chicken', 'goat', 'shrimp', 'mutton', 'egg',
    'fish', 'lamb', 'meat', 'tandoori chicken', 'butter chicken',
    'chicken tikka', 'chicken malai', 'chicken curry', 'chicken dum',
]


def classify_item(name: str) -> tuple:
    """Return (category, is_veg) for a menu item name."""
    lower = name.lower()
    category = 'other'
    for pattern, cat in CATEGORY_RULES:
        if re.search(pattern, lower):
            category = cat
            break
    is_veg = None
    if any(kw in lower for kw in NON_VEG_KEYWORDS):
        is_veg = False
    elif any(kw in lower for kw in VEG_KEYWORDS):
        is_veg = True
    return category, is_veg


# ---------------------------------------------------------------------------
# Fuzzy matching helpers (identical to load_data.py)
# ---------------------------------------------------------------------------
def normalize_name(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r'[^\w\s]', '', name)
    name = re.sub(r'\s+', ' ', name)
    return name


def fuzzy_match_name(app_name: str, customer_names: dict) -> tuple:
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
        if not email or '@' not in email:
            continue
        fn_parts = full_name.split()
        if not fn_parts:
            continue
        cust_first = fn_parts[0]
        if not cust_first or not first or cust_first[0] != first[0]:
            continue
        if cust_first == first or SequenceMatcher(None, cust_first, first).ratio() > 0.88:
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

    candidates.sort(key=lambda x: -x[2])
    best = candidates[0]
    if best[2] >= FUZZY_THRESHOLD:
        return best[1], best[2]
    return None, 0


# ---------------------------------------------------------------------------
# Data loading (identical to load_data.py)
# ---------------------------------------------------------------------------
def load_customers(filepath: str) -> pd.DataFrame:
    df = pd.read_excel(filepath, sheet_name="Sheet5")
    df['email_norm'] = df['Email'].fillna('').astype(str).str.strip().str.lower()
    df['phone_norm'] = df['Phone Number'].fillna('').astype(str).apply(
        lambda x: re.sub(r'\D', '', x)[-10:] if len(re.sub(r'\D', '', x)) >= 10 else ''
    )
    df['name_norm'] = df['Customer Name'].fillna('').astype(str).str.strip().str.lower()
    return df


def load_orders(filepath: str) -> tuple:
    xls = pd.ExcelFile(filepath)
    summaries = []
    items = []

    for sheet in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet)
        df.columns = ['Source'] + list(df.columns[1:])
        for c in df.columns:
            if c.lower().startswith('email'):
                df.rename(columns={c: 'Email address'}, inplace=True)
        df = df.iloc[1:].reset_index(drop=True)

        for _, row in df.iterrows():
            cname = row.get('Customer name')
            if pd.isna(cname) or not str(cname).strip():
                continue

            source = str(row.get('Source', '')).strip() if pd.notna(row.get('Source')) else 'Website'
            item_ordered = row.get('Item Ordered')
            is_app = source == 'Food Delivery Apps'

            order_id_raw = str(row.get('OrderID', '')).strip() if pd.notna(row.get('OrderID')) else ''
            app_platform = None
            if is_app:
                for platform in ['DoorDash', 'Uber Eats', 'Grubhub']:
                    if platform.lower() in order_id_raw.lower():
                        app_platform = platform
                        break
                if not app_platform:
                    app_platform = 'Unknown'

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
# SQL-safe string escaping
# ---------------------------------------------------------------------------
def esc(value) -> str:
    """Escape a value for embedding in a SQL string literal.
    Returns the content *without* surrounding quotes.
    """
    if value is None:
        return ''
    s = str(value)
    s = s.replace("'", "''")        # double single-quotes
    s = s.replace('\\', '\\\\')     # escape backslashes
    s = s.replace('\x00', '')       # strip NUL bytes
    return s


def sql_str(value) -> str:
    """Return a SQL-quoted string or NULL."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return 'NULL'
    return f"'{esc(value)}'"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("DabbahWala SQL Generator")
    print("=" * 70)

    # -----------------------------------------------------------------------
    # 1. Load customer master
    # -----------------------------------------------------------------------
    print("\n[1/6] Loading customer master...")
    customers = load_customers(CUSTOMER_FILE)
    print(f"  {len(customers)} customers loaded")

    email_to_cust = {}
    phone_to_cust = {}
    name_to_email = {}

    for _, row in customers.iterrows():
        email = row['email_norm']
        phone = row['phone_norm']
        name = row['name_norm']
        cust_name = str(row['Customer Name']).strip()
        address = str(row['Address']).strip() if pd.notna(row.get('Address')) else ''

        if email:
            email_to_cust[email] = {
                'name': cust_name, 'email': email, 'phone': phone, 'address': address,
            }
        if phone:
            phone_to_cust[phone] = email
        if name and email:
            name_to_email[name] = email

    print(f"  {len(email_to_cust)} by email, {len(phone_to_cust)} by phone, {len(name_to_email)} by name")

    # -----------------------------------------------------------------------
    # 2. Load orders
    # -----------------------------------------------------------------------
    print("\n[2/6] Loading order data...")
    web_orders, app_items = load_orders(ORDER_FILE)
    print(f"  {len(web_orders)} website orders, {len(app_items)} app item rows")

    app_orders_grouped = defaultdict(list)
    for item in app_items:
        key = (item['order_id_external'], item['order_date'], item['customer_name'])
        app_orders_grouped[key].append(item)

    print(f"  {len(app_orders_grouped)} unique app orders (grouped)")

    # -----------------------------------------------------------------------
    # 3. Match & merge (identical logic to load_data.py)
    # -----------------------------------------------------------------------
    print("\n[3/6] Matching orders to customers...")

    orders_to_insert = []
    menu_items_set = {}

    # -- Website orders --
    web_matched = 0
    web_new = 0
    for order in web_orders:
        email = order['email']
        phone = order['phone']
        name = order['customer_name']

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
            if order['address'] and not cust.get('address'):
                cust['address'] = order['address']
        else:
            web_new += 1
            if email:
                contact_email = email
            else:
                contact_email = f"no-email-{normalize_name(name).replace(' ', '-')}@placeholder.local"
            if contact_email not in email_to_cust:
                email_to_cust[contact_email] = {
                    'name': name,
                    'email': contact_email,
                    'phone': phone,
                    'address': order['address'],
                }

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

    # -- App orders with fuzzy matching --
    app_matched_exact = 0
    app_matched_fuzzy = 0
    app_new = 0
    fuzzy_merges = []

    for (oid, odate, cname), item_list in app_orders_grouped.items():
        first_item = item_list[0]
        email = first_item['email']
        phone = first_item['phone']

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
    print(f"  Fuzzy merges:")
    for m in fuzzy_merges[:20]:
        print(f"    '{m['app_name']}' -> '{m['matched_to']}' ({m['email']}) conf={m['confidence']:.2f}")
    if len(fuzzy_merges) > 20:
        print(f"    ... and {len(fuzzy_merges) - 20} more")

    # -----------------------------------------------------------------------
    # 4. Compute contact stats & lifecycle (identical to load_data.py)
    # -----------------------------------------------------------------------
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

    lifecycle_dist = defaultdict(int)
    for cust in email_to_cust.values():
        lifecycle_dist[cust.get('lifecycle', 'cold')] += 1
    print(f"  Lifecycle distribution:")
    for seg, cnt in sorted(lifecycle_dist.items(), key=lambda x: -x[1]):
        print(f"    {seg}: {cnt}")

    total_contacts = len(email_to_cust)
    print(f"\n  {total_contacts} total contacts, {len(contact_order_counts)} with orders")
    print(f"  {len(menu_items_set)} unique menu items")
    print(f"  {len(orders_to_insert)} total orders")
    print(f"  {sum(len(o['items']) for o in orders_to_insert)} order items")

    # -----------------------------------------------------------------------
    # 5. Generate SQL
    # -----------------------------------------------------------------------
    print("\n[5/6] Generating SQL...")
    lines = []
    w = lines.append  # shorthand

    w("-- =======================================================================")
    w("-- DabbahWala bulk load - auto-generated by scripts/generate_sql.py")
    w(f"-- Generated at {datetime.now().isoformat()}")
    w("-- =======================================================================")
    w("")
    w("SET search_path TO dabbahwala;")
    w("")
    w("BEGIN;")
    w("")

    # --- Menu items ---
    w("-- -----------------------------------------------------------------------")
    w(f"-- Menu items ({len(menu_items_set)} rows)")
    w("-- -----------------------------------------------------------------------")
    for item_name, info in menu_items_set.items():
        avg_p = sum(info['prices']) / len(info['prices']) if info['prices'] else 0
        cat = info['category']
        is_veg_val = 'true' if info['is_veg'] else ('false' if info['is_veg'] is False else 'NULL')
        w(
            f"INSERT INTO menu_items (item_name, category, is_veg, avg_price) "
            f"VALUES ('{esc(item_name)}', '{esc(cat)}', {is_veg_val}, {avg_p:.2f}) "
            f"ON CONFLICT (item_name) DO UPDATE SET "
            f"avg_price = EXCLUDED.avg_price, category = EXCLUDED.category, is_veg = EXCLUDED.is_veg;"
        )
    w("")

    # --- Contacts ---
    w("-- -----------------------------------------------------------------------")
    w(f"-- Contacts ({total_contacts} rows)")
    w("-- -----------------------------------------------------------------------")
    for email, cust in email_to_cust.items():
        if '@placeholder.local' in email:
            email_val = 'NULL'
        else:
            email_val = f"'{esc(email)}'"

        name = cust.get('name', '')
        parts = name.split(None, 1)
        first = esc(parts[0]) if parts else ''
        last = esc(parts[1]) if len(parts) > 1 else ''
        phone = esc(cust.get('phone', ''))
        address = esc(cust.get('address', ''))
        lifecycle = cust.get('lifecycle', 'cold')
        total_orders = cust.get('total_orders', 0)
        last_order = cust.get('last_order_at')
        sub_type = esc(cust.get('subscription_type', ''))
        primary_src = esc(cust.get('primary_source', 'Website'))
        merged = 'true' if cust.get('merged_from_app') else 'false'

        if last_order is not None and pd.notna(last_order):
            last_order_val = f"'{pd.Timestamp(last_order).strftime('%Y-%m-%d')}'"
        else:
            last_order_val = 'NULL'

        w(
            f"INSERT INTO contacts "
            f"(email, phone, first_name, last_name, lifecycle_segment, total_orders, last_order_at, "
            f"address, subscription_type, primary_source, merged_from_app) "
            f"VALUES ({email_val}, '{phone}', '{first}', '{last}', '{lifecycle}', {total_orders}, "
            f"{last_order_val}, '{address}', '{sub_type}', '{primary_src}', {merged}) "
            f"ON CONFLICT (email) DO UPDATE SET "
            f"phone = COALESCE(NULLIF(EXCLUDED.phone, ''), contacts.phone), "
            f"first_name = COALESCE(NULLIF(EXCLUDED.first_name, ''), contacts.first_name), "
            f"last_name = COALESCE(NULLIF(EXCLUDED.last_name, ''), contacts.last_name), "
            f"address = COALESCE(NULLIF(EXCLUDED.address, ''), contacts.address), "
            f"total_orders = EXCLUDED.total_orders, "
            f"last_order_at = EXCLUDED.last_order_at, "
            f"lifecycle_segment = EXCLUDED.lifecycle_segment, "
            f"subscription_type = EXCLUDED.subscription_type, "
            f"primary_source = EXCLUDED.primary_source;"
        )
    w("")

    # --- Orders ---
    w("-- -----------------------------------------------------------------------")
    w(f"-- Orders ({len(orders_to_insert)} rows)")
    w("-- -----------------------------------------------------------------------")
    for order in orders_to_insert:
        ce = order['contact_email']
        odate = pd.Timestamp(order['order_date']).strftime('%Y-%m-%d') if pd.notna(order['order_date']) else '2025-01-01'
        oid_ext = esc(order['order_id_external'])
        src = esc(order['source'])
        app_plat = f"'{esc(order['app_platform'])}'" if order.get('app_platform') else 'NULL'
        total = order['total_amount']
        otype = esc(order.get('order_type', ''))
        dslot = esc(order.get('delivery_slot', ''))
        cname_raw = esc(order['customer_name_raw'])

        # Resolve contact_id via subquery on email
        if '@placeholder.local' in ce:
            # Placeholder contacts were inserted with email = NULL; we cannot
            # look them up by email.  Match on first_name + last_name instead.
            name = email_to_cust.get(ce, {}).get('name', '')
            nparts = name.split(None, 1)
            fn = esc(nparts[0]) if nparts else ''
            ln = esc(nparts[1]) if len(nparts) > 1 else ''
            contact_id_expr = (
                f"(SELECT id FROM contacts WHERE first_name = '{fn}' "
                f"AND last_name = '{ln}' LIMIT 1)"
            )
        else:
            contact_id_expr = f"(SELECT id FROM contacts WHERE email = '{esc(ce)}' LIMIT 1)"

        w(
            f"INSERT INTO orders "
            f"(contact_id, order_id_external, order_date, source, app_platform, "
            f"total_amount, order_type, delivery_slot, customer_name_raw) "
            f"VALUES ({contact_id_expr}, '{oid_ext}', '{odate}', '{src}', {app_plat}, "
            f"{total}, '{otype}', '{dslot}', '{cname_raw}');"
        )
    w("")

    # --- Order items ---
    total_items = sum(len(o['items']) for o in orders_to_insert)
    w("-- -----------------------------------------------------------------------")
    w(f"-- Order items ({total_items} rows)")
    w("-- -----------------------------------------------------------------------")
    for order in orders_to_insert:
        if not order['items']:
            continue
        oid_ext = esc(order['order_id_external'])
        cname_raw = esc(order['customer_name_raw'])
        for item in order['items']:
            iname = esc(item['item_name'])
            qty = item['quantity']
            uprice = item['unit_price']
            ltotal = item['line_total']
            w(
                f"INSERT INTO order_items "
                f"(order_id, menu_item_id, item_name, quantity, unit_price, line_total) "
                f"VALUES ("
                f"(SELECT id FROM orders WHERE order_id_external = '{oid_ext}' "
                f"AND customer_name_raw = '{cname_raw}' LIMIT 1), "
                f"(SELECT id FROM menu_items WHERE item_name = '{iname}' LIMIT 1), "
                f"'{iname}', {qty}, {uprice}, {ltotal});"
            )
    w("")

    # --- Events (order_placed) ---
    w("-- -----------------------------------------------------------------------")
    w("-- Events (order_placed)")
    w("-- -----------------------------------------------------------------------")
    event_count = 0
    for order in orders_to_insert:
        ce = order['contact_email']
        odate = pd.Timestamp(order['order_date']).strftime('%Y-%m-%dT00:00:00Z') if pd.notna(order['order_date']) else '2025-01-01T00:00:00Z'

        meta = json.dumps({
            'source': order['source'],
            'total_amount': order['total_amount'],
            'order_type': order.get('order_type', ''),
            'order_id_external': order['order_id_external'],
        })
        meta_esc = esc(meta)

        if '@placeholder.local' in ce:
            name = email_to_cust.get(ce, {}).get('name', '')
            nparts = name.split(None, 1)
            fn = esc(nparts[0]) if nparts else ''
            ln = esc(nparts[1]) if len(nparts) > 1 else ''
            contact_id_expr = (
                f"(SELECT id FROM contacts WHERE first_name = '{fn}' "
                f"AND last_name = '{ln}' LIMIT 1)"
            )
        else:
            contact_id_expr = f"(SELECT id FROM contacts WHERE email = '{esc(ce)}' LIMIT 1)"

        w(
            f"INSERT INTO events (contact_id, event_type, metadata, occurred_at) "
            f"SELECT {contact_id_expr}, 'order_placed', '{meta_esc}', '{odate}' "
            f"WHERE {contact_id_expr} IS NOT NULL;"
        )
        event_count += 1
    w("")

    w("COMMIT;")
    w("")
    w(f"-- Total statements: menu_items={len(menu_items_set)}, contacts={total_contacts}, "
      f"orders={len(orders_to_insert)}, order_items={total_items}, events={event_count}")

    # -----------------------------------------------------------------------
    # 6. Write file
    # -----------------------------------------------------------------------
    print(f"\n[6/6] Writing SQL to {OUTPUT_FILE}...")
    sql_text = '\n'.join(lines) + '\n'
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write(sql_text)

    line_count = len(lines)
    byte_size = len(sql_text.encode('utf-8'))

    print(f"\n{'=' * 70}")
    print(f"DONE")
    print(f"{'=' * 70}")
    print(f"Output file:   {os.path.abspath(OUTPUT_FILE)}")
    print(f"File size:     {byte_size:,} bytes ({byte_size / 1024:.1f} KB)")
    print(f"Line count:    {line_count:,}")
    print(f"")
    print(f"Breakdown:")
    print(f"  Menu items:   {len(menu_items_set)}")
    print(f"  Contacts:     {total_contacts}")
    print(f"  Orders:       {len(orders_to_insert)}")
    print(f"  Order items:  {total_items}")
    print(f"  Events:       {event_count}")
    print(f"  Fuzzy merges: {len(fuzzy_merges)}")


if __name__ == '__main__':
    main()
