#!/usr/bin/env python3
"""
Create Airtable tables for DabbahWala.

Usage:
    AIRTABLE_API_KEY=patXXX python scripts/create_airtable_tables.py

Requires a Personal Access Token with scope: schema.bases:write, schema.bases:read
"""

import os
import sys
import json
import urllib.request
import urllib.error

API_KEY = os.environ.get("AIRTABLE_API_KEY", "")
BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "appuy2VTIao6XVpIW")

if not API_KEY or API_KEY.startswith("pat_your"):
    print("ERROR: Set AIRTABLE_API_KEY env var to your Personal Access Token")
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}


def airtable_request(method, path, body=None):
    url = f"https://api.airtable.com{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        print(f"  HTTP {e.code}: {error_body}")
        return None


def get_existing_tables():
    result = airtable_request("GET", f"/v0/meta/bases/{BASE_ID}/tables")
    if not result:
        return {}
    return {t["name"]: t["id"] for t in result.get("tables", [])}


def create_table(name, fields):
    print(f"  Creating table: {name}")
    result = airtable_request(
        "POST",
        f"/v0/meta/bases/{BASE_ID}/tables",
        {"name": name, "fields": fields},
    )
    if result:
        print(f"  OK — table id: {result.get('id')}")
    return result


# ── Table definitions ──────────────────────────────────────────────────────────

TABLES = [
    {
        "name": "Menu Catalog",
        "fields": [
            # Primary field — one row per menu item
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
    },
    {
        "name": "Field Sales Tasks",
        "fields": [
            # Primary field
            {"name": "Customer Name", "type": "singleLineText"},
            {"name": "Phone", "type": "phoneNumber"},
            {"name": "Email", "type": "email"},
            {
                "name": "Status",
                "type": "singleSelect",
                "options": {
                    "choices": [
                        {"name": "New", "color": "blueBright"},
                        {"name": "In Progress", "color": "yellowBright"},
                        {"name": "Contacted", "color": "cyanBright"},
                        {"name": "Ordered", "color": "greenBright"},
                        {"name": "Converted", "color": "green"},
                        {"name": "Not Interested", "color": "redBright"},
                        {"name": "Declined", "color": "red"},
                        {"name": "No Answer", "color": "gray"},
                        {"name": "Unreachable", "color": "grayBright"},
                    ]
                },
            },
            {
                "name": "Priority",
                "type": "singleSelect",
                "options": {
                    "choices": [
                        {"name": "Hot", "color": "redBright"},
                        {"name": "Warm", "color": "yellowBright"},
                        {"name": "Cold", "color": "blueBright"},
                    ]
                },
            },
            {"name": "Reason", "type": "multilineText"},
            {"name": "Suggested Action", "type": "multilineText"},
            {"name": "Outcome Notes", "type": "multilineText"},
            {"name": "Action Type", "type": "singleLineText"},
            {"name": "Postgres Opportunity ID", "type": "singleLineText"},
            {
                "name": "Confidence Score",
                "type": "number",
                "options": {"precision": 2},
            },
            {"name": "Synced to Postgres", "type": "checkbox", "options": {"icon": "check", "color": "greenBright"}},
        ],
    },
    {
        "name": "Team Content",
        "fields": [
            # Primary field — used as merge key in google_docs_sync
            {"name": "Doc ID", "type": "singleLineText"},
            {"name": "Doc Name", "type": "singleLineText"},
            {
                "name": "Type",
                "type": "singleSelect",
                "options": {
                    "choices": [
                        {"name": "agent_playbook"},
                        {"name": "marketing_input"},
                        {"name": "field_team_input"},
                        {"name": "general"},
                    ]
                },
            },
            {"name": "Link", "type": "url"},
            {"name": "Content", "type": "multilineText"},
            {"name": "Word Count", "type": "number", "options": {"precision": 0}},
            {
                "name": "Synced At",
                "type": "dateTime",
                "options": {
                    "dateFormat": {"name": "iso"},
                    "timeFormat": {"name": "24hour"},
                    "timeZone": "utc",
                },
            },
        ],
    },
    {
        "name": "Marketing Queries",
        "fields": [
            # Primary field
            {"name": "Category", "type": "singleLineText"},
            {"name": "Question", "type": "multilineText"},
            {"name": "Answer", "type": "multilineText"},
            {
                "name": "Timestamp",
                "type": "dateTime",
                "options": {
                    "dateFormat": {"name": "iso"},
                    "timeFormat": {"name": "24hour"},
                    "timeZone": "utc",
                },
            },
        ],
    },
    {
        "name": "Team Inputs",
        "fields": [
            {"name": "Title", "type": "singleLineText"},
            {"name": "Content", "type": "multilineText"},
            {"name": "Source", "type": "singleLineText"},
            {"name": "Submitted At", "type": "dateTime", "options": {"dateFormat": {"name": "iso"}, "timeFormat": {"name": "24hour"}, "timeZone": "utc"}},
        ],
    },
]

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print(f"Base: {BASE_ID}")
    print("Fetching existing tables...")
    existing = get_existing_tables()
    print(f"  Existing: {list(existing.keys()) or 'none'}\n")

    for table_def in TABLES:
        name = table_def["name"]
        if name in existing:
            print(f"  Skipping '{name}' — already exists (id: {existing[name]})")
        else:
            create_table(name, table_def["fields"])
        print()

    print("Done.")


if __name__ == "__main__":
    main()
