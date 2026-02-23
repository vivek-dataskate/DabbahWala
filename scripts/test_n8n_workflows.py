#!/usr/bin/env python3
"""
n8n Workflow Health Check
=========================
Verifies:
  1. All 21 production workflows are ACTIVE on the n8n instance
  2. Each Python API endpoint that workflows call returns a valid response
  3. No missing credentials (checks for placeholder IDs in workflow JSONs)

Usage:
    python scripts/test_n8n_workflows.py [--api-url URL] [--n8n-url URL]

Defaults:
    api_url  = https://dabbahwala-latest.onrender.com
    n8n_url  = https://digitalworker.dataskate.io
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

# ─── Config ───────────────────────────────────────────────────────────────────

DEFAULT_API  = "https://dabbahwala-latest.onrender.com"
DEFAULT_N8N  = "https://digitalworker.dataskate.io"
N8N_KEY      = os.environ.get(
    "N8N_API_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJkMmFmN2JlMi1hMTYwLTRlZmUtYjFhOC0wMjlmM2U3OWZmMDkiLCJpc3MiOiJuOG4iLCJhdWQiOiJwdWJsaWMtYXBpIiwiaWF0IjoxNzcxNTQ3NzAzfQ"
    ".lOtKLp-YEdulBGSOD62uKCPTJBHOl_-0rDy2qa79FqE",
)

SHOULD_BE_ACTIVE = {
    "[System — Action] Action Queue Executor",
    "[Claude — Inference] Agent Orchestration",
    "[Airtable — Evidence] Outcome Sync",
    "[Airtable — Evidence] Playbook Sync",
    "[Telnyx — Action] Broadcast Dispatch",
    "[Telnyx — Form] Broadcast Form",
    "[Instantly — Evidence] Campaign Performance",
    "[System — Maintenance] Chatbot Docs Reindex",
    "[Reporting — Inference] Daily Activity Report",
    "[Reporting — Inference] Daily Field Brief",
    "[Orders — Action] Daily CSV Upload",
    "[Reporting — Inference] Daily Outcome Report",
    "[Claude — Inference] Hourly Intelligence Cycle",
    "[Instantly — Action] Campaign Sync",
    "[Instantly — Action] Campaign Setup",
    "[Claude — Decision] Lapsed Customer Daily Cycle",
    "[Claude — Decision] Lifecycle Cycle Runner",
    "[Airtable — Inference] Marketing Query Form",
    "[Shipday — Evidence] Delivery Collector",
    "[Shipday — Evidence] Feedback Sync",
    "[Telnyx — Evidence] Inbound SMS Collector",
}

SHOULD_BE_INACTIVE = {
    "[Google — Evidence] Docs & Drive Sync",      # needs OAuth setup
    "[Shipday — Evidence] Historical Import",     # manual trigger only
}

CRED_PLACEHOLDERS = [
    "SHIPDAY_CREDENTIAL_ID",
    "TELNYX_CREDENTIAL_ID",
    "instantly-api-key",
    "GOOGLE_DOCS_CREDENTIAL_ID",
    "GOOGLE_DRIVE_CREDENTIAL_ID",
]

# ─── Helpers ──────────────────────────────────────────────────────────────────

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
WARN = "\033[33m⚠\033[0m"
SKIP = "\033[90m○\033[0m"


def http_get(url: str, headers: dict = {}) -> tuple[int, Optional[dict]]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
            try:
                return resp.status, json.loads(body)
            except Exception:
                return resp.status, None
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        return 0, None


def check(label: str, ok: bool, detail: str = "") -> bool:
    icon = PASS if ok else FAIL
    msg = f"  {icon}  {label}"
    if detail:
        msg += f"  ({detail})"
    print(msg)
    return ok


# ─── Test sections ────────────────────────────────────────────────────────────

def test_n8n_workflows(n8n_url: str) -> int:
    """Section 1: Verify all n8n workflows are in the correct active/inactive state."""
    print("\n── Section 1: n8n Workflow States ──────────────────────────────")
    failures = 0

    status, data = http_get(
        f"{n8n_url}/api/v1/workflows?limit=100",
        headers={"X-N8N-API-KEY": N8N_KEY},
    )
    if status != 200 or not data:
        print(f"  {FAIL}  Cannot reach n8n API (status={status})")
        return 1

    live = {w["name"]: w for w in data.get("data", [])}
    found_active   = {n for n, w in live.items() if w.get("active")}
    found_inactive = {n for n, w in live.items() if not w.get("active")}

    # Check expected-active
    for name in sorted(SHOULD_BE_ACTIVE):
        if name not in live:
            check(name, False, "missing from n8n!")
            failures += 1
        elif name in found_active:
            check(name, True, "ACTIVE")
        else:
            check(name, False, "INACTIVE — should be active")
            failures += 1

    # Check expected-inactive
    for name in sorted(SHOULD_BE_INACTIVE):
        if name not in live:
            check(name + " (inactive)", False, "missing from n8n!")
            failures += 1
        elif name in found_inactive:
            print(f"  {SKIP}  {name}  (correctly inactive)")
        else:
            print(f"  {WARN}  {name}  (unexpectedly active)")

    # Warn about old SMS dispatch if still present
    if "[Telnyx — Action] SMS Dispatch" in live:
        print(f"  {WARN}  Old [Telnyx — Action] SMS Dispatch still exists — should be deleted")
        failures += 1

    return failures


def test_credential_placeholders() -> int:
    """Section 2: Ensure no workflow JSON uses placeholder credential IDs."""
    print("\n── Section 2: Credential Placeholder Audit ─────────────────────")
    failures = 0
    n8n_dir = Path(__file__).parent.parent / "n8n"

    for fpath in sorted(n8n_dir.glob("*.json")):
        if fpath.name == "config.json":
            continue
        raw = fpath.read_text()
        found = [p for p in CRED_PLACEHOLDERS if p in raw]
        if found:
            check(fpath.name, False, f"placeholder creds: {found}")
            failures += 1
        else:
            check(fpath.name, True, "no placeholders")

    return failures


def test_python_api_endpoints(api_url: str) -> int:
    """Section 3: Smoke-test every Python endpoint that n8n workflows call."""
    print("\n── Section 3: Python API Endpoint Health ────────────────────────")
    failures = 0

    ENDPOINTS = [
        # [workflow group] description → (method, path, expected_status)
        ("Shipday",    "sync-status",             "GET",  "/api/shipday/sync-status",               200),
        ("Shipday",    "feedback-stats",           "GET",  "/api/shipday/feedback-stats",             200),
        ("Telnyx",     "pending inbound messages", "GET",  "/api/telnyx/pending",                    200),
        ("Airtable",   "playbook rules",           "GET",  "/api/playbook",                          200),
        ("Instantly",  "campaign analytics",       "GET",  "/api/campaigns/analytics",               200),
        ("Instantly",  "campaign list",            "GET",  "/api/webhooks/campaigns",                200),
        ("Orders",     "daily-orders schema",      "GET",  "/api/daily-orders/download-shipday-csv/00000000-0000-0000-0000-000000000000", 404),  # 404 = endpoint exists
        ("Reporting",  "activity data",            "GET",  "/api/agents/report/activity-data",       200),
        ("Reporting",  "outcome data",             "GET",  "/api/agents/report/outcome-data",        200),
        ("Claude",     "action-queue pending",     "GET",  "/api/agents/action-queue/pending",       200),
        ("Claude",     "contacts for cycle",       "GET",  "/api/contacts?limit=1",                  200),
        ("System",     "broadcasts pending",       "GET",  "/api/broadcasts/pending-recipients",     200),
        ("System",     "broadcasts list",          "GET",  "/api/broadcasts/",                       200),
        ("System",     "lifecycle/run exists",     "GET",  "/api/lifecycle/run",                     405),  # POST-only → 405
        ("Chatbot",    "chatbot query schema",     "GET",  "/api/chatbot/docs",                      200),
    ]

    for group, label, method, path, expected in ENDPOINTS:
        url = f"{api_url}{path}"
        status, _ = http_get(url)
        ok = status == expected
        if not ok and status == 0:
            detail = "connection error"
        elif not ok:
            detail = f"got {status}, expected {expected}"
        else:
            detail = f"HTTP {status}"
        check(f"[{group}] {label}", ok, detail)
        if not ok:
            failures += 1

    return failures


def test_action_queue_types(api_url: str) -> int:
    """Section 4: Verify action_queue pending endpoint returns correct schema."""
    print("\n── Section 4: Action Queue Schema ──────────────────────────────")
    failures = 0

    status, data = http_get(f"{api_url}/api/agents/action-queue/pending")
    if status != 200:
        check("action-queue/pending reachable", False, f"status={status}")
        return 1

    check("action-queue/pending reachable", True, f"HTTP 200, {len(data or [])} pending items")

    if data:
        required_keys = {"id", "action_type", "payload", "created_at"}
        sample = data[0] if isinstance(data, list) else {}
        missing = required_keys - set(sample.keys())
        check("pending item schema", not missing, f"missing keys: {missing}" if missing else "all keys present")
        if missing:
            failures += 1

        # Verify no unknown action types
        valid_types = {
            "send_sms", "move_campaign", "escalate_airtable", "none",
            "sync_airtable_task", "log_query_to_airtable",
            "push_instantly_lead", "push_instantly_sequences",
            "upload_google_drive", "send_email_report",
        }
        unknown = {r["action_type"] for r in data if r.get("action_type") not in valid_types}
        check("all action_types known", not unknown, f"unknown: {unknown}" if unknown else "")
        if unknown:
            failures += 1

    return failures


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="n8n workflow health check")
    parser.add_argument("--api-url", default=DEFAULT_API, help="Render API base URL")
    parser.add_argument("--n8n-url", default=DEFAULT_N8N, help="n8n instance URL")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  n8n Workflow Health Check")
    print(f"  API: {args.api_url}")
    print(f"  n8n: {args.n8n_url}")
    print(f"{'='*60}")

    t0 = time.time()
    total_failures = 0

    total_failures += test_n8n_workflows(args.n8n_url)
    total_failures += test_credential_placeholders()
    total_failures += test_python_api_endpoints(args.api_url)
    total_failures += test_action_queue_types(args.api_url)

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    if total_failures == 0:
        print(f"  {PASS}  All checks passed  ({elapsed:.1f}s)")
    else:
        print(f"  {FAIL}  {total_failures} check(s) failed  ({elapsed:.1f}s)")
    print(f"{'='*60}\n")

    sys.exit(0 if total_failures == 0 else 1)


if __name__ == "__main__":
    main()
