"""
One-shot script: Create the Airtable 'Menu Catalog' table and bulk-load all 126 items
from data/WeeklyMenu.csv.

Usage:
  AIRTABLE_API_KEY=patXXX python3 scripts/setup_menu_catalog.py
"""
import csv
import json
import os
import sys
import time

import httpx

BASE_ID = "appuy2VTIao6XVpIW"
TABLE_NAME = "Menu Catalog"
ADDED_DATE = "2026-02-23"  # Week these items were first cataloged
CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "WeeklyMenu.csv")

API_KEY = os.environ.get("AIRTABLE_API_KEY", "")
if not API_KEY:
    print("ERROR: AIRTABLE_API_KEY not set")
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

META_URL = f"https://api.airtable.com/v0/meta/bases/{BASE_ID}/tables"
DATA_URL = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_NAME}"


# ── Step 1: Create the table ──────────────────────────────────────────────────
print("Creating 'Menu Catalog' table...")
table_def = {
    "name": TABLE_NAME,
    "fields": [
        {"name": "Item Name", "type": "singleLineText"},
        {"name": "Category", "type": "singleLineText"},
        {"name": "Is Veg", "type": "checkbox",
         "options": {"icon": "check", "color": "greenBright"}},
        {"name": "Description", "type": "multilineText"},
        {"name": "Image URL", "type": "url"},
        {"name": "Price", "type": "number", "options": {"precision": 2}},
        {"name": "Added Date", "type": "date",
         "options": {"dateFormat": {"name": "iso"}}},
    ],
}
resp = httpx.post(META_URL, headers=HEADERS, json=table_def, timeout=30)
if resp.status_code == 422 and "already exists" in resp.text.lower():
    print("  Table already exists — skipping creation")
    table_id = None
elif resp.is_success:
    table_id = resp.json().get("id")
    print(f"  Created table ID: {table_id}")
else:
    print(f"  ERROR creating table: {resp.status_code} {resp.text[:300]}")
    sys.exit(1)


# ── Step 2: Load CSV items ────────────────────────────────────────────────────
print(f"Loading items from {CSV_PATH}...")
items = []
with open(CSV_PATH, newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        name = row.get("Item Name", "").strip()
        price_raw = row.get("Price", "").strip().replace("$", "").strip()
        if not name:
            continue
        try:
            price = float(price_raw)
        except ValueError:
            price = None
        items.append({
            "Item Name": name,
            "Added Date": ADDED_DATE,
            **({"Price": price} if price is not None else {}),
        })

print(f"  {len(items)} items to upload")

# Airtable allows max 10 records per request
created = 0
errors = 0
for i in range(0, len(items), 10):
    batch = items[i:i+10]
    payload = {"records": [{"fields": item} for item in batch]}
    resp = httpx.post(DATA_URL, headers=HEADERS, json=payload, timeout=30)
    if resp.is_success:
        created += len(batch)
        print(f"  Uploaded batch {i//10 + 1}: {len(batch)} items (total {created})")
    else:
        print(f"  ERROR batch {i//10 + 1}: {resp.status_code} {resp.text[:200]}")
        errors += len(batch)
    time.sleep(0.25)  # stay under Airtable rate limit (5 req/s)

print(f"\nDone — created={created} errors={errors}")
if table_id:
    print(f"Table ID: {table_id}")
    print(f"Add this to n8n/config.json as the airtable_menu_sync table ID")
