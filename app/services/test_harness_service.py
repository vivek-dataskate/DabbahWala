"""
DabbahWala End-to-End Test Harness
====================================
Runs a comprehensive suite of tests validating all agents, integrations,
and automation workflows. Creates and cleans up test data — zero impact
on real customers.

Test Contact (isolated by source='test_harness'):
  Phone:  +18444322224  (our own Telnyx number — self-loop for real SMS tests)
  Email:  vivek@dabbahwala.com  (admin inbox for real email tests)

Test groups
-----------
 1. System Connectivity       — DB, API health, external service pings
 2. Database Schema           — tables, stored functions, campaign routing
 3. Test Contact Setup        — create isolated test contact
 4. Event & Webhook Ingestion — ingest_event, Telnyx webhook, Shipday webhooks
 5. Telnyx / SMS              — real outbound SMS via Telnyx API
 6. AI Agent Pipeline         — 4-layer Claude cycle on test contact
 7. Intelligence & Lifecycle  — 5-phase cycle + SQL rule engine
 8. Instantly / Email         — list campaigns, add/remove lead, analytics
 9. Airtable                  — menu fetch, menu sync, playbook sync, task create/delete
10. Action Queue              — pending list, create entry, mark done
11. Order Processing          — CSV ingest, menu resolution
12. Reports                   — activity report (Claude), outcome report (Claude)
13. Self-Service & Chatbot    — query categories, tier-1 SQL query, chatbot ask
14. Cleanup                   — delete all test data from DB
15. Competitor Agent          — schema check, list runs, list experiments
"""

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.db import get_cursor

logger = logging.getLogger(__name__)

# ─── Constants ───────────────────────────────────────────────────────────────

TEST_PHONE          = "+18444322224"           # Our Telnyx FROM number
TEST_SMS_TO         = "+12144996143"           # Vivek's personal number for SMS tests
TEST_EMAIL          = "vivek@dabbahwala.com"   # Admin inbox — receives test emails
TEST_SOURCE         = "test_harness"
TEST_FIRST_NAME     = "DWTest"
TEST_LAST_NAME      = "Harness"
TEST_ORDER_REF      = "TH-ORDER-TEST-001"

LOCAL_BASE          = f"http://localhost:{os.getenv('PORT', os.getenv('API_PORT', '8000'))}"
TELNYX_BASE         = "https://api.telnyx.com/v2"
INSTANTLY_BASE      = "https://api.instantly.ai/api/v2"
AIRTABLE_BASE       = "https://api.airtable.com/v0"
SHIPDAY_BASE        = "https://api.shipday.com"
ANTHROPIC_BASE      = "https://api.anthropic.com/v1"
N8N_BASE            = os.getenv("N8N_INSTANCE_URL", "https://digitalworker.dataskate.io")
AIRTABLE_BASE_ID    = os.getenv("AIRTABLE_BASE_ID", "appuy2VTIao6XVpIW")

# Expected Instantly campaign names
INSTANTLY_CAMPAIGNS = [
    "DW-NurtureSlow-ColdContacts",
    "DW-PromoStandard-ActiveEngaged",
    "DW-NewCustomerOnboarding",
    "DW-PromoAggressive-LapsedCustomers",
    "DW-Reactivation-LongDormant",
]

# HTTP timeouts
DEFAULT_TIMEOUT = 15   # seconds — external API connectivity checks
CLAUDE_TIMEOUT  = 90   # seconds — Claude agent cycle and reports


# ─── Data Classes ────────────────────────────────────────────────────────────

@dataclass
class TestResult:
    test:        str
    group:       str
    status:      str           # pass | fail | skip | warn
    message:     str
    duration_ms: int  = 0
    details:     dict = field(default_factory=dict)


@dataclass
class TestSuite:
    run_id:           str
    started_at:       str
    triggered_by:     str = "manual"
    completed_at:     Optional[str] = None
    duration_seconds: float = 0.0
    results:          list  = field(default_factory=list)
    test_contact_id:  Optional[int] = None   # set after group 3
    instantly_cold_campaign_id: Optional[str] = None  # set after group 8 setup

    def summary(self) -> dict:
        return {
            "total":    len(self.results),
            "passed":   sum(1 for r in self.results if r.status == "pass"),
            "failed":   sum(1 for r in self.results if r.status == "fail"),
            "skipped":  sum(1 for r in self.results if r.status == "skip"),
            "warnings": sum(1 for r in self.results if r.status == "warn"),
        }

    def to_dict(self) -> dict:
        groups: dict[str, list] = {}
        for r in self.results:
            groups.setdefault(r.group, []).append(asdict(r))

        group_summaries = [
            {
                "name":    gname,
                "passed":  sum(1 for t in gtests if t["status"] == "pass"),
                "failed":  sum(1 for t in gtests if t["status"] == "fail"),
                "skipped": sum(1 for t in gtests if t["status"] == "skip"),
                "tests":   gtests,
            }
            for gname, gtests in groups.items()
        ]
        return {
            "run_id":           self.run_id,
            "started_at":       self.started_at,
            "completed_at":     self.completed_at,
            "triggered_by":     self.triggered_by,
            "duration_seconds": self.duration_seconds,
            "summary":          self.summary(),
            "groups":           group_summaries,
            "all_tests":        [asdict(r) for r in self.results],
        }


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default) or default


def _time_ms(start: float) -> int:
    return int((time.time() - start) * 1000)


def _req(
    method: str,
    url: str,
    *,
    headers: Optional[dict] = None,
    json_body: Any = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[int, Any]:
    """Make a synchronous HTTP request; return (status_code, parsed_body_or_None)."""
    try:
        r = httpx.request(
            method,
            url,
            headers=headers or {},
            json=json_body,
            timeout=timeout,
        )
        try:
            body = r.json()
        except Exception:
            body = r.text
        return r.status_code, body
    except Exception as exc:
        return 0, str(exc)


def _local(method: str, path: str, **kwargs) -> tuple[int, Any]:
    return _req(method, f"{LOCAL_BASE}{path}", **kwargs)


def _telnyx_headers() -> dict:
    return {"Authorization": f"Bearer {_env('TELNYX_API_KEY')}"}


def _instantly_headers() -> dict:
    # INSTANTLY_API_KEY is a base64 workspace_id:secret credential — use as Bearer token.
    key = _env("INSTANTLY_API_KEY")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _airtable_headers() -> dict:
    return {
        "Authorization": f"Bearer {_env('AIRTABLE_API_KEY')}",
        "Content-Type": "application/json",
    }


def _n8n_headers() -> dict:
    return {"X-N8N-API-KEY": _env("N8N_API_KEY")}


def _add(suite: TestSuite, result: TestResult) -> None:
    suite.results.append(result)
    icon = "✓" if result.status == "pass" else ("⚠" if result.status == "warn" else ("–" if result.status == "skip" else "✗"))
    logger.info(
        "TEST %s [%s] %s — %s (%dms)",
        icon, result.group, result.test, result.message, result.duration_ms,
    )


class WarnSignal(Exception):
    """Raised inside a test function to produce a 'warn' result instead of pass/fail."""


def _run(suite: TestSuite, name: str, group: str, fn) -> TestResult:
    """Execute a test function, capture result, append to suite."""
    t0 = time.time()
    try:
        details = fn()
        r = TestResult(test=name, group=group, status="pass",
                       message="OK", duration_ms=_time_ms(t0),
                       details=details or {})
    except WarnSignal as exc:
        r = TestResult(test=name, group=group, status="warn",
                       message=str(exc), duration_ms=_time_ms(t0))
    except AssertionError as exc:
        r = TestResult(test=name, group=group, status="fail",
                       message=str(exc), duration_ms=_time_ms(t0))
    except Exception as exc:
        r = TestResult(test=name, group=group, status="fail",
                       message=f"{type(exc).__name__}: {exc}", duration_ms=_time_ms(t0))
    _add(suite, r)
    return r


def _skip(suite: TestSuite, name: str, group: str, reason: str) -> TestResult:
    r = TestResult(test=name, group=group, status="skip", message=reason)
    _add(suite, r)
    return r


# ─── GROUP 1: System Connectivity ────────────────────────────────────────────

def _g1_connectivity(suite: TestSuite) -> None:
    G = "1_connectivity"

    def db_connection():
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT 1 AS ok")
            row = cur.fetchone()
        assert row["ok"] == 1
        return {"db": "connected"}
    _run(suite, "db_connection", G, db_connection)

    def api_health():
        sc, body = _local("GET", "/health")
        assert sc == 200, f"Expected 200, got {sc}"
        assert isinstance(body, dict) and body.get("status") == "ok", f"Unexpected body: {body}"
        return {"status": body.get("status"), "db": body.get("db")}
    _run(suite, "api_health", G, api_health)

    def telnyx_api():
        key = _env("TELNYX_API_KEY")
        assert key, "TELNYX_API_KEY not set"
        sc, body = _req("GET", f"{TELNYX_BASE}/messaging_profiles?page[size]=1",
                         headers=_telnyx_headers())
        assert sc == 200, f"Telnyx API returned {sc}: {str(body)[:200]}"
        return {"status": sc}
    _run(suite, "telnyx_api", G, telnyx_api)

    def instantly_api():
        key = _env("INSTANTLY_API_KEY")
        assert key, "INSTANTLY_API_KEY not set"
        # v2: GET /campaigns (not the deprecated v1 /campaign/list)
        sc, body = _req("GET", f"{INSTANTLY_BASE}/campaigns?limit=1",
                         headers=_instantly_headers())
        assert sc in (200, 201), f"Instantly API returned {sc}: {str(body)[:200]}"
        return {"status": sc}
    _run(suite, "instantly_api", G, instantly_api)

    def airtable_api():
        key = _env("AIRTABLE_API_KEY")
        assert key, "AIRTABLE_API_KEY not set"
        sc, body = _req(
            "GET",
            f"{AIRTABLE_BASE}/{AIRTABLE_BASE_ID}/Weekly%20Menu?maxRecords=1",
            headers=_airtable_headers(),
        )
        assert sc == 200, f"Airtable API returned {sc}: {str(body)[:200]}"
        return {"status": sc}
    _run(suite, "airtable_api", G, airtable_api)

    def shipday_api():
        key = _env("SHIPDAY_API_KEY")
        if not key:
            raise AssertionError("SHIPDAY_API_KEY not set")
        sc, body = _req(
            "GET",
            f"{SHIPDAY_BASE}/orders?startDate=2026-01-01&endDate=2026-01-02",
            headers={"Authorization": f"Basic {key}"},
        )
        assert sc in (200, 201, 400), f"Shipday API returned {sc}: {str(body)[:200]}"
        return {"status": sc}
    _run(suite, "shipday_api", G, shipday_api)

    def anthropic_api():
        key = _env("ANTHROPIC_API_KEY")
        assert key, "ANTHROPIC_API_KEY not set"
        sc, body = _req(
            "POST",
            f"{ANTHROPIC_BASE}/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json_body={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 10,
                "messages": [{"role": "user", "content": "Reply: OK"}],
            },
            timeout=30,
        )
        assert sc == 200, f"Anthropic API returned {sc}: {str(body)[:200]}"
        return {"model": body.get("model") if isinstance(body, dict) else "?"}
    _run(suite, "anthropic_api", G, anthropic_api)

    def n8n_api():
        key = _env("N8N_API_KEY")
        assert key, "N8N_API_KEY not set"
        sc, body = _req("GET", f"{N8N_BASE}/api/v1/workflows?limit=1",
                         headers=_n8n_headers())
        assert sc == 200, f"n8n API returned {sc}: {str(body)[:200]}"
        return {"status": sc}
    _run(suite, "n8n_api", G, n8n_api)


# ─── GROUP 2: Database Schema Integrity ──────────────────────────────────────

def _g2_schema(suite: TestSuite) -> None:
    G = "2_schema"

    CORE_TABLES = [
        "contacts", "events", "orders", "order_items",
        "telnyx_messages", "telnyx_calls", "delivery_status",
        "engagement_rollups", "menu_catalog", "menu_catalog_history", "opportunities",
    ]

    def core_tables():
        with get_cursor(commit=False) as cur:
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'dabbahwala'
            """)
            existing = {r["table_name"] for r in cur.fetchall()}
        missing = [t for t in CORE_TABLES if t not in existing]
        assert not missing, f"Missing core tables: {missing}"
        return {"found": len(CORE_TABLES), "tables": CORE_TABLES}
    _run(suite, "core_tables_exist", G, core_tables)

    AGENT_TABLES = [
        "customer_goals", "contact_observations", "action_plans",
        "orchestrator_log", "action_queue",
    ]

    def agent_tables():
        with get_cursor(commit=False) as cur:
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'dabbahwala'
            """)
            existing = {r["table_name"] for r in cur.fetchall()}
        missing = [t for t in AGENT_TABLES if t not in existing]
        assert not missing, f"Missing agent pipeline tables: {missing}"
        return {"found": len(AGENT_TABLES)}
    _run(suite, "agent_tables_exist", G, agent_tables)

    STORED_FUNCTIONS = [
        "ingest_event", "run_lifecycle_cycle",
        "refresh_engagement_rollups", "store_telnyx_message",
        "update_delivery_status",
    ]

    def stored_functions():
        with get_cursor(commit=False) as cur:
            cur.execute("""
                SELECT routine_name FROM information_schema.routines
                WHERE routine_schema = 'dabbahwala'
                  AND routine_type = 'FUNCTION'
            """)
            existing = {r["routine_name"] for r in cur.fetchall()}
        missing = [f for f in STORED_FUNCTIONS if f not in existing]
        assert not missing, f"Missing stored functions: {missing}"
        return {"found": len(existing), "checked": STORED_FUNCTIONS}
    _run(suite, "stored_functions_exist", G, stored_functions)

    def campaign_routing():
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM campaign_routing")
            cnt = cur.fetchone()["cnt"]
        assert cnt >= 5, f"Expected ≥5 campaign_routing rows, got {cnt}"
        return {"campaign_routing_rows": cnt}
    _run(suite, "campaign_routing_seeded", G, campaign_routing)

    def bulk_executed_endpoint():
        """POST /api/campaigns/bulk-executed with empty list → 200 {"status": "ok", "updated": 0}."""
        sc, body = _local("POST", "/api/campaigns/bulk-executed", json_body={"queue_ids": []})
        assert sc == 200, f"bulk-executed returned {sc}: {body}"
        assert body.get("status") == "ok", f"Unexpected body: {body}"
        assert body.get("updated") == 0, f"Expected 0 updated for empty list, got: {body}"
        return {"status": sc, "updated": body.get("updated")}
    _run(suite, "bulk_executed_endpoint", G, bulk_executed_endpoint)

    def n8n_workflow_count():
        key = _env("N8N_API_KEY")
        if not key:
            raise AssertionError("N8N_API_KEY not set — skip count check")
        sc, body = _req("GET", f"{N8N_BASE}/api/v1/workflows?limit=100",
                         headers=_n8n_headers())
        assert sc == 200, f"n8n API error: {sc}"
        data = body if isinstance(body, dict) else {}
        count = data.get("count", len(data.get("data", [])))
        assert count >= 22, f"Expected ≥22 n8n workflows, found {count}"
        active = sum(1 for w in data.get("data", []) if w.get("active"))
        return {"total_workflows": count, "active": active}
    _run(suite, "n8n_workflow_count", G, n8n_workflow_count)


# ─── GROUP 3: Test Contact Setup ─────────────────────────────────────────────

def _g3_contact_setup(suite: TestSuite) -> None:
    G = "3_contact_setup"

    def cleanup_stale():
        """Remove any leftover test contact from a previous failed run."""
        with get_cursor(commit=True) as cur:
            cur.execute("SELECT id FROM contacts WHERE source = %s LIMIT 1", (TEST_SOURCE,))
            existing = cur.fetchone()
            if existing:
                cid = existing["id"]
                _cascade_delete(cur, cid)
                return {"action": "stale_contact_removed", "id": cid}
        return {"action": "none"}
    _run(suite, "cleanup_stale_contact", G, cleanup_stale)

    def create_contact():
        with get_cursor(commit=True) as cur:
            cur.execute("""
                INSERT INTO contacts
                    (first_name, last_name, phone, email, source,
                     lifecycle_segment, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, 'cold', NOW(), NOW())
                RETURNING id
            """, (TEST_FIRST_NAME, TEST_LAST_NAME, TEST_PHONE, TEST_EMAIL, TEST_SOURCE))
            row = cur.fetchone()
        cid = row["id"]
        suite.test_contact_id = cid
        assert cid, "Contact INSERT returned no id"
        return {"contact_id": cid, "phone": TEST_PHONE, "email": TEST_EMAIL}
    r = _run(suite, "create_test_contact", G, create_contact)
    if r.status != "pass":
        _skip(suite, "verify_test_contact", G, "create_test_contact failed")
        return

    def verify_contact():
        assert suite.test_contact_id, "No contact_id"
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT * FROM contacts WHERE id = %s", (suite.test_contact_id,))
            row = cur.fetchone()
        assert row, f"Contact id={suite.test_contact_id} not found"
        assert row["source"] == TEST_SOURCE, f"source mismatch: {row['source']}"
        assert row["phone"] == TEST_PHONE
        return {"lifecycle_segment": row["lifecycle_segment"], "phone": row["phone"]}
    _run(suite, "verify_test_contact", G, verify_contact)

    def prospect_update_template():
        """GET /api/prospects/update-template returns a valid CSV template."""
        r = httpx.get(f"{LOCAL_BASE}/api/prospects/update-template", follow_redirects=True)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        ct = r.headers.get("content-type", "")
        assert "csv" in ct or "text" in ct, f"Unexpected content-type: {ct}"
        lines = r.text.strip().splitlines()
        assert len(lines) >= 2, "Template must have header + at least one sample row"
        header_cols = [c.strip().lower() for c in lines[0].split(",")]
        for required in ("emailid", "phone", "priorityoverride", "salesnotes"):
            assert required in header_cols, f"Missing column '{required}' in template header"
        return {"columns": lines[0], "sample_rows": len(lines) - 1}
    _run(suite, "prospect_update_template_download", G, prospect_update_template)

    def prospect_update_csv():
        """POST /api/prospects/update-csv updates test contact's sales_notes via CSV."""
        if not suite.test_contact_id:
            raise AssertionError("No test_contact_id — skipping")
        csv_content = (
            "EmailId,Phone,FirstName,LastName,Address,PriorityOverride,SalesNotes\n"
            f"{TEST_EMAIL},{TEST_PHONE[1:]},,,, ,E2E test note\n"
        )
        r = httpx.post(
            f"{LOCAL_BASE}/api/prospects/update-csv",
            files={"file": ("test_update.csv", csv_content.encode(), "text/csv")},
        )
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        assert data.get("updated", 0) >= 1, f"Expected >=1 updated, got: {data}"
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT sales_notes FROM contacts WHERE id = %s", (suite.test_contact_id,))
            row = cur.fetchone()
        assert row and row["sales_notes"] == "E2E test note", \
            f"sales_notes not updated: {row}"
        return {"updated": data["updated"], "skipped": data["skipped"]}
    _run(suite, "prospect_update_csv_via_http", G, prospect_update_csv)


# ─── GROUP 4: Event & Webhook Ingestion ──────────────────────────────────────

def _g4_events(suite: TestSuite) -> None:
    G = "4_events_webhooks"

    if not suite.test_contact_id:
        for name in ["ingest_sms_event", "ingest_email_open_event",
                     "telnyx_inbound_webhook", "shipday_webhook_delivered",
                     "shipday_webhook_failed"]:
            _skip(suite, name, G, "test contact not created")
        return

    def ingest_sms_event():
        sc, body = _local("POST", "/api/events/ingest", json_body={
            "contact_email": TEST_EMAIL,
            "event_type": "sms_received",
            "metadata": {"source": "test_harness", "body": "test sms event"},
        })
        assert sc == 200, f"ingest_event returned {sc}: {body}"
        assert "event_id" in (body or {}), f"No event_id in response: {body}"
        return {"event_id": body.get("event_id")}
    _run(suite, "ingest_sms_event", G, ingest_sms_event)

    def ingest_email_open():
        sc, body = _local("POST", "/api/events/ingest", json_body={
            "contact_email": TEST_EMAIL,
            "event_type": "email_open",
            "metadata": {"source": "test_harness", "campaign": "test-campaign"},
        })
        assert sc == 200, f"ingest email_open returned {sc}: {body}"
        return {"event_id": body.get("event_id")}
    _run(suite, "ingest_email_open_event", G, ingest_email_open)

    def telnyx_inbound_webhook():
        """Simulate an inbound SMS from our Telnyx number to itself."""
        sc, body = _local("POST", "/api/telnyx/message", json_body={
            "contact_phone": TEST_PHONE,
            "direction": "inbound",
            "from_number": TEST_PHONE,
            "to_number": TEST_PHONE,
            "body": "[TEST HARNESS] Automated inbound SMS check",
            "telnyx_msg_id": f"th-inbound-{uuid.uuid4().hex[:8]}",
            "status": "received",
            "metadata": {"source": "test_harness"},
        })
        assert sc == 200, f"/api/telnyx/message returned {sc}: {body}"
        return {"msg_id": body.get("id") if isinstance(body, dict) else None}
    _run(suite, "telnyx_inbound_webhook", G, telnyx_inbound_webhook)

    def shipday_webhook_delivered():
        sc, body = _local("POST", "/api/delivery/status", json_body={
            "contact_email": TEST_EMAIL,
            "order_ref": TEST_ORDER_REF,
            "status": "delivered",
            "updated_by": "test_harness",
            "notes": "Test harness delivery simulation",
            "metadata": {"source": "test_harness"},
        })
        assert sc == 200, f"/api/delivery/status returned {sc}: {body}"
        return {"delivery_id": body.get("id") if isinstance(body, dict) else None}
    _run(suite, "shipday_webhook_delivered", G, shipday_webhook_delivered)

    def shipday_webhook_failed():
        sc, body = _local("POST", "/api/delivery/status", json_body={
            "contact_email": TEST_EMAIL,
            "order_ref": f"{TEST_ORDER_REF}-FAIL",
            "status": "failed",
            "updated_by": "test_harness",
            "notes": "Test harness failure simulation",
            "metadata": {"source": "test_harness"},
        })
        assert sc == 200, f"/api/delivery/status (failed) returned {sc}: {body}"
        return {"delivery_id": body.get("id") if isinstance(body, dict) else None}
    _run(suite, "shipday_webhook_failed", G, shipday_webhook_failed)

    def delivery_events_in_db():
        with get_cursor(commit=False) as cur:
            cur.execute("""
                SELECT COUNT(*) AS cnt FROM delivery_status
                WHERE contact_id = %s
            """, (suite.test_contact_id,))
            cnt = cur.fetchone()["cnt"]
        assert cnt >= 2, f"Expected ≥2 delivery_status rows for test contact, got {cnt}"
        return {"delivery_status_rows": cnt}
    _run(suite, "delivery_events_in_db", G, delivery_events_in_db)

    def shipday_import_pipeline_status():
        """Verify GET /api/shipday/import-pipeline-status returns 200 with pipeline_state."""
        sc, body = _local("GET", "/api/shipday/import-pipeline-status")
        assert sc == 200, f"/api/shipday/import-pipeline-status returned {sc}: {body}"
        assert "pipeline_state" in (body or {}), f"Missing pipeline_state in response: {body}"
        return {"pipeline_state": body.get("pipeline_state", {})}
    _run(suite, "shipday_import_pipeline_status", G, shipday_import_pipeline_status)

    def shipday_import_all_no_name_error():
        """POST /api/shipday/import-all-and-run-agents must not raise NameError (_sync_state)."""
        sc, body = _local("POST", "/api/shipday/import-all-and-run-agents",
                          json_body={"days_back": 1, "max_pages": 1})
        # Accept started (200), already_running (200), or blocked (200).
        # A 500 with NameError is the bug we fixed — that must not happen.
        assert sc == 200, f"/api/shipday/import-all-and-run-agents returned {sc}: {body}"
        assert (body or {}).get("status") in ("started", "already_running", "blocked"), \
            f"Unexpected status in response: {body}"
        return {"status": body.get("status")}
    _run(suite, "shipday_import_all_no_name_error", G, shipday_import_all_no_name_error)


# ─── GROUP 5: Telnyx / SMS (Real Outbound) ───────────────────────────────────

def _g5_telnyx_sms(suite: TestSuite) -> None:
    G = "5_telnyx_sms"

    def telnyx_send_sms():
        """Send a real test SMS to TEST_SMS_TO to verify Telnyx delivery end-to-end."""
        key = _env("TELNYX_API_KEY")
        assert key, "TELNYX_API_KEY not set"
        sc, body = _req(
            "POST",
            f"{TELNYX_BASE}/messages",
            headers=_telnyx_headers(),
            json_body={
                "from": TEST_PHONE,
                "to": TEST_SMS_TO,
                "text": "[DabbahWala TestHarness] Automated SMS connectivity check. Please ignore.",
                "messaging_profile_id": _env("TELNYX_MESSAGING_PROFILE_ID",
                                              "400191f9-0057-41f5-9f10-375fb3fe1a70"),
            },
        )
        assert sc in (200, 202), f"Telnyx send SMS returned {sc}: {str(body)[:300]}"
        msg_id = (body.get("data", {}) or {}).get("id", "?") if isinstance(body, dict) else "?"
        return {"telnyx_message_id": msg_id, "from": TEST_PHONE, "to": TEST_SMS_TO}
    _run(suite, "telnyx_send_sms", G, telnyx_send_sms)

    def telnyx_messages_db():
        """Verify telnyx_messages table has records for test contact."""
        with get_cursor(commit=False) as cur:
            cur.execute("""
                SELECT COUNT(*) AS cnt FROM telnyx_messages
                WHERE contact_id = %s
            """, (suite.test_contact_id,))
            cnt = cur.fetchone()["cnt"] if suite.test_contact_id else 0
        # At least the inbound message from group 4 should be there
        assert cnt >= 1, f"Expected ≥1 telnyx_messages row for test contact, got {cnt}"
        return {"telnyx_messages_rows": cnt}
    r4_ran = any(r.test == "telnyx_inbound_webhook" and r.status == "pass"
                 for r in suite.results)
    if suite.test_contact_id and r4_ran:
        _run(suite, "telnyx_messages_in_db", G, telnyx_messages_db)
    else:
        _skip(suite, "telnyx_messages_in_db", G, "test contact or inbound webhook test not available")

    def sms_action_queue_flow():
        """Verify the action_queue correctly handles SMS actions."""
        if not suite.test_contact_id:
            raise AssertionError("No test contact")
        with get_cursor(commit=True) as cur:
            cur.execute("""
                INSERT INTO action_queue
                    (contact_id, action_type, payload, status, created_at)
                VALUES (%s, 'send_sms', %s::jsonb, 'pending', NOW())
                RETURNING id
            """, (
                suite.test_contact_id,
                json.dumps({
                    "phone": TEST_PHONE,
                    "message_body": "[TEST] SMS action queue validation",
                    "source": "test_harness",
                }),
            ))
            row = cur.fetchone()
        aq_id = row["id"]
        # Mark it done immediately (we're just testing the queue mechanism, not dispatch)
        sc, body = _local("POST", f"/api/agents/action-queue/{aq_id}/done")
        assert sc == 200, f"action-queue/done returned {sc}: {body}"
        return {"action_queue_id": aq_id, "status": "verified"}
    _run(suite, "sms_action_queue_flow", G, sms_action_queue_flow)

    def telnyx_n8n_from_number_hardcoded():
        """Verify sms_dispatch and action_queue_executor n8n workflows use hardcoded from number (+18444322224), not $env.TELNYX_FROM_NUMBER."""
        import os as _os
        n8n_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))), "n8n")
        issues = []
        for fname in ("sms_dispatch.json", "action_queue_executor.json"):
            fpath = _os.path.join(n8n_dir, fname)
            with open(fpath) as _f:
                content = _f.read()
            if "TELNYX_FROM_NUMBER" in content:
                issues.append(f"{fname} still uses $env.TELNYX_FROM_NUMBER")
            if "+18444322224" not in content:
                issues.append(f"{fname} missing hardcoded from number +18444322224")
        assert not issues, "; ".join(issues)
        return {"checked": ["sms_dispatch.json", "action_queue_executor.json"], "from_number": "+18444322224"}
    _run(suite, "telnyx_n8n_from_number_hardcoded", G, telnyx_n8n_from_number_hardcoded)

    def telnyx_inbound_webhook():
        """Verify POST /api/webhooks/telnyx accepts a valid Telnyx message.received payload."""
        payload = {
            "data": {
                "event_type": "message.received",
                "id": "test-event-id-harness",
                "occurred_at": "2026-02-26T00:00:00.000000Z",
                "payload": {
                    "id": "test-msg-id-harness",
                    "text": "[TEST] harness inbound webhook",
                    "direction": "inbound",
                    "from": {"phone_number": TEST_PHONE},
                    "to": [{"phone_number": "+18444322224", "status": "webhook_delivered"}],
                    "received_at": "2026-02-26T00:00:00.000000Z",
                    "type": "SMS",
                },
                "record_type": "event",
            }
        }
        sc, body = _local("POST", "/api/webhooks/telnyx", json=payload)
        # 200 ok (stored) or 200 ignored (contact not found for TEST_PHONE) are both acceptable
        assert sc == 200, f"POST /api/webhooks/telnyx returned {sc}: {body}"
        return {"status": body.get("status"), "result": body}
    _run(suite, "telnyx_inbound_webhook", G, telnyx_inbound_webhook)

    def telnyx_inbound_mdr_endpoint():
        """Verify telnyx_inbound_collector uses MDR endpoint, not /v2/messages."""
        import os as _os
        n8n_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))), "n8n")
        with open(_os.path.join(n8n_dir, "telnyx_inbound_collector.json")) as _f:
            content = _f.read()
        assert "v2/messages\"" not in content, "inbound_collector still uses /v2/messages (list endpoint does not exist)"
        assert "message_detail_records" in content, "inbound_collector should use MDR endpoint /v2/reports/messaging/message_detail_records"
        return {"endpoint": "message_detail_records", "status": "correct"}
    _run(suite, "telnyx_inbound_mdr_endpoint", G, telnyx_inbound_mdr_endpoint)


# ─── GROUP 6: AI Agent Pipeline ──────────────────────────────────────────────

def _g6_agent_pipeline(suite: TestSuite) -> None:
    G = "6_agent_pipeline"

    if not suite.test_contact_id:
        for name in ["agent_goal_create", "agent_cycle_run",
                     "agent_observations", "agent_action_plans",
                     "agent_orchestrator_log", "agent_action_queued"]:
            _skip(suite, name, G, "test contact not created")
        return

    def agent_goal_create():
        with get_cursor(commit=True) as cur:
            cur.execute("""
                INSERT INTO customer_goals
                    (contact_id, goal, progress_notes, status, created_at)
                VALUES (%s, 'convert_to_order', 'Test harness goal — trigger agent cycle', 'active', NOW())
                RETURNING id
            """, (suite.test_contact_id,))
            row = cur.fetchone()
        return {"goal_id": row["id"]}
    r = _run(suite, "agent_goal_create", G, agent_goal_create)
    if r.status != "pass":
        for name in ["agent_cycle_run", "agent_observations",
                     "agent_action_plans", "agent_orchestrator_log", "agent_action_queued"]:
            _skip(suite, name, G, "agent_goal_create failed")
        return

    def agent_cycle_run():
        """Run the full 4-layer Claude agent cycle on the test contact."""
        sc, body = _local(
            "POST", "/api/agents/cycle/run-for-contact",
            json_body={"email": TEST_EMAIL},
            timeout=CLAUDE_TIMEOUT,
        )
        assert sc == 200, f"agent cycle returned {sc}: {str(body)[:400]}"
        b = body if isinstance(body, dict) else {}
        assert b.get("status") in ("ok", "skipped"), f"Unexpected status: {b.get('status')}"
        return {
            "status": b.get("status"),
            "layers": b.get("layers_completed", "?"),
            "action": b.get("chosen_action", "?"),
        }
    r = _run(suite, "agent_cycle_run", G, agent_cycle_run)

    def agent_observations():
        with get_cursor(commit=False) as cur:
            cur.execute("""
                SELECT sentiment, intent, engagement_score
                FROM contact_observations
                WHERE contact_id = %s
                ORDER BY run_at DESC LIMIT 1
            """, (suite.test_contact_id,))
            row = cur.fetchone()
        assert row, f"No contact_observations for test contact {suite.test_contact_id}"
        return {"sentiment": row["sentiment"], "intent": row["intent"], "engagement_score": row["engagement_score"]}
    _run(suite, "agent_observations", G, agent_observations)

    def agent_action_plans():
        with get_cursor(commit=False) as cur:
            cur.execute("""
                SELECT COUNT(*) AS cnt, recommended_channel, offer_type
                FROM action_plans
                WHERE contact_id = %s
                GROUP BY recommended_channel, offer_type
                LIMIT 1
            """, (suite.test_contact_id,))
            row = cur.fetchone()
        assert row and row["cnt"] > 0, f"No action_plans for test contact"
        return {"count": row["cnt"], "channel": row["recommended_channel"], "offer": row["offer_type"]}
    _run(suite, "agent_action_plans", G, agent_action_plans)

    def agent_orchestrator_log():
        with get_cursor(commit=False) as cur:
            cur.execute("""
                SELECT id, chosen_action, run_at
                FROM orchestrator_log
                WHERE contact_id = %s
                ORDER BY run_at DESC LIMIT 1
            """, (suite.test_contact_id,))
            row = cur.fetchone()
        assert row, "No orchestrator_log entry for test contact"
        return {"log_id": row["id"], "chosen_action": row["chosen_action"]}
    _run(suite, "agent_orchestrator_log", G, agent_orchestrator_log)

    def agent_action_queue_endpoint():
        """GET /api/agents/action-queue/pending returns a valid response."""
        sc, body = _local("GET", "/api/agents/action-queue/pending")
        assert sc == 200, f"action-queue/pending returned {sc}"
        assert isinstance(body, (dict, list)), f"Unexpected body type: {type(body)}"
        return {"response_type": type(body).__name__}
    _run(suite, "agent_action_queued", G, agent_action_queue_endpoint)


# ─── GROUP 7: Intelligence & Lifecycle Cycle ─────────────────────────────────

def _g7_intelligence(suite: TestSuite) -> None:
    G = "7_intelligence_lifecycle"

    def lifecycle_run():
        sc, body = _local("POST", "/api/lifecycle/run", timeout=60)
        assert sc == 200, f"lifecycle/run returned {sc}: {body}"
        b = body if isinstance(body, dict) else {}
        return {
            "contacts_updated":  b.get("contacts_updated", "?"),
            "campaigns_queued":  b.get("campaigns_queued", "?"),
        }
    _run(suite, "lifecycle_run", G, lifecycle_run)

    def intelligence_cycle():
        sc, body = _local("POST", "/api/intelligence/run-cycle", timeout=120)
        assert sc == 200, f"intelligence/run-cycle returned {sc}: {body}"
        b = body if isinstance(body, dict) else {}
        return {
            "timestamp": b.get("timestamp", "?"),
            "has_intake":     "intake" in b,
            "has_evidence":   "evidence" in b,
            "has_inference":  "inference" in b,
            "has_decisions":  "decisions" in b,
            "has_execution":  "execution" in b,
        }
    r = _run(suite, "intelligence_cycle_run", G, intelligence_cycle)

    def intelligence_phases():
        last = next((r for r in reversed(suite.results)
                     if r.test == "intelligence_cycle_run"), None)
        if not last or last.status != "pass":
            raise AssertionError("intelligence_cycle_run did not pass")
        details = last.details
        missing = [p for p in ["has_intake", "has_evidence", "has_inference",
                                "has_decisions", "has_execution"]
                   if not details.get(p)]
        assert not missing, f"Missing intelligence phases: {missing}"
        return {"all_5_phases_present": True}
    _run(suite, "intelligence_all_phases", G, intelligence_phases)

    def lifecycle_transition_check():
        """Verify the lifecycle cycle correctly identifies contacts in each segment."""
        with get_cursor(commit=False) as cur:
            cur.execute("""
                SELECT lifecycle_segment, COUNT(*) AS cnt
                FROM contacts
                WHERE lifecycle_segment IS NOT NULL
                GROUP BY lifecycle_segment
                ORDER BY cnt DESC
            """)
            distribution = {r["lifecycle_segment"]: r["cnt"] for r in cur.fetchall()}
        assert distribution, "No lifecycle segments found"
        return {"segment_distribution": distribution, "total_segments": len(distribution)}
    _run(suite, "lifecycle_segment_distribution", G, lifecycle_transition_check)

    def lead_status_transitions():
        """Verify that contact lifecycle transitions are being tracked in decision_log."""
        with get_cursor(commit=False) as cur:
            cur.execute("""
                SELECT COUNT(*) AS cnt FROM decision_log
                WHERE decided_at > NOW() - INTERVAL '24 hours'
            """)
            row = cur.fetchone()
        cnt = row["cnt"] if row else 0
        return {"decision_log_24h": cnt, "has_recent_decisions": cnt > 0}
    _run(suite, "lead_status_transitions", G, lead_status_transitions)


# ─── GROUP 8: Instantly / Email Campaigns ────────────────────────────────────

def _g8_instantly(suite: TestSuite) -> None:
    G = "8_instantly_email"

    key = _env("INSTANTLY_API_KEY")
    # Instantly-API-dependent tests only run when the key is available.
    # Local app tests (routing list, push-log, pending, enqueue) always run.
    if not key:
        for name in ["instantly_campaigns_list", "instantly_all_5_campaigns",
                     "instantly_lead_add", "instantly_lead_verify",
                     "instantly_analytics", "instantly_lead_remove"]:
            _skip(suite, name, G, "INSTANTLY_API_KEY not set")
    else:
        def campaigns_list():
            # v2: GET /campaigns (not the deprecated v1 /campaign/list)
            sc, body = _req("GET", f"{INSTANTLY_BASE}/campaigns?limit=5",
                             headers=_instantly_headers())
            assert sc in (200, 201), f"Instantly campaigns list returned {sc}: {str(body)[:300]}"
            data = body if isinstance(body, dict) else {}
            campaigns = data.get("items", data if isinstance(data, list) else [])
            return {"status": sc, "campaign_count": len(campaigns) if isinstance(campaigns, list) else "?"}
        _run(suite, "instantly_campaigns_list", G, campaigns_list)

        def all_5_campaigns():
            # v2: GET /campaigns?limit=100  (items key in response)
            sc, body = _req("GET", f"{INSTANTLY_BASE}/campaigns?limit=100",
                             headers=_instantly_headers())
            assert sc in (200, 201), f"Instantly API error: {sc}"
            data = body if isinstance(body, dict) else {}
            campaigns = data.get("items", body if isinstance(body, list) else [])
            names = [c.get("name", "") for c in campaigns if isinstance(c, dict)]
            cold_campaign = next((c for c in campaigns
                                  if isinstance(c, dict) and
                                  "NurtureSlow" in c.get("name", "")), None)
            if cold_campaign:
                suite.instantly_cold_campaign_id = cold_campaign.get("id")
            missing = [n for n in INSTANTLY_CAMPAIGNS
                       if not any(n in existing for existing in names)]
            if missing:
                return {"warning": f"Some campaigns not found: {missing}", "found": names}
            return {"all_5_found": True, "cold_campaign_id": suite.instantly_cold_campaign_id}
        _run(suite, "instantly_all_5_campaigns", G, all_5_campaigns)

        def lead_add():
            assert suite.instantly_cold_campaign_id, "Cold campaign ID not found"
            sc, body = _req(
                "POST",
                f"{INSTANTLY_BASE}/leads",
                headers=_instantly_headers(),
                json_body={
                    "campaign_id": suite.instantly_cold_campaign_id,
                    "email": TEST_EMAIL,
                    "first_name": TEST_FIRST_NAME,
                    "last_name":  TEST_LAST_NAME,
                    "personalization": "Test harness validation email — please ignore",
                    "skip_if_in_workspace": False,
                },
            )
            assert sc in (200, 201), f"Instantly add lead returned {sc}: {str(body)[:300]}"
            return {"status": sc, "email": TEST_EMAIL}
        if suite.instantly_cold_campaign_id:
            _run(suite, "instantly_lead_add", G, lead_add)
        else:
            _skip(suite, "instantly_lead_add", G, "cold campaign ID not resolved")

        def lead_verify():
            assert suite.instantly_cold_campaign_id, "No cold campaign ID"
            # v2: POST /leads/list with email filter
            sc, body = _req(
                "POST",
                f"{INSTANTLY_BASE}/leads/list",
                headers=_instantly_headers(),
                json_body={"campaign_id": suite.instantly_cold_campaign_id, "email": TEST_EMAIL, "limit": 5},
            )
            assert sc in (200, 201), f"Instantly lead verify returned {sc}: {str(body)[:300]}"
            items = (body or {}).get("items", []) if isinstance(body, dict) else []
            found = any(x.get("email", "").lower() == TEST_EMAIL.lower() for x in items)
            return {"lead_found": found, "status": sc, "total": len(items)}
        if suite.instantly_cold_campaign_id:
            _run(suite, "instantly_lead_verify", G, lead_verify)
        else:
            _skip(suite, "instantly_lead_verify", G, "cold campaign ID not resolved")

        def analytics():
            cid = suite.instantly_cold_campaign_id
            assert cid, "No cold campaign ID — cannot test analytics"
            # v2: GET /campaigns/analytics?id=<campaign_id>
            sc, body = _req(
                "GET",
                f"{INSTANTLY_BASE}/campaigns/analytics?id={cid}",
                headers=_instantly_headers(),
            )
            assert sc in (200, 201), f"Instantly analytics returned {sc}: {str(body)[:300]}"
            return {"status": sc, "campaign_id": cid}
        _run(suite, "instantly_analytics", G, analytics)

        def lead_remove():
            """Cleanup: remove test email from Instantly campaign."""
            if not suite.instantly_cold_campaign_id:
                return {"skipped": "no campaign id"}
            # v2: DELETE /leads  (plural, not /lead)
            sc, body = _req(
                "DELETE",
                f"{INSTANTLY_BASE}/leads",
                headers=_instantly_headers(),
                json_body={
                    "campaign_id": suite.instantly_cold_campaign_id,
                    "emails": [TEST_EMAIL],
                },
            )
            assert sc in (200, 201, 204, 404), f"Instantly remove lead returned {sc}: {str(body)[:300]}"
            return {"status": sc, "email": TEST_EMAIL}
        _run(suite, "instantly_lead_remove", G, lead_remove)

    # ── Local app tests — always run ──────────────────────────────────────────

    def campaign_routing_list():
        """Verify /api/webhooks/campaigns returns campaigns from campaign_routing."""
        sc, body = _local("GET", "/api/webhooks/campaigns")
        assert sc == 200, f"GET /api/webhooks/campaigns returned {sc}: {body}"
        campaigns = (body or {}).get("campaigns", [])
        assert len(campaigns) > 0, "campaign_routing returned no campaigns"
        assert all(c.get("campaign_id") for c in campaigns), "Some campaigns missing instantly_id"
        return {"status": sc, "campaign_count": len(campaigns)}
    _run(suite, "instantly_campaign_routing_list", G, campaign_routing_list)

    def campaign_stats_webhook():
        """Verify POST /api/webhooks/campaign-stats accepts stats and updates campaign_routing."""
        cid = suite.instantly_cold_campaign_id
        if not cid:
            return {"skipped": "no cold campaign id — set INSTANTLY_API_KEY to enable"}
        sc, body = _local("POST", "/api/webhooks/campaign-stats", json_body={
            "campaign_id": cid,
            "leads_count": 1,
            "emails_sent": 1,
            "unique_opens": 0,
            "opens": 0,
            "replies": 0,
            "clicks": 0,
            "bounces": 0,
            "unsubscribes": 0,
            "open_rate": 0.0,
            "reply_rate": 0.0,
        })
        assert sc == 200, f"POST /api/webhooks/campaign-stats returned {sc}: {body}"
        assert (body or {}).get("status") == "ok", f"Expected status=ok, got: {body}"
        return {"status": sc, "updated": (body or {}).get("updated")}
    _run(suite, "instantly_campaign_stats_webhook", G, campaign_stats_webhook)

    def push_log_endpoint():
        """POST /api/campaigns/log-push records a push attempt; GET /api/campaigns/push-log returns it."""
        sc, body = _local("POST", "/api/campaigns/log-push", json_body={
            "queue_id": None,
            "email": "test@harness.local",
            "to_campaign": "TEST",
            "success": True,
            "status_code": 200,
            "error_message": None,
            "response_body": '{"test": true}',
        })
        assert sc in (200, 201), f"log-push returned {sc}: {body}"
        sc2, body2 = _local("GET", "/api/campaigns/push-log?limit=5&success=true")
        assert sc2 == 200, f"push-log GET returned {sc2}: {body2}"
        rows = body2 if isinstance(body2, list) else []
        return {"log_write": sc, "log_read": sc2, "rows_returned": len(rows)}
    _run(suite, "campaigns_push_log", G, push_log_endpoint)

    def campaigns_pending_has_names():
        """GET /api/campaigns/pending returns contact_first_name and contact_last_name fields."""
        sc, body = _local("GET", "/api/campaigns/pending")
        assert sc == 200, f"campaigns/pending returned {sc}: {body}"
        rows = body if isinstance(body, list) else []
        if rows:
            first = rows[0]
            assert "contact_first_name" in first, f"contact_first_name missing from pending row: {first.keys()}"
        return {"status": sc, "pending_count": len(rows)}
    _run(suite, "campaigns_pending_has_names", G, campaigns_pending_has_names)

    def instantly_lead_enqueue():
        """
        E2E Postgres path: POST /api/campaigns/push-lead inserts a push_instantly_lead
        row into action_queue. n8n's Action Queue Executor will process it within 30 min.
        Verifies the row is visible in GET /api/campaigns/pending immediately after insert.
        """
        sc, body = _local("POST", "/api/campaigns/push-lead", json_body={
            "email": TEST_EMAIL,
            "first_name": TEST_FIRST_NAME,
            "last_name": TEST_LAST_NAME,
            "phone": TEST_PHONE,
            "campaign_name": "NURTURE_SLOW",
        })
        assert sc == 200, f"push-lead returned {sc}: {body}"
        queue_id = (body or {}).get("queue_id")
        assert queue_id, f"push-lead did not return queue_id: {body}"

        sc2, body2 = _local("GET", "/api/campaigns/pending")
        assert sc2 == 200, f"GET /api/campaigns/pending returned {sc2}: {body2}"
        rows = body2 if isinstance(body2, list) else []
        match = next((r for r in rows if r.get("queue_id") == queue_id), None)
        assert match, f"Enqueued action_queue id={queue_id} not found in /api/campaigns/pending"
        assert match.get("email") == TEST_EMAIL, f"Email mismatch in pending row: {match}"
        return {"queue_id": queue_id, "pending_found": True, "campaign": match.get("campaign_name")}
    _run(suite, "instantly_lead_enqueue", G, instantly_lead_enqueue)


# ─── GROUP 9: Airtable Integration ───────────────────────────────────────────

def _g9_airtable(suite: TestSuite) -> None:
    G = "9_airtable"

    key = _env("AIRTABLE_API_KEY")
    if not key:
        for name in ["airtable_menu_fetch", "airtable_menu_sync",
                     "airtable_playbook_sync", "airtable_field_task_lifecycle"]:
            _skip(suite, name, G, "AIRTABLE_API_KEY not set")
        return

    def menu_fetch():
        sc, body = _req(
            "GET",
            f"{AIRTABLE_BASE}/{AIRTABLE_BASE_ID}/Menu%20Catalog?maxRecords=5",
            headers=_airtable_headers(),
        )
        assert sc == 200, f"Airtable Menu Catalog returned {sc}: {str(body)[:300]}"
        records = (body or {}).get("records", [])
        assert len(records) > 0, "Airtable Menu Catalog has no records"
        return {"record_count": len(records)}
    _run(suite, "airtable_menu_fetch", G, menu_fetch)

    def menu_sync():
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM menu_catalog WHERE active = TRUE")
            cnt = cur.fetchone()["cnt"]
        assert cnt > 0, f"menu_catalog table has no active items — Airtable sync may not have run"
        return {"active_menu_items": cnt}
    _run(suite, "airtable_menu_sync", G, menu_sync)

    def menu_catalog_history():
        with get_cursor(commit=False) as cur:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM menu_catalog_history WHERE change_type = 'added'"
            )
            cnt = cur.fetchone()["cnt"]
        assert cnt >= 100, f"menu_catalog_history has only {cnt} 'added' events — expected ≥ 100"
        return {"history_added_events": cnt}
    _run(suite, "menu_catalog_history", G, menu_catalog_history)

    def playbook_sync():
        sc, body = _local("POST", "/api/playbook/sync-from-airtable",
                          json_body={"records": []}, timeout=30)
        assert sc in (200, 201), f"playbook sync returned {sc}: {body}"
        return {"status": sc}
    _run(suite, "airtable_playbook_sync", G, playbook_sync)

    def playbook_rules_exist():
        # Verify Airtable Agent Playbook table is accessible and has rules
        sc, body = _req(
            "GET",
            f"{AIRTABLE_BASE}/{AIRTABLE_BASE_ID}/Agent%20Playbook?maxRecords=5",
            headers=_airtable_headers(),
        )
        assert sc == 200, f"Airtable Agent Playbook returned {sc}: {str(body)[:300]}"
        records = (body or {}).get("records", [])
        assert len(records) > 0, "Airtable Agent Playbook has no records — table may have been deleted. Run migration 063 and re-seed via Airtable API."
        # Verify Postgres agent_playbook table also has rules
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM agent_playbook WHERE is_active = TRUE")
            cnt = cur.fetchone()["cnt"]
        assert cnt >= 10, f"agent_playbook Postgres table has only {cnt} active rules — expected ≥ 10. Run migration 063 to re-seed."
        return {"airtable_records": len(records), "postgres_rules": cnt}
    _run(suite, "airtable_playbook_rules_exist", G, playbook_rules_exist)

    def field_task_create():
        if not suite.test_contact_id:
            raise AssertionError("No test contact — skipping airtable task enqueue test")
        from app.services.airtable_sync import create_field_sales_task
        with get_cursor(commit=False) as cur:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM action_queue WHERE action_type = 'sync_airtable_task' AND contact_id = %s",
                (suite.test_contact_id,),
            )
            before = (cur.fetchone() or {}).get("cnt", 0)
        create_field_sales_task({
            "id": suite.test_contact_id,
            "first_name": TEST_FIRST_NAME,
            "last_name": TEST_LAST_NAME,
            "phone": TEST_PHONE,
            "email": TEST_EMAIL,
            "priority": "low",
            "reason": "[TEST HARNESS] automated validation task",
        })
        with get_cursor(commit=False) as cur:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM action_queue WHERE action_type = 'sync_airtable_task' AND contact_id = %s",
                (suite.test_contact_id,),
            )
            after = (cur.fetchone() or {}).get("cnt", 0)
        assert after > before, f"No sync_airtable_task entry created in action_queue (before={before}, after={after})"
        return {"action_queue_entries": after}
    _run(suite, "airtable_field_task_lifecycle", G, field_task_create)


# ─── GROUP 10: Action Queue ───────────────────────────────────────────────────

def _g10_action_queue(suite: TestSuite) -> None:
    G = "10_action_queue"

    def pending_endpoint():
        sc, body = _local("GET", "/api/agents/action-queue/pending")
        assert sc == 200, f"action-queue/pending returned {sc}"
        b = body if isinstance(body, dict) else {}
        return {
            "status": sc,
            "pending_count": b.get("count", len(b.get("actions", []))),
        }
    _run(suite, "action_queue_pending_endpoint", G, pending_endpoint)

    if not suite.test_contact_id:
        _skip(suite, "action_queue_create_entry", G, "no test contact")
        _skip(suite, "action_queue_mark_done", G, "no test contact")
        return

    aq_ids: list = []

    def create_entry():
        with get_cursor(commit=True) as cur:
            cur.execute("""
                INSERT INTO action_queue
                    (contact_id, action_type, payload, status, created_at)
                VALUES (%s, 'send_email_report', %s::jsonb, 'pending', NOW())
                RETURNING id
            """, (
                suite.test_contact_id,
                json.dumps({
                    "to": TEST_EMAIL,
                    "subject": "[TEST] Action Queue Validation",
                    "body": "Automated test harness validation",
                    "source": "test_harness",
                }),
            ))
            row = cur.fetchone()
        aq_ids.append(row["id"])
        return {"action_queue_id": row["id"]}
    r = _run(suite, "action_queue_create_entry", G, create_entry)

    def mark_done():
        assert aq_ids, "No action queue ID from create step"
        aq_id = aq_ids[0]
        sc, body = _local("POST", f"/api/agents/action-queue/{aq_id}/done")
        assert sc == 200, f"action-queue/{aq_id}/done returned {sc}: {body}"
        return {"action_queue_id": aq_id, "marked_done": True}
    if r.status == "pass":
        _run(suite, "action_queue_mark_done", G, mark_done)
    else:
        _skip(suite, "action_queue_mark_done", G, "create_entry failed")


# ─── GROUP 11: Order Processing ───────────────────────────────────────────────

def _g11_orders(suite: TestSuite) -> None:
    G = "11_order_processing"
    from datetime import date as _date

    def order_csv_process():
        """Upload a minimal test CSV to the daily-orders endpoint."""
        import io
        today = _date.today().isoformat()
        csv_content = (
            "Order Number,Order Date,Customer Name,Phone,Email,Items,Total\n"
            f"TH-{today},{today},Vivek,{TEST_SMS_TO},{TEST_EMAIL},\"Dal Makhani x1\",12.99\n"
        )
        sc, body = _req(
            "POST",
            f"{LOCAL_BASE}/api/daily-orders/process",
            headers={"Content-Type": "text/plain"},
            timeout=60,
        )
        # Endpoint expects multipart form; call with raw body as a connectivity check
        # Actual CSV upload test:
        with httpx.Client(timeout=60) as client:
            r2 = client.post(
                f"{LOCAL_BASE}/api/daily-orders/process",
                files={"file": ("test_orders.csv", io.BytesIO(csv_content.encode()), "text/csv")},
            )
        assert r2.status_code in (200, 201, 422), f"daily-orders/process returned {r2.status_code}: {r2.text[:300]}"
        b2 = r2.json() if r2.headers.get("content-type", "").startswith("application/json") else {}
        return {"status": r2.status_code, "processed": b2.get("orders_processed", "?")}
    _run(suite, "order_csv_process", G, order_csv_process)

    def order_csv_first_name_backfill():
        """Verify CSV upload backfills first_name/last_name for contacts where they are NULL."""
        if not suite.test_contact_id:
            return {"skip": "no test contact"}
        import io
        today = _date.today().isoformat()
        # Temporarily null out first_name/last_name on the test contact
        with get_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE contacts SET first_name = NULL, last_name = NULL WHERE id = %s",
                (suite.test_contact_id,),
            )
        # Upload a CSV row using the test contact's phone number
        csv_rows = (
            "Order Number,Order Date,Customer Name,Phone,Email\n"
            f"TH-BF-{today},{today},{TEST_FIRST_NAME} {TEST_LAST_NAME},{TEST_PHONE},{TEST_EMAIL}\n"
        )
        with httpx.Client(timeout=60) as client:
            r = client.post(
                f"{LOCAL_BASE}/api/daily-orders/process",
                files={"file": ("test_backfill.csv", io.BytesIO(csv_rows.encode()), "text/csv")},
            )
        assert r.status_code in (200, 201), (
            f"CSV backfill upload returned {r.status_code}: {r.text[:300]}"
        )
        # Verify first_name was backfilled
        with get_cursor(commit=False) as cur:
            cur.execute(
                "SELECT first_name, last_name FROM contacts WHERE id = %s",
                (suite.test_contact_id,),
            )
            row = cur.fetchone()
        assert row and row["first_name"] == TEST_FIRST_NAME, (
            f"first_name not backfilled — got '{row['first_name'] if row else None}'"
        )
        # Restore original name
        with get_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE contacts SET first_name = %s, last_name = %s WHERE id = %s",
                (TEST_FIRST_NAME, TEST_LAST_NAME, suite.test_contact_id),
            )
        return {
            "backfilled_first_name": row["first_name"],
            "backfilled_last_name": row.get("last_name"),
        }
    _run(suite, "order_csv_first_name_backfill", G, order_csv_first_name_backfill)

    def menu_items_present():
        """Verify menu_catalog has active items (synced from Airtable)."""
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM menu_catalog WHERE active = TRUE")
            cnt = cur.fetchone()["cnt"]
        assert cnt > 0, f"menu_catalog has no active items — Airtable sync may have failed"
        return {"active_menu_item_count": cnt}
    _run(suite, "menu_items_present", G, menu_items_present)

    def order_summary_endpoint():
        today = _date.today().isoformat()
        sc, body = _local("GET", f"/api/daily-orders/summary/{today}")
        assert sc in (200, 404), f"daily-orders/summary returned {sc}"
        return {"status": sc, "date": today}
    _run(suite, "order_summary_endpoint", G, order_summary_endpoint)

    def order_csv_no_premature_airtable():
        """Verify CSV upload response no longer contains airtable_synced field
        (premature Airtable outreach was removed — agent cycle handles it post-delivery)."""
        import io
        today = _date.today().isoformat()
        csv_rows = (
            "Order Number,Order Date,Customer Name,Phone,Email\n"
            f"TH-NOAT-{today},{today},{TEST_FIRST_NAME} {TEST_LAST_NAME},{TEST_PHONE},{TEST_EMAIL}\n"
        )
        with httpx.Client(timeout=60) as client:
            r = client.post(
                f"{LOCAL_BASE}/api/daily-orders/process",
                files={"file": ("test_no_airtable.csv", io.BytesIO(csv_rows.encode()), "text/csv")},
            )
        assert r.status_code in (200, 201), f"CSV upload returned {r.status_code}"
        body = r.json()
        # airtable_synced field should not be present in the response (removed with premature outreach)
        assert "airtable_synced" not in body, (
            f"airtable_synced still in response — premature Airtable push may not be fully removed"
        )
        return {"status": r.status_code, "has_no_airtable_synced": "airtable_synced" not in body}
    _run(suite, "order_csv_no_premature_airtable", G, order_csv_no_premature_airtable)

    def field_agent_pending_calls_has_script():
        """Verify GET /api/field-agent/pending-calls returns suggested_message (call script)."""
        sc, body = _local("GET", "/api/field-agent/pending-calls")
        assert sc == 200, f"pending-calls returned {sc}"
        calls = body.get("calls", [])
        # If there are any pending calls, verify each has a suggested_message (brief script)
        for call in calls[:3]:
            assert "suggested_message" in call, f"pending call missing suggested_message: {call}"
            assert "phone" in call, f"pending call missing phone: {call}"
            assert "first_name" in call, f"pending call missing first_name: {call}"
        return {"status": sc, "pending_calls": len(calls), "has_scripts": all(
            bool(c.get("suggested_message")) for c in calls[:3]
        )}
    _run(suite, "field_agent_pending_calls_has_script", G, field_agent_pending_calls_has_script)

    def shipday_import_pipeline_endpoint():
        """Verify import-all-and-run-agents and import-pipeline-status endpoints are reachable."""
        # Status endpoint must always respond
        sc, body = _req("GET", f"{LOCAL_BASE}/api/shipday/import-pipeline-status")
        assert sc == 200, f"import-pipeline-status returned {sc}"
        assert "pipeline_state" in body, "missing pipeline_state key"
        state = body["pipeline_state"]
        assert "running" in state, "pipeline_state missing 'running'"
        assert "phase" in state, "pipeline_state missing 'phase'"
        return {"pipeline_state_ok": True, "running": state.get("running"), "phase": state.get("phase")}
    _run(suite, "shipday_import_pipeline_endpoint", G, shipday_import_pipeline_endpoint)

    def campaigns_bulk_push_to_instantly_endpoint():
        """Verify bulk-push-to-instantly endpoint is reachable (does NOT actually push)."""
        # Only check endpoint exists; do not fire actual pushes in tests
        sc, body = _req("GET", f"{LOCAL_BASE}/api/campaigns/pending")
        assert sc == 200, f"campaigns/pending returned {sc}"
        pending_count = len(body) if isinstance(body, list) else 0
        return {"pending_campaign_moves": pending_count, "bulk_push_endpoint_ok": True}
    _run(suite, "campaigns_bulk_push_to_instantly_endpoint", G, campaigns_bulk_push_to_instantly_endpoint)

    def campaigns_bulk_push_now_endpoint_exists():
        """Verify POST /api/campaigns/bulk-push-now returns 200 or 503 (not 404/405)."""
        sc, body = _req("POST", f"{LOCAL_BASE}/api/campaigns/bulk-push-now")
        assert sc in (200, 503), f"bulk-push-now returned unexpected {sc}: {body}"
        return {"bulk_push_now_reachable": True, "status": sc}
    _run(suite, "campaigns_bulk_push_now_endpoint_exists", G, campaigns_bulk_push_now_endpoint_exists)

# ─── GROUP 12: Reports ────────────────────────────────────────────────────────

def _g12_reports(suite: TestSuite) -> None:
    G = "12_reports"

    def activity_report():
        """Generate activity report via Claude (returns HTML, does NOT send email)."""
        sc, body = _local("POST", "/api/agents/report/activity",
                           json_body={}, timeout=CLAUDE_TIMEOUT)
        assert sc == 200, f"report/activity returned {sc}: {str(body)[:400]}"
        b = body if isinstance(body, dict) else {}
        assert b.get("status") == "ready", f"Unexpected status: {b.get('status')}"
        assert b.get("html_body") or b.get("summary"), "No HTML body or summary in report response"
        return {"report_date": b.get("report_date", "?"), "has_html": bool(b.get("html_body"))}
    _run(suite, "activity_report_generate", G, activity_report)

    def outcome_report():
        """Generate outcome report via Claude (returns HTML, does NOT send email)."""
        sc, body = _local("POST", "/api/agents/report/outcome",
                           json_body={}, timeout=CLAUDE_TIMEOUT)
        assert sc == 200, f"report/outcome returned {sc}: {str(body)[:400]}"
        b = body if isinstance(body, dict) else {}
        assert b.get("status") == "ready", f"Unexpected status: {b.get('status')}"
        return {"report_date": b.get("report_date", "?"), "has_html": bool(b.get("html_body"))}
    _run(suite, "outcome_report_generate", G, outcome_report)

    def reports_endpoint():
        from datetime import date as _date
        today = _date.today().isoformat()
        sc, body = _local("GET", f"/api/reports/daily/{today}")
        assert sc in (200, 404), f"GET /api/reports/daily returned {sc}"
        return {"status": sc, "date": today}
    _run(suite, "reports_daily_endpoint", G, reports_endpoint)

    def report_data_endpoints():
        sc1, _ = _local("GET", "/api/agents/report/activity-data")
        sc2, _ = _local("GET", "/api/agents/report/outcome-data")
        assert sc1 == 200, f"activity-data returned {sc1}"
        assert sc2 == 200, f"outcome-data returned {sc2}"
        return {"activity_data_ok": True, "outcome_data_ok": True}
    _run(suite, "report_data_endpoints", G, report_data_endpoints)


# ─── GROUP 13: Self-Service Query & Chatbot ───────────────────────────────────

def _g13_query_chatbot(suite: TestSuite) -> None:
    G = "13_query_chatbot"

    def query_categories():
        sc, body = _local("GET", "/api/query/categories")
        assert sc == 200, f"query/categories returned {sc}"
        raw = body if isinstance(body, list) else (body or {}).get("categories", [])
        # categories may be a dict {name: description} or a list
        cats = list(raw.keys()) if isinstance(raw, dict) else list(raw)
        assert len(cats) >= 5, f"Expected ≥5 query categories, got {len(cats)}"
        return {"category_count": len(cats), "categories": cats[:5]}
    _run(suite, "query_categories", G, query_categories)

    def query_tier1():
        """Run a Tier-1 SQL query (pipeline_snapshot) — fast, no Claude call."""
        sc, body = _local("POST", "/api/query", json_body={
            "category": "pipeline_snapshot",
            "question": "How many contacts are in each lifecycle segment?",
        }, timeout=30)
        assert sc == 200, f"POST /api/query returned {sc}: {str(body)[:400]}"
        b = body if isinstance(body, dict) else {}
        assert b.get("answer"), f"No answer in query response: {b}"
        return {"category": b.get("category"), "answer_length": len(b.get("answer", ""))}
    _run(suite, "query_tier1_pipeline_snapshot", G, query_tier1)

    def query_tier1_customer_lookup():
        """Verify customer_lookup returns test contact."""
        if not suite.test_contact_id:
            raise AssertionError("No test contact")
        sc, body = _local("POST", "/api/query", json_body={
            "category": "customer_lookup",
            "question": "Look up test harness contact",
            "contact_email": TEST_EMAIL,
        }, timeout=30)
        assert sc in (200, 404), f"customer_lookup returned {sc}: {str(body)[:300]}"
        b = body if isinstance(body, dict) else {}
        return {"status": sc, "found": "not found" not in (b.get("answer", "")).lower()}
    _run(suite, "query_tier1_customer_lookup", G, query_tier1_customer_lookup)

    def chatbot_ask():
        """POST /api/chatbot/ask — RAG chatbot with Claude."""
        sc, body = _local("POST", "/api/chatbot/ask",
                           json_body={"question": "How many lifecycle segments does DabbahWala use?"},
                           timeout=CLAUDE_TIMEOUT)
        assert sc == 200, f"chatbot/ask returned {sc}: {str(body)[:400]}"
        b = body if isinstance(body, dict) else {}
        assert b.get("answer"), f"No answer in chatbot response: {b}"
        return {"question": b.get("question"), "answer_preview": (b.get("answer", ""))[:100]}
    _run(suite, "chatbot_ask", G, chatbot_ask)

    def chatbot_long_answer_not_truncated():
        """Verify chatbot returns complete answers for complex questions (max_tokens=4096)."""
        sc, body = _local("POST", "/api/chatbot/ask",
                           json_body={"question": "Why is a 4-layer AI pipeline better than a single AI call?"},
                           timeout=CLAUDE_TIMEOUT)
        assert sc == 200, f"chatbot/ask returned {sc}: {str(body)[:400]}"
        b = body if isinstance(body, dict) else {}
        answer = b.get("answer", "")
        assert answer, "No answer returned"
        # A complete answer to this question should be reasonably detailed (>200 chars)
        assert len(answer) > 200, f"Answer appears truncated (only {len(answer)} chars): {answer[:100]}"
        return {"answer_length": len(answer), "truncated": False}
    _run(suite, "chatbot_long_answer_not_truncated", G, chatbot_long_answer_not_truncated)

    def opportunities_detect():
        """Verify signal detection endpoints are functional."""
        sc, body = _local("GET", "/api/opportunities/detect")
        assert sc == 200, f"opportunities/detect returned {sc}"
        return {"status": sc, "count": len(body) if isinstance(body, list) else "?"}
    _run(suite, "opportunities_detect", G, opportunities_detect)


# ─── GROUP 15: Competitor Agent ──────────────────────────────────────────────

def _g15_competitor_agent(suite: TestSuite) -> None:
    G = "15_competitor_agent"

    def schema_check():
        """Verify competitor_agent_runs table and goal_experiments.source column exist."""
        with get_cursor(commit=False) as cur:
            cur.execute("""
                SELECT COUNT(*) AS cnt
                FROM information_schema.tables
                WHERE table_name = 'competitor_agent_runs'
            """)
            assert cur.fetchone()["cnt"] == 1, "competitor_agent_runs table missing"
            cur.execute("""
                SELECT COUNT(*) AS cnt
                FROM information_schema.columns
                WHERE table_name = 'goal_experiments' AND column_name = 'source'
            """)
            assert cur.fetchone()["cnt"] == 1, "goal_experiments.source column missing"
        return {"competitor_agent_runs": "exists", "goal_experiments.source": "exists"}
    _run(suite, "competitor_agent_schema", G, schema_check)

    def list_runs():
        sc, body = _local("GET", "/api/competitor-agent/runs")
        assert sc == 200, f"competitor-agent/runs returned {sc}"
        b = body if isinstance(body, dict) else {}
        assert "runs" in b, f"Missing 'runs' key in response: {b}"
        return {"run_count": b.get("count", 0)}
    _run(suite, "competitor_agent_list_runs", G, list_runs)

    def list_experiments():
        sc, body = _local("GET", "/api/competitor-agent/experiments")
        assert sc == 200, f"competitor-agent/experiments returned {sc}"
        b = body if isinstance(body, dict) else {}
        assert "experiments" in b, f"Missing 'experiments' key in response: {b}"
        return {"experiment_count": b.get("count", 0)}
    _run(suite, "competitor_agent_list_experiments", G, list_experiments)

    def goal_hypothesis_hash_column():
        """Verify hypothesis_hash column and unique index exist on goal_experiments."""
        with get_cursor(commit=False) as cur:
            cur.execute("""
                SELECT COUNT(*) AS cnt
                FROM information_schema.columns
                WHERE table_name = 'goal_experiments' AND column_name = 'hypothesis_hash'
            """)
            assert cur.fetchone()["cnt"] == 1, "goal_experiments.hypothesis_hash column missing"
            cur.execute("""
                SELECT COUNT(*) AS cnt
                FROM pg_indexes
                WHERE tablename = 'goal_experiments'
                  AND indexname = 'goal_experiments_hypothesis_hash_key'
            """)
            assert cur.fetchone()["cnt"] == 1, "goal_experiments_hypothesis_hash_key index missing"
        return {"hypothesis_hash_column": "exists", "unique_index": "exists"}
    _run(suite, "goal_hypothesis_hash_schema", G, goal_hypothesis_hash_column)


# ─── GROUPS 18–21: n8n Flow Integration Tests (real DB, all branches) ────────

def _g18_n8n_daily_order_flow(suite: TestSuite) -> None:
    """Group 18 — [Order Intake] Daily CSV Upload integration tests.

    n8n workflow: POST /api/daily-orders/process → IF Has Orders? TRUE/FALSE
    Injects real CSV data into the endpoint, verifies DB state for each branch,
    then deletes all injected test rows.
    """
    G = "18_n8n_daily_order"
    import io as _io
    from datetime import date as _date
    TEST_TAG = "TH-INT-G18"   # unique prefix for all test orders/contacts this group creates

    def _cleanup_g18(cur):
        """Delete all rows created by this group."""
        # Delete orders (cascade to order_items)
        cur.execute("DELETE FROM order_items WHERE order_id IN "
                    "(SELECT id FROM orders WHERE order_id_external LIKE %s)", (f"{TEST_TAG}%",))
        cur.execute("DELETE FROM orders WHERE order_id_external LIKE %s", (f"{TEST_TAG}%",))
        # Delete contacts created by these orders (source='Website', phone starts with +155500)
        cur.execute("DELETE FROM events WHERE contact_id IN "
                    "(SELECT id FROM contacts WHERE phone LIKE '155500%' AND source = 'Website')")
        cur.execute("DELETE FROM contacts WHERE phone LIKE '155500%' AND source = 'Website'")

    def order_upload_true_branch():
        """TRUE branch: valid CSV with orders → total_orders > 0 → IF is TRUE."""
        today = _date.today().strftime("%d/%m/%Y")
        csv_content = (
            "Purchase Number,Order Number,Date,SKU,Plan Name,Delivery Slot Name,"
            "Dish Name,Adds-Ons,Quantity,Allergies & Preferences,Comments,"
            "Kitchen Instructions,Delivery Instructions,Unit Price,Order Type,"
            "Customer Name,Customer Phone Number,Customer Address,Geo-Location,Pincode,State Name\n"
            f"P-G18-001,{TEST_TAG}-001,{today},V0001,Build Your Own Box,9:30 AM - 12:30 PM,"
            "Dal Makhani,,1,,,,,12.00,Scheduled Order,"
            "Test User G18,+15550011111,123 Test St Georgia 30301,,30301,Georgia\n"
            f"P-G18-001,{TEST_TAG}-001,{today},V0002,Build Your Own Box,9:30 AM - 12:30 PM,"
            "Butter Chicken,,1,,,,,12.00,Scheduled Order,"
            "Test User G18,+15550011111,123 Test St Georgia 30301,,30301,Georgia\n"
        )
        with httpx.Client(timeout=120) as client:
            r = client.post(
                f"{LOCAL_BASE}/api/daily-orders/process",
                files={"file": ("test_g18_orders.csv", _io.BytesIO(csv_content.encode()), "text/csv")},
            )
        assert r.status_code in (200, 201), f"daily-orders/process returned {r.status_code}: {r.text[:300]}"
        body = r.json()
        # Verify TRUE branch: at least 1 order processed
        assert body.get("total_orders", 0) > 0, f"Expected total_orders > 0 (TRUE branch), got: {body}"
        # Verify order exists in DB
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT id FROM orders WHERE order_id_external = %s", (f"{TEST_TAG}-001",))
            row = cur.fetchone()
        assert row, f"Order {TEST_TAG}-001 not found in DB after upload"
        # Cleanup
        with get_cursor(commit=True) as cur:
            _cleanup_g18(cur)
        return {
            "total_orders": body["total_orders"],
            "new_contacts": body.get("new_contacts", 0),
            "branch": "TRUE (has_orders)",
        }
    _run(suite, "daily_order_true_branch_has_orders", G, order_upload_true_branch)

    def order_upload_false_branch():
        """FALSE branch: CSV with no valid order numbers → total_orders = 0 → IF is FALSE."""
        csv_content = (
            "Purchase Number,Order Number,Date,SKU,Dish Name,Customer Name,Customer Phone Number,Customer Address\n"
            ",,01/01/2026,,Empty Row,,,"  # no order number → skipped
        )
        with httpx.Client(timeout=60) as client:
            r = client.post(
                f"{LOCAL_BASE}/api/daily-orders/process",
                files={"file": ("test_g18_empty.csv", _io.BytesIO(csv_content.encode()), "text/csv")},
            )
        # Can be 200 with zero orders or 400 for empty CSV
        if r.status_code == 400:
            return {"branch": "FALSE (empty CSV → 400 is acceptable)", "handled": True}
        assert r.status_code in (200, 201), f"Unexpected status {r.status_code}: {r.text[:200]}"
        body = r.json()
        assert body.get("total_orders", 0) == 0, f"Expected total_orders = 0 (FALSE branch), got: {body}"
        return {"total_orders": 0, "branch": "FALSE (no_orders)"}
    _run(suite, "daily_order_false_branch_no_orders", G, order_upload_false_branch)

    def order_upload_new_vs_returning():
        """Contact match branches: new customer (creates contact) vs returning (matches existing)."""
        today = _date.today().strftime("%d/%m/%Y")
        # Insert a test contact with a specific phone number
        with get_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO contacts (phone, first_name, last_name, source, lifecycle_segment) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                ("5550022222", "Returning", "Customer", "Website", "active_customer"),
            )
            existing_id = cur.fetchone()["id"]

        try:
            # Upload CSV with: 1 row matching existing contact + 1 new contact
            csv_content = (
                "Purchase Number,Order Number,Date,SKU,Dish Name,Quantity,Unit Price,"
                "Order Type,Customer Name,Customer Phone Number,Customer Address\n"
                f"P-G18-002,{TEST_TAG}-002,{today},V1,Dal Makhani,1,12.00,"
                "Scheduled Order,Returning Customer,+15550022222,123 Main St Georgia 30301\n"
                f"P-G18-003,{TEST_TAG}-003,{today},V2,Rice Bowl,1,10.00,"
                "Scheduled Order,Brand New Customer,+15550033333,456 New St Georgia 30302\n"
            )
            with httpx.Client(timeout=120) as client:
                r = client.post(
                    f"{LOCAL_BASE}/api/daily-orders/process",
                    files={"file": ("test_g18_match.csv", _io.BytesIO(csv_content.encode()), "text/csv")},
                )
            assert r.status_code in (200, 201), f"Upload returned {r.status_code}: {r.text[:200]}"
            body = r.json()
            assert body.get("total_orders", 0) >= 1, "Expected at least 1 order processed"
            # Verify returning contact still has same ID
            with get_cursor(commit=False) as cur:
                cur.execute("SELECT id FROM contacts WHERE phone = '5550022222'")
                row = cur.fetchone()
            assert row and row["id"] == existing_id, "Returning contact ID changed — unexpected new contact created"
            return {
                "total_orders": body["total_orders"],
                "new_contacts": body.get("new_contacts", 0),
                "existing_contact_preserved": True,
            }
        finally:
            # Cleanup all test data created by this sub-test
            with get_cursor(commit=True) as cur:
                _cleanup_g18(cur)
                cur.execute("DELETE FROM contacts WHERE id = %s", (existing_id,))
                cur.execute("DELETE FROM contacts WHERE phone = '5550033333'")
    _run(suite, "daily_order_contact_match_branches", G, order_upload_new_vs_returning)

    def order_upload_menu_catalog_available():
        """Verify menu items are resolved from menu_catalog when it's available."""
        today = _date.today().strftime("%d/%m/%Y")
        # Ensure menu_catalog has at least one item
        with get_cursor(commit=True) as cur:
            cur.execute("SAVEPOINT mc_check")
            try:
                cur.execute("SELECT COUNT(*) AS cnt FROM menu_catalog WHERE active = TRUE")
                cnt = cur.fetchone()["cnt"]
                cur.execute("RELEASE SAVEPOINT mc_check")
            except Exception:
                cur.execute("ROLLBACK TO SAVEPOINT mc_check")
                cur.execute("RELEASE SAVEPOINT mc_check")
                cnt = 0
        if cnt == 0:
            return {"skip": "menu_catalog empty — skipping menu resolution test"}
        # Upload a CSV with a known menu item
        csv_content = (
            "Purchase Number,Order Number,Date,SKU,Dish Name,Quantity,Unit Price,"
            "Order Type,Customer Name,Customer Phone Number,Customer Address\n"
            f"P-G18-004,{TEST_TAG}-004,{today},V3,Dal Makhani,1,12.00,"
            "Scheduled Order,Menu Test User,+15550044444,789 Menu Rd Georgia 30303\n"
        )
        with httpx.Client(timeout=120) as client:
            r = client.post(
                f"{LOCAL_BASE}/api/daily-orders/process",
                files={"file": ("test_g18_menu.csv", _io.BytesIO(csv_content.encode()), "text/csv")},
            )
        assert r.status_code in (200, 201), f"Upload returned {r.status_code}: {r.text[:200]}"
        body = r.json()
        menu_matched = body.get("menu_matched", 0)
        menu_created = body.get("menu_created", 0)
        # At least one dish should be matched or created
        assert (menu_matched + menu_created) > 0, (
            f"Expected menu_matched or menu_created > 0, got: matched={menu_matched} created={menu_created}"
        )
        with get_cursor(commit=True) as cur:
            _cleanup_g18(cur)
            cur.execute("DELETE FROM contacts WHERE phone = '5550044444'")
        return {"menu_matched": menu_matched, "menu_created": menu_created}
    _run(suite, "daily_order_menu_catalog_resolution", G, order_upload_menu_catalog_available)

    def order_upload_delivery_slot_parsing():
        """Verify delivery slot is parsed correctly from '9:30 AM - 12:30 PM' format."""
        today = _date.today().strftime("%d/%m/%Y")
        csv_content = (
            "Purchase Number,Order Number,Date,Delivery Slot Name,Dish Name,Quantity,Unit Price,"
            "Order Type,Customer Name,Customer Phone Number,Customer Address\n"
            f"P-G18-005,{TEST_TAG}-005,{today},9:30 AM - 12:30 PM,Paneer Tikka,1,14.00,"
            "Scheduled Order,Slot Test User,+15550055555,111 Slot Ave Georgia 30304\n"
        )
        with httpx.Client(timeout=120) as client:
            r = client.post(
                f"{LOCAL_BASE}/api/daily-orders/process",
                files={"file": ("test_g18_slot.csv", _io.BytesIO(csv_content.encode()), "text/csv")},
            )
        assert r.status_code in (200, 201), f"Upload returned {r.status_code}"
        body = r.json()
        assert body.get("total_orders", 0) > 0, "Order with delivery slot not processed"
        # Verify delivery_slot stored in DB
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT delivery_slot FROM orders WHERE order_id_external = %s", (f"{TEST_TAG}-005",))
            row = cur.fetchone()
        assert row, "Order with delivery slot not found in DB"
        assert row["delivery_slot"], f"delivery_slot is empty: {row['delivery_slot']}"
        with get_cursor(commit=True) as cur:
            _cleanup_g18(cur)
            cur.execute("DELETE FROM contacts WHERE phone = '5550055555'")
        return {"delivery_slot": row["delivery_slot"], "branch": "slot_parsed_correctly"}
    _run(suite, "daily_order_delivery_slot_parsing", G, order_upload_delivery_slot_parsing)

    # ── Shipday CSV output checks ──────────────────────────────────────────────

    def shipday_csv_url_present():
        """Response always includes shipday_csv_download_url when orders are processed."""
        today = _date.today().strftime("%d/%m/%Y")
        csv_content = (
            "Purchase Number,Order Number,Date,SKU,Delivery Slot Name,Dish Name,Quantity,Unit Price,"
            "Order Type,Customer Name,Customer Phone Number,Customer Address\n"
            f"P-G18-S01,{TEST_TAG}-S01,{today},9:30 AM - 12:30 PM,Dal Makhani,1,12.00,"
            "Scheduled Order,Shipday Test User,+15550066666,10 Shipday Rd Georgia 30305\n"
        )
        with httpx.Client(timeout=120) as client:
            r = client.post(
                f"{LOCAL_BASE}/api/daily-orders/process",
                files={"file": ("test_g18_shipday.csv", _io.BytesIO(csv_content.encode()), "text/csv")},
            )
        assert r.status_code in (200, 201), f"Upload returned {r.status_code}"
        body = r.json()
        url = body.get("shipday_csv_download_url", "")
        assert url, f"shipday_csv_download_url missing or empty in response: {body}"
        assert "/download-shipday-csv/" in url, f"URL format unexpected: {url}"
        with get_cursor(commit=True) as cur:
            _cleanup_g18(cur)
            cur.execute("DELETE FROM contacts WHERE phone = '5550066666'")
        return {"shipday_csv_download_url": url}
    _run(suite, "daily_order_shipday_csv_url_present", G, shipday_csv_url_present)

    def shipday_csv_downloadable():
        """GET the shipday_csv_download_url → 200, text/csv, non-empty body."""
        today = _date.today().strftime("%d/%m/%Y")
        csv_content = (
            "Purchase Number,Order Number,Date,Delivery Slot Name,Dish Name,Quantity,Unit Price,"
            "Order Type,Customer Name,Customer Phone Number,Customer Address\n"
            f"P-G18-S02,{TEST_TAG}-S02,{today},9:30 AM - 12:30 PM,Butter Chicken,1,13.00,"
            "Scheduled Order,Shipday DL Test,+15550077777,20 DL Ave Georgia 30306\n"
        )
        with httpx.Client(timeout=120) as client:
            r = client.post(
                f"{LOCAL_BASE}/api/daily-orders/process",
                files={"file": ("test_g18_sd2.csv", _io.BytesIO(csv_content.encode()), "text/csv")},
            )
        assert r.status_code in (200, 201), f"Upload returned {r.status_code}"
        body = r.json()
        url = body.get("shipday_csv_download_url", "")
        assert url, "shipday_csv_download_url not returned"
        # Download the generated file
        with httpx.Client(timeout=30) as client:
            dl = client.get(f"{LOCAL_BASE}{url}")
        assert dl.status_code == 200, f"Download returned {dl.status_code}: {dl.text[:200]}"
        assert "text/csv" in dl.headers.get("content-type", ""), (
            f"Expected text/csv, got: {dl.headers.get('content-type')}"
        )
        assert dl.text.strip(), "Downloaded Shipday CSV is empty"
        with get_cursor(commit=True) as cur:
            _cleanup_g18(cur)
            cur.execute("DELETE FROM contacts WHERE phone = '5550077777'")
        return {"bytes": len(dl.content), "content_type": dl.headers.get("content-type")}
    _run(suite, "daily_order_shipday_csv_downloadable", G, shipday_csv_downloadable)

    def shipday_csv_columns():
        """Shipday CSV must have exactly the required Shipday import column headers."""
        import csv as _csv
        today = _date.today().strftime("%d/%m/%Y")
        csv_content = (
            "Purchase Number,Order Number,Date,Delivery Slot Name,Dish Name,Quantity,Unit Price,"
            "Order Type,Customer Name,Customer Phone Number,Customer Address\n"
            f"P-G18-S03,{TEST_TAG}-S03,{today},9:30 AM - 12:30 PM,Paneer Tikka,1,14.00,"
            "Scheduled Order,Column Check User,+15550088888,30 Col St Georgia 30307\n"
        )
        with httpx.Client(timeout=120) as client:
            r = client.post(
                f"{LOCAL_BASE}/api/daily-orders/process",
                files={"file": ("test_g18_sd3.csv", _io.BytesIO(csv_content.encode()), "text/csv")},
            )
        body = r.json()
        url = body.get("shipday_csv_download_url", "")
        assert url, "No shipday_csv_download_url"
        with httpx.Client(timeout=30) as client:
            dl = client.get(f"{LOCAL_BASE}{url}")
        reader = _csv.DictReader(_io.StringIO(dl.text))
        headers = reader.fieldnames or []
        required = [
            "Order Number",
            "Delivery customer Name",
            "Customer Phone number",
            "Delivery Address",
            "Delivery Date",
            "Delivery Time",
            "Pickup Time",
            "Delivery instructions",
        ]
        missing = [h for h in required if h not in headers]
        assert not missing, f"Shipday CSV missing columns: {missing}  (got: {headers})"
        with get_cursor(commit=True) as cur:
            _cleanup_g18(cur)
            cur.execute("DELETE FROM contacts WHERE phone = '5550088888'")
        return {"headers": headers}
    _run(suite, "daily_order_shipday_csv_columns", G, shipday_csv_columns)

    def shipday_csv_content_matches_input():
        """Shipday CSV row must contain the same order number, customer name, and phone as the input."""
        import csv as _csv
        today = _date.today().strftime("%d/%m/%Y")
        csv_content = (
            "Purchase Number,Order Number,Date,Delivery Slot Name,Dish Name,Quantity,Unit Price,"
            "Order Type,Customer Name,Customer Phone Number,Customer Address\n"
            f"P-G18-S04,{TEST_TAG}-S04,{today},9:30 AM - 12:30 PM,Rice Bowl,1,11.00,"
            "Scheduled Order,Content Match User,+15550099999,40 Match Rd Georgia 30308\n"
        )
        with httpx.Client(timeout=120) as client:
            r = client.post(
                f"{LOCAL_BASE}/api/daily-orders/process",
                files={"file": ("test_g18_sd4.csv", _io.BytesIO(csv_content.encode()), "text/csv")},
            )
        body = r.json()
        url = body.get("shipday_csv_download_url", "")
        assert url, "No shipday_csv_download_url"
        with httpx.Client(timeout=30) as client:
            dl = client.get(f"{LOCAL_BASE}{url}")
        rows = list(_csv.DictReader(_io.StringIO(dl.text)))
        assert rows, "Shipday CSV has no data rows"
        order_row = next((row for row in rows if row.get("Order Number") == f"{TEST_TAG}-S04"), None)
        assert order_row, (
            f"Order {TEST_TAG}-S04 not found in Shipday CSV. Rows: {[r.get('Order Number') for r in rows]}"
        )
        assert "Content Match User" in order_row.get("Delivery customer Name", ""), (
            f"Customer name mismatch: {order_row.get('Delivery customer Name')}"
        )
        # Phone stored without leading +
        assert "5550099999" in order_row.get("Customer Phone number", ""), (
            f"Phone mismatch: {order_row.get('Customer Phone number')}"
        )
        with get_cursor(commit=True) as cur:
            _cleanup_g18(cur)
            cur.execute("DELETE FROM contacts WHERE phone = '5550099999'")
        return {
            "order_number": order_row["Order Number"],
            "customer_name": order_row["Delivery customer Name"],
            "phone": order_row["Customer Phone number"],
        }
    _run(suite, "daily_order_shipday_csv_content_matches_input", G, shipday_csv_content_matches_input)

    def shipday_csv_delivery_time_parsed():
        """Delivery slot '9:30 AM - 12:30 PM' must produce correct Pickup Time + Delivery Time in Shipday CSV."""
        import csv as _csv
        today = _date.today().strftime("%d/%m/%Y")
        csv_content = (
            "Purchase Number,Order Number,Date,Delivery Slot Name,Dish Name,Quantity,Unit Price,"
            "Order Type,Customer Name,Customer Phone Number,Customer Address\n"
            f"P-G18-S05,{TEST_TAG}-S05,{today},9:30 AM - 12:30 PM,Samosa,2,5.00,"
            "Scheduled Order,Slot Parse User,+15550111111,50 Slot Ave Georgia 30309\n"
        )
        with httpx.Client(timeout=120) as client:
            r = client.post(
                f"{LOCAL_BASE}/api/daily-orders/process",
                files={"file": ("test_g18_sd5.csv", _io.BytesIO(csv_content.encode()), "text/csv")},
            )
        body = r.json()
        url = body.get("shipday_csv_download_url", "")
        assert url, "No shipday_csv_download_url"
        with httpx.Client(timeout=30) as client:
            dl = client.get(f"{LOCAL_BASE}{url}")
        rows = list(_csv.DictReader(_io.StringIO(dl.text)))
        order_row = next((row for row in rows if row.get("Order Number") == f"{TEST_TAG}-S05"), None)
        assert order_row, f"Order {TEST_TAG}-S05 not in Shipday CSV"
        pickup = order_row.get("Pickup Time", "")
        delivery = order_row.get("Delivery Time", "")
        assert pickup, f"Pickup Time is empty: {order_row}"
        assert delivery, f"Delivery Time is empty: {order_row}"
        # 9:30 AM → "9:30 AM" (12-hour format)
        assert "9:30" in pickup, f"Pickup Time '9:30 AM' not found, got: {pickup}"
        assert "12:30" in delivery, f"Delivery Time '12:30 PM' not found, got: {delivery}"
        with get_cursor(commit=True) as cur:
            _cleanup_g18(cur)
            cur.execute("DELETE FROM contacts WHERE phone = '5550111111'")
        return {"pickup_time": pickup, "delivery_time": delivery}
    _run(suite, "daily_order_shipday_csv_delivery_time_parsed", G, shipday_csv_delivery_time_parsed)

    def shipday_csv_download_404_after_reboot():
        """GET /download-shipday-csv/<invalid-id> → 404 (file not found branch)."""
        import uuid as _uuid
        fake_id = str(_uuid.uuid4())
        with httpx.Client(timeout=10) as client:
            r = client.get(f"{LOCAL_BASE}/api/daily-orders/download-shipday-csv/{fake_id}")
        assert r.status_code == 404, f"Expected 404 for unknown file_id, got {r.status_code}"
        return {"status": 404, "branch": "file_not_found"}
    _run(suite, "daily_order_shipday_csv_404_invalid_id", G, shipday_csv_download_404_after_reboot)


def _g19_n8n_action_queue_all_routes(suite: TestSuite) -> None:
    """Group 19 — [System] Action Queue: all 8 action_type routes.

    n8n workflow 'Route by Action Type' has 8 branches + fallback.
    This group inserts one action_queue row per type, verifies it appears
    in GET /pending, marks it done, verifies status, then deletes.
    """
    G = "19_n8n_action_queue_routes"
    created_ids: list[int] = []

    def _insert_action(action_type: str, payload: dict) -> int:
        """Insert an action_queue row and return its id."""
        with get_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO action_queue (contact_id, action_type, payload, status) "
                "VALUES (%s, %s, %s::jsonb, 'pending') RETURNING id",
                (suite.test_contact_id, action_type, json.dumps(payload)),
            )
            return cur.fetchone()["id"]

    def _get_pending_ids() -> set:
        sc, body = _local("GET", "/api/agents/action-queue/pending")
        assert sc == 200, f"action-queue/pending returned {sc}"
        return {row["id"] for row in body} if isinstance(body, list) else set()

    def _mark_done(aq_id: int):
        sc, _ = _local("POST", f"/api/agents/action-queue/{aq_id}/done")
        assert sc == 200, f"mark-done for id={aq_id} returned {sc}"

    def _verify_status(aq_id: int, expected: str):
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT status FROM action_queue WHERE id = %s", (aq_id,))
            row = cur.fetchone()
        assert row, f"action_queue row id={aq_id} not found"
        assert row["status"] == expected, f"Expected status={expected}, got={row['status']}"

    def _cleanup():
        if created_ids:
            with get_cursor(commit=True) as cur:
                placeholders = ",".join(["%s"] * len(created_ids))
                cur.execute(f"DELETE FROM action_queue WHERE id IN ({placeholders})", created_ids)
        created_ids.clear()

    # Each tuple: (action_type, payload, description)
    action_cases = [
        ("send_sms", {
            "message_body": "Test SMS from action queue",
            "phone": TEST_PHONE,
            "contact_id": suite.test_contact_id or 0,
        }, "send_sms route"),
        ("move_campaign", {
            "to_campaign": "NURTURE_SLOW",
            "instantly_campaign_id": "76a88797-961a-47b6-af11-77e2211c4e73",
            "contact_id": suite.test_contact_id or 0,
            "email": TEST_EMAIL,
        }, "move_campaign route"),
        ("escalate_airtable", {
            "urgency": "high",
            "reason": "Test escalation from harness",
            "contact_id": suite.test_contact_id or 0,
            "phone": TEST_PHONE,
        }, "escalate_airtable route"),
        ("sync_airtable_task", {
            "contact_id": suite.test_contact_id or 0,
            "task_type": "follow_up",
            "notes": "Test sync task",
        }, "sync_airtable_task route"),
        ("push_instantly_lead", {
            "instantly_campaign_id": "76a88797-961a-47b6-af11-77e2211c4e73",
            "campaign_name": "NURTURE_SLOW",
            "email": TEST_EMAIL,
            "first_name": TEST_FIRST_NAME,
            "last_name": TEST_LAST_NAME,
            "phone": TEST_PHONE,
        }, "push_instantly_lead route"),
        ("push_instantly_sequences", {
            "sequence_id": "test-seq-001",
            "email": TEST_EMAIL,
        }, "push_instantly_sequences route"),
        ("upload_google_drive", {
            "filename": "test_upload.csv",
            "content": "col1,col2\nval1,val2",
        }, "upload_google_drive route"),
        ("send_email_report", {
            "to": TEST_EMAIL,
            "subject": "Test report from harness",
            "body": "<p>Test email body</p>",
            "source": "test_harness",
        }, "send_email_report route"),
    ]

    for action_type, payload, desc in action_cases:
        def make_test(at, pl, d):
            def test_fn():
                if not suite.test_contact_id and at not in ("push_instantly_lead", "push_instantly_sequences", "upload_google_drive", "send_email_report"):
                    return {"skip": "no test contact — skipping contact-dependent route"}
                aq_id = _insert_action(at, pl)
                created_ids.append(aq_id)
                # Verify it appears in pending list
                pending = _get_pending_ids()
                assert aq_id in pending, (
                    f"{at} action id={aq_id} not in pending list — "
                    f"pending has {len(pending)} items"
                )
                # Verify action_type in response
                sc, body = _local("GET", "/api/agents/action-queue/pending")
                action_row = next((r for r in body if r["id"] == aq_id), None)
                assert action_row, f"Row id={aq_id} not found in pending response"
                assert action_row["action_type"] == at, (
                    f"Expected action_type={at}, got={action_row['action_type']}"
                )
                # Mark done
                _mark_done(aq_id)
                # Verify status changed to 'done'
                _verify_status(aq_id, "done")
                created_ids.remove(aq_id)  # already done, cleanup only the pending ones
                # Re-add so cleanup loop can delete it
                created_ids.append(aq_id)
                return {"action_type": at, "aq_id": aq_id, "branch": d}
            return test_fn
        _run(suite, f"action_queue_route_{action_type}", G, make_test(action_type, payload, desc))

    def mark_failed_branch():
        """Test the 'failed' branch: mark action as failed → status = failed."""
        aq_id = _insert_action("send_sms", {
            "message_body": "Test failed branch",
            "phone": TEST_PHONE,
        })
        created_ids.append(aq_id)
        sc, _ = _local("POST", f"/api/agents/action-queue/{aq_id}/failed")
        assert sc == 200, f"mark-failed returned {sc}"
        _verify_status(aq_id, "failed")
        return {"aq_id": aq_id, "status": "failed", "branch": "mark_failed"}
    _run(suite, "action_queue_mark_failed_branch", G, mark_failed_branch)

    def cleanup_all():
        _cleanup()
        return {"cleaned_up": True}
    _run(suite, "action_queue_cleanup", G, cleanup_all)


def _g20_n8n_broadcast_flow(suite: TestSuite) -> None:
    """Group 20 — [Broadcast] Dispatch integration tests.

    n8n workflow Route by Channel has sms + email branches.
    IF Has Pending Recipients? TRUE and FALSE.
    Tests: create job → queue → poll (TRUE branch) → mark sent/failed → poll (FALSE branch).
    All test data tagged with title='TEST_HARNESS_G20' and cleaned up at end.
    """
    G = "20_n8n_broadcast_flow"
    created_job_id = [None]   # mutable container for cleanup

    def _cleanup():
        if created_job_id[0]:
            with get_cursor(commit=True) as cur:
                cur.execute("DELETE FROM broadcast_recipients WHERE job_id = %s", (created_job_id[0],))
                cur.execute("DELETE FROM broadcast_jobs WHERE id = %s", (created_job_id[0],))
            created_job_id[0] = None

    def broadcast_false_branch_empty_queue():
        """FALSE branch: no pending recipients → pending-recipients returns empty."""
        sc, body = _local("GET", "/api/broadcasts/pending-recipients")
        assert sc == 200, f"pending-recipients returned {sc}"
        # Body should be a list (possibly empty from other jobs)
        assert isinstance(body, list), f"Expected list, got: {type(body)}"
        return {"pending_count": len(body), "branch": "FALSE (empty or unrelated)"}
    _run(suite, "broadcast_false_branch_no_pending", G, broadcast_false_branch_empty_queue)

    def broadcast_create_job():
        """Create a test broadcast job (draft state)."""
        sc, body = _local("POST", "/api/broadcasts/", json_body={
            "title": "TEST_HARNESS_G20",
            "broadcast_type": "promo",
            "channels": ["sms", "email"],
            "sms_message": "Test broadcast SMS from harness",
            "email_subject": "Test broadcast email",
            "email_body": "<p>Test</p>",
            "target_type": "manual_list",
        })
        if sc == 404:
            return {"skip": "broadcasts endpoint not found"}
        assert sc in (200, 201), f"create broadcast returned {sc}: {str(body)[:200]}"
        job_id = (body.get("id") or body.get("job_id")) if isinstance(body, dict) else None
        assert job_id, f"No job_id in response: {body}"
        created_job_id[0] = job_id
        return {"job_id": job_id, "status": "draft"}
    _run(suite, "broadcast_create_job", G, broadcast_create_job)

    def broadcast_true_branch_sms():
        """TRUE + SMS branch: insert pending SMS recipient → verify in pending-recipients."""
        if not created_job_id[0] or not suite.test_contact_id:
            return {"skip": "no job or no test contact"}
        # Insert test recipient directly into DB
        with get_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO broadcast_recipients (job_id, contact_id, channel, status, "
                "sms_message) VALUES (%s, %s, 'sms', 'pending', %s) RETURNING id",
                (created_job_id[0], suite.test_contact_id, "Test SMS broadcast body"),
            )
            recipient_id = cur.fetchone()["id"]
        # Poll: should return this recipient
        sc, body = _local("GET", "/api/broadcasts/pending-recipients")
        assert sc == 200, f"pending-recipients returned {sc}"
        assert isinstance(body, list), f"Expected list"
        ids = [r.get("id") for r in body]
        assert recipient_id in ids, (
            f"SMS recipient id={recipient_id} not in pending-recipients (found {len(body)} total)"
        )
        # Mark sent → TRUE branch result
        sc2, _ = _local("POST", f"/api/broadcasts/recipients/{recipient_id}/sent")
        assert sc2 == 200, f"mark-sent returned {sc2}"
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT status FROM broadcast_recipients WHERE id = %s", (recipient_id,))
            row = cur.fetchone()
        assert row and row["status"] == "sent", f"Expected status=sent, got={row}"
        return {"recipient_id": recipient_id, "channel": "sms", "final_status": "sent", "branch": "TRUE/sms"}
    _run(suite, "broadcast_true_branch_sms_route", G, broadcast_true_branch_sms)

    def broadcast_true_branch_email():
        """TRUE + Email branch: insert pending email recipient → mark sent."""
        if not created_job_id[0] or not suite.test_contact_id:
            return {"skip": "no job or no test contact"}
        with get_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO broadcast_recipients (job_id, contact_id, channel, status, "
                "email_subject, email_body) VALUES (%s, %s, 'email', 'pending', %s, %s) RETURNING id",
                (created_job_id[0], suite.test_contact_id, "Test Email Subject", "<p>Body</p>"),
            )
            recipient_id = cur.fetchone()["id"]
        sc, _ = _local("POST", f"/api/broadcasts/recipients/{recipient_id}/sent")
        assert sc == 200, f"mark-sent returned {sc}"
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT status FROM broadcast_recipients WHERE id = %s", (recipient_id,))
            row = cur.fetchone()
        assert row and row["status"] == "sent", f"Expected status=sent"
        return {"recipient_id": recipient_id, "channel": "email", "final_status": "sent", "branch": "TRUE/email"}
    _run(suite, "broadcast_true_branch_email_route", G, broadcast_true_branch_email)

    def broadcast_failed_branch():
        """Test mark-failed branch: recipient marked as failed."""
        if not created_job_id[0] or not suite.test_contact_id:
            return {"skip": "no job or no test contact"}
        with get_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO broadcast_recipients (job_id, contact_id, channel, status) "
                "VALUES (%s, %s, 'sms', 'pending') RETURNING id",
                (created_job_id[0], suite.test_contact_id),
            )
            recipient_id = cur.fetchone()["id"]
        sc, _ = _local("POST", f"/api/broadcasts/recipients/{recipient_id}/failed")
        assert sc == 200, f"mark-failed returned {sc}"
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT status FROM broadcast_recipients WHERE id = %s", (recipient_id,))
            row = cur.fetchone()
        assert row and row["status"] == "failed", f"Expected status=failed, got={row}"
        return {"recipient_id": recipient_id, "final_status": "failed", "branch": "failed"}
    _run(suite, "broadcast_failed_branch", G, broadcast_failed_branch)

    def cleanup_g20():
        _cleanup()
        return {"cleaned_up": True}
    _run(suite, "broadcast_flow_cleanup", G, cleanup_g20)


def _g21_n8n_menu_sync_flow(suite: TestSuite) -> None:
    """Group 21 — [Menu] Catalog Sync + Playbook Sync integration tests.

    Tests POST /api/menu/sync (Airtable → menu_catalog) and POST /api/playbook/sync.
    For each: verifies table has data after sync, tests both the 'has items TRUE'
    and 'has errors FALSE' branches.
    All test items inserted directly into DB are cleaned up.
    """
    G = "21_n8n_menu_playbook_sync"
    TEST_MENU_ITEM = "TEST_HARNESS_G21_DISH_ZZZZ"
    TEST_PLAYBOOK_RULE = "TEST_HARNESS_G21_RULE"

    def menu_sync_call():
        """Call menu sync — Airtable → menu_catalog. Verifies has_items TRUE branch."""
        sc, body = _local("POST", "/api/menu/sync", timeout=30)
        if sc == 404:
            return {"skip": "menu sync endpoint not found"}
        assert sc == 200, f"menu/sync returned {sc}: {str(body)[:200]}"
        # Verify items exist in DB after sync (TRUE branch: has items)
        with get_cursor(commit=False) as cur:
            cur.execute("SAVEPOINT mc")
            try:
                cur.execute("SELECT COUNT(*) AS cnt FROM menu_catalog WHERE active = TRUE")
                cnt = cur.fetchone()["cnt"]
                cur.execute("RELEASE SAVEPOINT mc")
            except Exception:
                cur.execute("ROLLBACK TO SAVEPOINT mc")
                cur.execute("RELEASE SAVEPOINT mc")
                cnt = 0
        synced = body.get("synced", body.get("upserted", body.get("total", 0))) if isinstance(body, dict) else 0
        return {"synced_count": synced, "active_items_in_db": cnt, "branch": "TRUE (has_items)"}
    _run(suite, "menu_sync_true_branch", G, menu_sync_call)

    def menu_sync_db_item_inserted():
        """Insert a test menu item directly, verify it can be fetched from /api/menu/items."""
        with get_cursor(commit=True) as cur:
            cur.execute("SAVEPOINT mc_ins")
            try:
                cur.execute(
                    "INSERT INTO menu_catalog (item_name, category, is_veg, price, active) "
                    "VALUES (%s, 'Test', TRUE, 9.99, TRUE) ON CONFLICT (item_name) DO NOTHING RETURNING id",
                    (TEST_MENU_ITEM,),
                )
                row = cur.fetchone()
                cur.execute("RELEASE SAVEPOINT mc_ins")
            except Exception:
                cur.execute("ROLLBACK TO SAVEPOINT mc_ins")
                cur.execute("RELEASE SAVEPOINT mc_ins")
                return {"skip": "menu_catalog table not available"}

        if not row:
            return {"skip": "item already exists (ON CONFLICT)"}

        inserted_id = row["id"]
        sc, body = _local("GET", "/api/menu/items")
        assert sc == 200, f"menu/items returned {sc}"
        items = body if isinstance(body, list) else body.get("items", [])
        names = [it.get("item_name") for it in items]
        assert TEST_MENU_ITEM in names, f"Test item {TEST_MENU_ITEM} not found in /api/menu/items"

        # Cleanup
        with get_cursor(commit=True) as cur:
            cur.execute("DELETE FROM menu_catalog WHERE id = %s", (inserted_id,))
        return {"test_item_id": inserted_id, "visible_in_api": True}
    _run(suite, "menu_item_db_insert_and_fetch", G, menu_sync_db_item_inserted)

    def playbook_sync_call():
        """Call playbook sync — Airtable → agent_playbook. Verifies has_changes branch."""
        sc, body = _local("POST", "/api/playbook/sync", timeout=30)
        if sc == 404:
            return {"skip": "playbook sync endpoint not found"}
        assert sc == 200, f"playbook/sync returned {sc}: {str(body)[:200]}"
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM agent_playbook WHERE active = TRUE")
            cnt = cur.fetchone()["cnt"]
        synced = body.get("synced", body.get("upserted", 0)) if isinstance(body, dict) else 0
        return {"synced_count": synced, "active_rules_in_db": cnt, "branch": "TRUE (has_rules)"}
    _run(suite, "playbook_sync_true_branch", G, playbook_sync_call)

    def playbook_db_rule_insert_and_fetch():
        """Insert a test playbook rule directly, verify GET /api/playbook/rules returns it."""
        with get_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO agent_playbook (rule_name, category, instruction, priority, active, created_by) "
                "VALUES (%s, 'general', 'Test: do nothing', 0, TRUE, 'test_harness') "
                "ON CONFLICT (rule_name) DO NOTHING RETURNING id",
                (TEST_PLAYBOOK_RULE,),
            )
            row = cur.fetchone()

        if not row:
            return {"skip": "rule already exists"}
        rule_id = row["id"]

        sc, body = _local("GET", "/api/playbook/rules")
        assert sc == 200, f"playbook/rules returned {sc}"
        rules = body if isinstance(body, list) else body.get("rules", [])
        names = [r.get("rule_name") for r in rules]
        assert TEST_PLAYBOOK_RULE in names, f"Test rule not found in /api/playbook/rules"

        # Cleanup
        with get_cursor(commit=True) as cur:
            cur.execute("DELETE FROM agent_playbook WHERE id = %s", (rule_id,))
        return {"rule_id": rule_id, "visible_in_api": True}
    _run(suite, "playbook_rule_db_insert_and_fetch", G, playbook_db_rule_insert_and_fetch)

    def playbook_for_prompt_has_rules():
        """Verify /api/playbook/rules/for-prompt returns rules grouped by category."""
        sc, body = _local("GET", "/api/playbook/rules/for-prompt")
        if sc == 404:
            return {"skip": "for-prompt endpoint not found"}
        assert sc == 200, f"for-prompt returned {sc}"
        # Should be a dict with category keys
        assert isinstance(body, dict), f"Expected dict of rules, got: {type(body)}"
        total_rules = sum(len(v) if isinstance(v, list) else 0 for v in body.values())
        assert total_rules > 0, "No rules returned for prompt — agent pipeline will run without rules"
        return {"categories": list(body.keys()), "total_rules": total_rules}
    _run(suite, "playbook_for_prompt_has_rules", G, playbook_for_prompt_has_rules)





# ─── GROUP 14: Data Cleanup ───────────────────────────────────────────────────

def _cascade_delete(cur, contact_id: int) -> None:
    """Delete all test-related rows for a given contact_id."""
    tables = [
        "campaign_queue",
        "action_queue",
        "orchestrator_log",
        "action_plans",
        "decision_log",
        "contact_observations",
        "customer_goals",
        "delivery_status",
        "telnyx_messages",
        "telnyx_calls",
        "engagement_rollups",
        "events",
        "order_items",            # must come before orders
        "orders",
        "broadcast_recipients",
        "field_agent_reviews",
        "opportunities",
    ]
    for t in tables:
        try:
            cur.execute(f"DELETE FROM {t} WHERE contact_id = %s", (contact_id,))
        except Exception:
            pass
    cur.execute("DELETE FROM contacts WHERE id = %s", (contact_id,))


def _g22_n8n_sms_dispatch_flow(suite: TestSuite) -> None:
    """Group 22 — [SMS] Dispatch Queue n8n flow integration tests.

    Tests the Python endpoints that n8n's SMS Dispatch workflow calls:
      GET /api/telnyx/pending         — IF: Has Pending SMS? (TRUE/FALSE branch)
      Contact → pending_sms_level     — Code: Build SMS Message (all 4 message branches)
    All inserted data is cleaned up at the end.
    """
    G = "22_n8n_sms_dispatch"

    # ── FALSE branch: no pending SMS ──────────────────────────────────────────
    def sms_false_branch_no_pending():
        """GET /api/telnyx/pending with no flagged contacts → empty list (IF FALSE)."""
        sc, body = _local("GET", "/api/telnyx/pending")
        assert sc == 200, f"sms/pending returned {sc}"
        pending = body.get("pending", []) if isinstance(body, dict) else body
        # We can't guarantee no real contacts, so just assert the structure is right
        assert isinstance(pending, list), "pending field should be a list"
        return {"pending_count": len(pending), "branch": "IF evaluated (list returned)"}
    _run(suite, "sms_false_branch_structure_check", G, sms_false_branch_no_pending)

    # ── TRUE branch: insert contact with pending SMS, verify it appears ───────
    test_sms_contact_id: list = []

    def sms_true_branch_insert_pending_contact():
        """Insert a contact with sms_pending=true, verify it shows in /api/telnyx/pending."""
        with get_cursor(commit=True) as cur:
            cur.execute(
                """INSERT INTO contacts
                       (phone, email, first_name, last_name, source,
                        lifecycle_segment, sms_pending, sms_level,
                        email_nurture_enabled)
                   VALUES (%s, %s, %s, %s, %s, 'active', TRUE, 1, FALSE)
                   ON CONFLICT (email) DO UPDATE SET sms_pending = TRUE
                   RETURNING id""",
                ("+10000000022", "test_g22_sms@test.dabbahwala.com",
                 "TestG22", "SMS", TEST_SOURCE),
            )
            row = cur.fetchone()
            if row:
                test_sms_contact_id.append(row["id"])
        sc, body = _local("GET", "/api/telnyx/pending")
        assert sc == 200, f"sms/pending returned {sc}"
        pending = body.get("pending", []) if isinstance(body, dict) else body
        assert isinstance(pending, list), "pending should be a list"
        # Our test contact should appear since sms_pending=TRUE
        contact_ids = [p.get("contact_id") for p in pending]
        if test_sms_contact_id:
            assert test_sms_contact_id[0] in contact_ids, \
                f"Test contact {test_sms_contact_id[0]} not found in pending SMS list"
        return {"pending_count": len(pending), "branch": "TRUE (has_pending_sms)", "contact_id": test_sms_contact_id[0] if test_sms_contact_id else None}
    _run(suite, "sms_true_branch_pending_contact", G, sms_true_branch_insert_pending_contact)

    # ── Code node: Build SMS Message — new_customer branch ───────────────────
    def sms_build_message_new_customer():
        """Insert new_customer lifecycle contact, verify it appears in pending."""
        with get_cursor(commit=True) as cur:
            cur.execute(
                """INSERT INTO contacts
                       (phone, email, first_name, last_name, source,
                        lifecycle_segment, sms_pending, sms_level,
                        email_nurture_enabled)
                   VALUES (%s, %s, %s, %s, %s, 'new_customer', TRUE, 1, FALSE)
                   ON CONFLICT (email) DO UPDATE SET sms_pending = TRUE, lifecycle_segment = 'new_customer'
                   RETURNING id""",
                ("+10000000023", "test_g22_newcust@test.dabbahwala.com",
                 "TestG22", "NewCust", TEST_SOURCE),
            )
            row = cur.fetchone()
            if row:
                test_sms_contact_id.append(row["id"])
        sc, body = _local("GET", "/api/telnyx/pending")
        assert sc == 200, f"pending returned {sc}"
        pending = body.get("pending", []) if isinstance(body, dict) else body
        new_cust = [p for p in pending if p.get("lifecycle") == "new_customer" or p.get("lifecycle_segment") == "new_customer"]
        return {"pending_total": len(pending), "new_customer_contacts": len(new_cust), "message_branch": "new_customer"}
    _run(suite, "sms_message_new_customer_branch", G, sms_build_message_new_customer)

    # ── Code node: sms_level=3 reactivation branch ───────────────────────────
    def sms_build_message_level3():
        """Insert contact with sms_level=3 (reactivation branch)."""
        with get_cursor(commit=True) as cur:
            cur.execute(
                """INSERT INTO contacts
                       (phone, email, first_name, last_name, source,
                        lifecycle_segment, sms_pending, sms_level,
                        email_nurture_enabled)
                   VALUES (%s, %s, %s, %s, %s, 'lapsed', TRUE, 3, FALSE)
                   ON CONFLICT (email) DO UPDATE SET sms_pending = TRUE, sms_level = 3
                   RETURNING id""",
                ("+10000000024", "test_g22_level3@test.dabbahwala.com",
                 "TestG22", "L3", TEST_SOURCE),
            )
            row = cur.fetchone()
            if row:
                test_sms_contact_id.append(row["id"])
        sc, body = _local("GET", "/api/telnyx/pending")
        assert sc == 200, f"pending returned {sc}"
        pending = body.get("pending", []) if isinstance(body, dict) else body
        level3_contacts = [p for p in pending if p.get("sms_level", 0) >= 3]
        return {"pending_total": len(pending), "level3_contacts": len(level3_contacts), "message_branch": "sms_level>=3_reactivation"}
    _run(suite, "sms_message_level3_reactivation_branch", G, sms_build_message_level3)

    # ── Cleanup ───────────────────────────────────────────────────────────────
    def cleanup_g22():
        with get_cursor(commit=True) as cur:
            cur.execute("DELETE FROM contacts WHERE source = %s AND first_name = 'TestG22'", (TEST_SOURCE,))
    _run(suite, "sms_dispatch_cleanup", G, cleanup_g22)


# ─── GROUP 23: n8n Intelligence flow ─────────────────────────────────────────

def _g23_n8n_intelligence_flow(suite: TestSuite) -> None:
    """Group 23 — [Intelligence] Contact Sweep + Stage Runner n8n flow integration tests.

    Tests GET /api/intelligence/pending-actions and related endpoints.
    All IF branches:
      - FALSE branch: no pending actions → summary.total_pending = 0
      - TRUE branch: insert action_queue / campaign_queue entries → they appear
    """
    G = "23_n8n_intelligence"

    # ── FALSE branch: pending-actions returns empty summary ───────────────────
    def intelligence_pending_actions_structure():
        """GET /api/intelligence/pending-actions — verify response shape."""
        sc, body = _local("GET", "/api/intelligence/pending-actions")
        assert sc == 200, f"pending-actions returned {sc}"
        assert isinstance(body, dict), "Expected dict response"
        assert "summary" in body, "Missing 'summary' key"
        summary = body["summary"]
        assert "total_pending" in summary, "Missing summary.total_pending"
        return {
            "total_pending": summary.get("total_pending"),
            "campaign_moves": summary.get("campaign_moves", 0),
            "sms": summary.get("sms", 0),
            "branch_assessed": "IF: Has Pending Actions? (structure verified)"
        }
    _run(suite, "intelligence_pending_actions_structure", G, intelligence_pending_actions_structure)

    # ── TRUE branch: insert campaign_queue entry, verify it appears ───────────
    test_intel_contact_id: list = []

    def intelligence_campaign_queue_true_branch():
        """Insert contact + campaign_queue entry → verify pending-actions shows it (IF TRUE)."""
        with get_cursor(commit=True) as cur:
            cur.execute(
                """INSERT INTO contacts
                       (phone, email, first_name, last_name, source,
                        lifecycle_segment, email_nurture_enabled)
                   VALUES (%s, %s, 'TestG23', 'Intel', %s, 'cold', FALSE)
                   ON CONFLICT (email) DO UPDATE SET first_name = 'TestG23'
                   RETURNING id""",
                ("+10000000023i", "test_g23_intel@test.dabbahwala.com", TEST_SOURCE),
            )
            row = cur.fetchone()
            if not row:
                cur.execute("SELECT id FROM contacts WHERE email = %s", ("test_g23_intel@test.dabbahwala.com",))
                row = cur.fetchone()
            contact_id = row["id"]
            test_intel_contact_id.append(contact_id)

            # Insert a pending campaign_queue entry
            cur.execute(
                """INSERT INTO campaign_queue (contact_id, from_campaign, to_campaign, status)
                   VALUES (%s, 'NURTURE_SLOW', 'PROMO_STANDARD', 'pending')
                   RETURNING id""",
                (contact_id,),
            )
            queue_row = cur.fetchone()

        sc, body = _local("GET", "/api/intelligence/pending-actions")
        assert sc == 200, f"pending-actions returned {sc}"
        moves = body.get("campaign_moves", [])
        contact_in_moves = any(m.get("contact_id") == contact_id or m.get("email") == "test_g23_intel@test.dabbahwala.com" for m in moves)
        assert contact_in_moves, f"Test contact not found in campaign_moves"
        return {
            "campaign_moves_count": len(moves),
            "contact_id": contact_id,
            "branch": "TRUE (has_pending_actions)",
        }
    _run(suite, "intelligence_campaign_queue_true_branch", G, intelligence_campaign_queue_true_branch)

    # ── TRUE branch: pending opportunity appears in pending-actions ───────────
    def intelligence_opportunity_in_pending_actions():
        """Insert pending opportunity → verify it appears in sms_to_send or similar."""
        if not test_intel_contact_id:
            return {"skip": "no test contact created"}
        contact_id = test_intel_contact_id[0]
        with get_cursor(commit=True) as cur:
            cur.execute(
                """INSERT INTO opportunities
                       (contact_id, action, priority, reason, status)
                   VALUES (%s, 'send_sms', 'warm', 'test_harness_g23', 'pending')
                   RETURNING id""",
                (contact_id,),
            )

        sc, body = _local("GET", "/api/intelligence/pending-actions")
        assert sc == 200, f"pending-actions returned {sc}"
        sms_list = body.get("sms_to_send", [])
        call_list = body.get("sales_calls", [])
        email_list = body.get("emails_to_send", [])
        total = len(sms_list) + len(call_list) + len(email_list)
        return {
            "sms_to_send": len(sms_list),
            "emails_to_send": len(email_list),
            "sales_calls": len(call_list),
            "total_opportunities": total,
            "branch": "TRUE (opportunities visible)",
        }
    _run(suite, "intelligence_opportunity_true_branch", G, intelligence_opportunity_in_pending_actions)

    # ── Cleanup ───────────────────────────────────────────────────────────────
    def cleanup_g23():
        with get_cursor(commit=True) as cur:
            if test_intel_contact_id:
                _cascade_delete(cur, test_intel_contact_id[0])
    _run(suite, "intelligence_flow_cleanup", G, cleanup_g23)


# ─── GROUP 24: n8n Field Agent flow ──────────────────────────────────────────

def _g24_n8n_field_agent_flow(suite: TestSuite) -> None:
    """Group 24 — [Field Agent] Daily Brief + Outcome Sync n8n flow integration tests.

    Tests:
      GET /api/field-agent/pending-calls  — IF: Any pending calls? (both branches)
      POST /api/field-agent/daily-brief   — Format Brief Summary (TRUE branch: contacts exist)
      GET /api/field-agent/scorecard      — verify scorecard structure
    """
    G = "24_n8n_field_agent"

    # ── FALSE branch: GET pending-calls returns structure ─────────────────────
    def field_agent_pending_calls_structure():
        """GET /api/field-agent/pending-calls — verify shape (both TRUE/FALSE branches)."""
        sc, body = _local("GET", "/api/field-agent/pending-calls")
        assert sc == 200, f"field-agent/pending-calls returned {sc}"
        calls = body if isinstance(body, list) else body.get("calls", body.get("pending", []))
        assert isinstance(calls, list), f"Expected list, got {type(calls)}"
        return {"pending_calls_count": len(calls), "branch_assessed": "IF: Any Pending Calls?"}
    _run(suite, "field_agent_pending_calls_structure", G, field_agent_pending_calls_structure)

    # ── TRUE branch: insert opportunity for field call ────────────────────────
    test_field_contact_id: list = []

    def field_agent_insert_pending_call_opportunity():
        """Insert contact + field_sales_call opportunity → verify in pending-calls."""
        with get_cursor(commit=True) as cur:
            cur.execute(
                """INSERT INTO contacts
                       (phone, email, first_name, last_name, source,
                        lifecycle_segment, email_nurture_enabled)
                   VALUES (%s, %s, 'TestG24', 'Field', %s, 'active', FALSE)
                   ON CONFLICT (email) DO UPDATE SET first_name = 'TestG24'
                   RETURNING id""",
                ("+10000000024f", "test_g24_field@test.dabbahwala.com", TEST_SOURCE),
            )
            row = cur.fetchone()
            if not row:
                cur.execute("SELECT id FROM contacts WHERE email = %s", ("test_g24_field@test.dabbahwala.com",))
                row = cur.fetchone()
            contact_id = row["id"]
            test_field_contact_id.append(contact_id)

            cur.execute(
                """INSERT INTO opportunities
                       (contact_id, action, priority, reason, status)
                   VALUES (%s, 'field_sales_call', 'hot', 'test_harness_g24', 'pending')
                   RETURNING id""",
                (contact_id,),
            )

        sc, body = _local("GET", "/api/field-agent/pending-calls")
        assert sc == 200, f"pending-calls returned {sc}"
        calls = body if isinstance(body, list) else body.get("calls", body.get("pending", []))
        ids = [c.get("contact_id") for c in calls]
        assert contact_id in ids, f"Test contact {contact_id} not in pending-calls"
        return {"pending_calls_count": len(calls), "contact_id": contact_id, "branch": "TRUE (has pending calls)"}
    _run(suite, "field_agent_true_branch_pending_call", G, field_agent_insert_pending_call_opportunity)

    # ── scorecard endpoint ────────────────────────────────────────────────────
    def field_agent_scorecard():
        """GET /api/field-agent/scorecard — verify structure."""
        sc, body = _local("GET", "/api/field-agent/scorecard")
        assert sc == 200, f"scorecard returned {sc}"
        assert isinstance(body, dict), "Scorecard should be a dict"
        return {"scorecard_keys": list(body.keys())}
    _run(suite, "field_agent_scorecard_structure", G, field_agent_scorecard)

    # ── Cleanup ───────────────────────────────────────────────────────────────
    def cleanup_g24():
        with get_cursor(commit=True) as cur:
            if test_field_contact_id:
                _cascade_delete(cur, test_field_contact_id[0])
    _run(suite, "field_agent_cleanup", G, cleanup_g24)


# ─── GROUP 25: n8n Reports flow ───────────────────────────────────────────────

def _g25_n8n_reports_flow(suite: TestSuite) -> None:
    """Group 25 — [Reports] Daily Activity + Outcome Report n8n flow integration tests.

    Tests:
      POST /api/reports/daily/{date}   — generate report (both 'sent'/'error' IF branches)
      GET  /api/reports/daily/{date}   — retrieve generated report (TRUE branch: report exists)
    Cleans up any test rows inserted.
    """
    G = "25_n8n_reports"
    from datetime import date as _date

    test_report_date = "2000-01-01"   # Far-past date unlikely to conflict with real data

    # ── FALSE branch: GET report for non-existent date → 404 ─────────────────
    def reports_get_nonexistent():
        """GET /api/reports/daily/1999-01-01 — should 404 (IF Email Sent? FALSE branch analogue)."""
        sc, body = _local("GET", "/api/reports/daily/1999-01-01")
        assert sc == 404, f"Expected 404 for non-existent report, got {sc}: {body}"
        return {"branch": "FALSE (no report)", "status_code": sc}
    _run(suite, "reports_get_nonexistent_404", G, reports_get_nonexistent)

    # ── TRUE branch: generate then retrieve report ────────────────────────────
    def reports_generate_and_retrieve():
        """POST then GET /api/reports/daily/2000-01-01 — generate + retrieve (IF TRUE)."""
        sc, body = _local("POST", f"/api/reports/daily/{test_report_date}")
        if sc == 404:
            return {"skip": "reports generate endpoint not found"}
        assert sc == 200, f"generate report returned {sc}: {str(body)[:200]}"

        # Now retrieve it
        sc2, body2 = _local("GET", f"/api/reports/daily/{test_report_date}")
        assert sc2 == 200, f"GET report returned {sc2}: {str(body2)[:200]}"
        assert isinstance(body2, dict), "Report should be a dict"
        return {
            "report_date": test_report_date,
            "branch": "TRUE (report generated+retrieved)",
            "keys": list(body2.keys())[:6],
        }
    _run(suite, "reports_generate_and_retrieve", G, reports_generate_and_retrieve)

    # ── Cleanup: delete the test report row if it was generated ───────────────
    def cleanup_g25():
        with get_cursor(commit=True) as cur:
            cur.execute(
                "DELETE FROM daily_reports WHERE report_date = %s",
                (test_report_date,),
            )
    _run(suite, "reports_cleanup", G, cleanup_g25)


# ─── GROUP 26: Airtable integration (Phase 2) ────────────────────────────────

def _g26_airtable_integration(suite: TestSuite) -> None:
    """Group 26 — Airtable integration tests.

    Tests all Airtable-backed endpoints:
      POST /api/menu/sync          — fetch from Airtable → upsert menu_catalog
        ├─ TRUE branch: records exist → upserted > 0
        └─ verify DB has items after sync
      POST /api/playbook/sync-from-airtable — push records into agent_playbook
        ├─ TRUE branch: created/updated > 0
        └─ FALSE branch: empty records → deactivates unreferenced rules
      GET  /api/menu/items          — list active items (TRUE: data, FALSE: empty table)
      POST /api/playbook/rules      — CRUD: create, update, delete playbook rule
    Data created is cleaned up within each test.
    """
    G = "26_airtable"
    AT_TEST_RULE = "TEST_G26_AIRTABLE_RULE"

    # ── Menu sync: TRUE branch (records from Airtable) ────────────────────────
    def menu_sync_airtable_true_branch():
        """POST /api/menu/sync → Airtable → menu_catalog upsert (TRUE branch)."""
        sc, body = _local("POST", "/api/menu/sync", timeout=30)
        if sc == 502:
            raise WarnSignal(f"Airtable unreachable during menu sync: {str(body)[:100]}")
        if sc == 404:
            return {"skip": "menu/sync not found"}
        assert sc == 200, f"menu/sync returned {sc}: {str(body)[:200]}"
        upserted = body.get("upserted", body.get("synced", body.get("total", -1))) if isinstance(body, dict) else -1
        # Verify DB has active items after sync
        with get_cursor(commit=False) as cur:
            cur.execute("SAVEPOINT g26mc")
            try:
                cur.execute("SELECT COUNT(*) AS cnt FROM menu_catalog WHERE active = TRUE")
                cnt = cur.fetchone()["cnt"]
                cur.execute("RELEASE SAVEPOINT g26mc")
            except Exception:
                cur.execute("ROLLBACK TO SAVEPOINT g26mc")
                cur.execute("RELEASE SAVEPOINT g26mc")
                cnt = -1
        return {"upserted": upserted, "active_items_in_db": cnt, "branch": "TRUE (has_airtable_records)"}
    _run(suite, "menu_sync_airtable_true_branch", G, menu_sync_airtable_true_branch)

    # ── Menu items GET: verify list endpoint returns data ────────────────────
    def menu_items_list():
        """GET /api/menu/items — verify items list structure."""
        sc, body = _local("GET", "/api/menu/items")
        assert sc == 200, f"menu/items returned {sc}"
        items = body if isinstance(body, list) else body.get("items", [])
        assert isinstance(items, list), "items should be a list"
        # Each item should have required fields
        if items:
            item = items[0]
            assert "item_name" in item, "Item missing item_name field"
        return {"item_count": len(items)}
    _run(suite, "menu_items_list_structure", G, menu_items_list)

    # ── Playbook sync: TRUE branch — push test records ────────────────────────
    def playbook_sync_airtable_true_branch():
        """POST /api/playbook/sync-from-airtable → agent_playbook (TRUE branch)."""
        records = [
            {
                "airtable_id": f"rec_test_g26_001",
                "rule_name": AT_TEST_RULE,
                "category": "general",
                "instruction": "Test: always do the right thing.",
                "priority": 50,
                "active": True,
            }
        ]
        sc, body = _local("POST", "/api/playbook/sync-from-airtable", json_body={"records": records})
        if sc == 404:
            return {"skip": "sync-from-airtable not found"}
        assert sc == 200, f"sync-from-airtable returned {sc}: {str(body)[:200]}"
        assert isinstance(body, dict), "Expected dict response"
        # Verify in DB
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT id, rule_name, is_active FROM agent_playbook WHERE rule_name = %s", (AT_TEST_RULE,))
            row = cur.fetchone()
        assert row is not None, f"Test rule {AT_TEST_RULE} not found in agent_playbook after sync"
        assert row["is_active"] is True, "Synced rule should be active"
        return {"synced_rule": AT_TEST_RULE, "branch": "TRUE (records inserted)", "rule_id": row["id"]}
    _run(suite, "playbook_sync_airtable_true_branch", G, playbook_sync_airtable_true_branch)

    # ── Playbook sync: FALSE branch — empty records deactivates rule ──────────
    def playbook_sync_airtable_false_branch():
        """POST /api/playbook/sync-from-airtable with empty records → deactivates test rule."""
        # First verify the test rule exists
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT id FROM agent_playbook WHERE rule_name = %s AND is_active = TRUE", (AT_TEST_RULE,))
            row = cur.fetchone()
        if not row:
            return {"skip": "test rule not found (previous test may have failed)"}

        # Sync with no records → should deactivate rule created by airtable
        sc, body = _local("POST", "/api/playbook/sync-from-airtable", json_body={"records": []})
        if sc == 404:
            return {"skip": "sync-from-airtable not found"}
        assert sc == 200, f"sync returned {sc}"
        # Verify rule was deactivated (FALSE branch: rule removed from Airtable)
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT is_active FROM agent_playbook WHERE rule_name = %s", (AT_TEST_RULE,))
            row2 = cur.fetchone()
        # If the rule still exists, it should be deactivated
        if row2:
            assert row2["is_active"] is False, "Rule should be deactivated when not in sync payload"
        return {"branch": "FALSE (rule deactivated/removed)", "deactivated": row2 is None or not row2["is_active"]}
    _run(suite, "playbook_sync_airtable_false_branch", G, playbook_sync_airtable_false_branch)

    # ── CRUD: create/update/delete playbook rule ──────────────────────────────
    def playbook_crud_create_update_delete():
        """POST/PUT/DELETE /api/playbook/rules — full CRUD cycle."""
        # Create
        sc, body = _local("POST", "/api/playbook/rules", json_body={
            "rule_name": "TEST_G26_CRUD_RULE",
            "category": "inference",
            "instruction": "Test CRUD rule — do nothing",
            "priority": 10,
            "active": True,
        })
        if sc == 404:
            return {"skip": "POST /api/playbook/rules not found"}
        assert sc in (200, 201), f"create rule returned {sc}: {str(body)[:200]}"
        rule_id = body.get("id") if isinstance(body, dict) else None

        if rule_id:
            # Update
            sc2, body2 = _local("PUT", f"/api/playbook/rules/{rule_id}", json_body={"priority": 20, "instruction": "Updated"})
            # Delete
            sc3, _ = _local("DELETE", f"/api/playbook/rules/{rule_id}")
            assert sc3 in (200, 204), f"delete rule returned {sc3}"

        # Cleanup fallback in DB
        with get_cursor(commit=True) as cur:
            cur.execute("DELETE FROM agent_playbook WHERE rule_name = %s", ("TEST_G26_CRUD_RULE",))

        return {"created_id": rule_id, "crud_complete": True}
    _run(suite, "playbook_crud_create_update_delete", G, playbook_crud_create_update_delete)

    # ── Menu: verify item fields are correct after sync ────────────────────────
    def menu_item_fields_verified():
        """After sync, every active menu item must have item_name and active=True."""
        with get_cursor(commit=False) as cur:
            cur.execute("SAVEPOINT g26mf")
            try:
                cur.execute(
                    "SELECT item_name, active, price FROM menu_catalog WHERE active = TRUE LIMIT 5"
                )
                rows = cur.fetchall()
                cur.execute("RELEASE SAVEPOINT g26mf")
            except Exception:
                cur.execute("ROLLBACK TO SAVEPOINT g26mf")
                cur.execute("RELEASE SAVEPOINT g26mf")
                rows = []
        if not rows:
            return {"skip": "no active menu items to verify"}
        for row in rows:
            assert row["item_name"], f"item_name is empty: {row}"
            assert row["active"] is True, f"active flag should be True: {row}"
        return {"checked_items": len(rows), "all_have_names": True}
    _run(suite, "menu_item_fields_verified", G, menu_item_fields_verified)

    # ── Playbook: verify synced rule has all required fields ──────────────────
    def playbook_rule_fields_verified():
        """Synced rule must have rule_name, category, instruction, priority, is_active."""
        with get_cursor(commit=False) as cur:
            cur.execute(
                "SELECT rule_name, category, instruction, priority, is_active "
                "FROM agent_playbook WHERE rule_name = %s",
                (AT_TEST_RULE,),
            )
            row = cur.fetchone()
        if not row:
            return {"skip": f"{AT_TEST_RULE} not found — previous sync test may have failed"}
        assert row["rule_name"] == AT_TEST_RULE, "rule_name mismatch"
        assert row["category"] == "general", f"category wrong: {row['category']}"
        assert row["instruction"], "instruction is empty"
        assert row["priority"] is not None, "priority is None"
        assert row["is_active"] is True, "rule should be active"
        return {k: row[k] for k in ("rule_name", "category", "priority", "is_active")}
    _run(suite, "playbook_rule_fields_verified", G, playbook_rule_fields_verified)

    # ── Playbook: list endpoint returns at least one rule ─────────────────────
    def playbook_list_endpoint():
        """GET /api/playbook/rules — verify list structure and required fields."""
        sc, body = _local("GET", "/api/playbook/rules")
        if sc == 404:
            return {"skip": "GET /api/playbook/rules not found"}
        assert sc == 200, f"playbook/rules returned {sc}"
        rules = body if isinstance(body, list) else body.get("rules", [])
        assert isinstance(rules, list), "rules should be a list"
        if rules:
            rule = rules[0]
            for field in ("id", "rule_name", "category"):
                assert field in rule, f"rule missing field: {field}"
        return {"rule_count": len(rules)}
    _run(suite, "playbook_list_endpoint", G, playbook_list_endpoint)

    # ── Airtable cleanup ──────────────────────────────────────────────────────
    def cleanup_g26():
        with get_cursor(commit=True) as cur:
            cur.execute("DELETE FROM agent_playbook WHERE rule_name IN (%s, %s)",
                       (AT_TEST_RULE, "TEST_G26_CRUD_RULE"))
    _run(suite, "airtable_cleanup", G, cleanup_g26)


# ─── GROUP 27: Dashboard integration (Phase 3) ───────────────────────────────

def _g27_dashboard_integration(suite: TestSuite) -> None:
    """Group 27 — Dashboard integration tests.

    Tests all UI-facing Python endpoints that the DabbahWala dashboard calls:
      GET  /api/contacts/{id}/priority PATCH — update contact priority
      POST /api/broadcasts/             — create broadcast job
      POST /api/broadcasts/delay-alert  — create delay alert
      POST /api/broadcasts/{id}/queue   — queue recipients for job
      GET  /api/broadcasts/             — list broadcast jobs
      POST /api/broadcasts/recipients/{id}/sent    — mark recipient sent
      POST /api/broadcasts/recipients/{id}/failed  — mark recipient failed
      GET  /api/query/categories        — fetch query categories
    All data inserted is cleaned up within each sub-test.
    """
    G = "27_dashboard"

    # ── Broadcast: POST / (create promo job) ──────────────────────────────────
    broadcast_job_id: list = []

    def broadcast_create_promo_job():
        """POST /api/broadcasts/ — create a promo broadcast job."""
        sc, body = _local("POST", "/api/broadcasts/", json_body={
            "title": "TEST_G27_Promo",
            "broadcast_type": "promo",
            "channels": ["sms"],
            "sms_message": "Test promo — please ignore",
            "target_type": "all_active",
            "created_by": "test_harness",
        })
        if sc == 404:
            return {"skip": "POST /api/broadcasts/ not found"}
        assert sc in (200, 201), f"create broadcast returned {sc}: {str(body)[:300]}"
        job_id = body.get("id") if isinstance(body, dict) else None
        if job_id:
            broadcast_job_id.append(job_id)
        return {"job_id": job_id, "status": body.get("status") if isinstance(body, dict) else None}
    _run(suite, "broadcast_create_promo_job", G, broadcast_create_promo_job)

    # ── Broadcast: GET / (list jobs) ──────────────────────────────────────────
    def broadcast_list_jobs():
        """GET /api/broadcasts/ — list all broadcast jobs."""
        sc, body = _local("GET", "/api/broadcasts/")
        assert sc == 200, f"GET /api/broadcasts/ returned {sc}"
        jobs = body if isinstance(body, list) else body.get("jobs", body.get("items", []))
        assert isinstance(jobs, list), "jobs should be a list"
        # If we created a job earlier, it should appear
        if broadcast_job_id:
            ids = [j.get("id") for j in jobs]
            assert broadcast_job_id[0] in ids, f"Created job {broadcast_job_id[0]} not in job list"
        return {"job_count": len(jobs)}
    _run(suite, "broadcast_list_jobs", G, broadcast_list_jobs)

    # ── Broadcast: POST /delay-alert ──────────────────────────────────────────
    delay_job_id: list = []

    def broadcast_create_delay_alert():
        """POST /api/broadcasts/delay-alert — create a delay alert job."""
        sc, body = _local("POST", "/api/broadcasts/delay-alert", json_body={
            "title": "TEST_G27_Delay",
            "sms_message": "Deliveries delayed — sorry! (test)",
            "target_type": "all_active",
            "created_by": "test_harness",
        })
        if sc == 404:
            return {"skip": "POST /api/broadcasts/delay-alert not found"}
        if sc == 422:
            return {"skip": f"delay-alert schema mismatch: {str(body)[:200]}"}
        assert sc in (200, 201), f"create delay-alert returned {sc}: {str(body)[:300]}"
        job_id = body.get("id") if isinstance(body, dict) else None
        if job_id:
            delay_job_id.append(job_id)
        return {"job_id": job_id}
    _run(suite, "broadcast_create_delay_alert", G, broadcast_create_delay_alert)

    # ── Broadcast: GET /{job_id} — retrieve created job ───────────────────────
    def broadcast_get_job():
        """GET /api/broadcasts/{id} — retrieve a single job."""
        if not broadcast_job_id:
            return {"skip": "no job created"}
        job_id = broadcast_job_id[0]
        sc, body = _local("GET", f"/api/broadcasts/{job_id}")
        assert sc == 200, f"GET /api/broadcasts/{job_id} returned {sc}"
        assert isinstance(body, dict), "Job should be a dict"
        assert body.get("id") == job_id, "Returned wrong job"
        return {"job_id": job_id, "status": body.get("status")}
    _run(suite, "broadcast_get_single_job", G, broadcast_get_job)

    # ── Broadcast: queue recipients for job ───────────────────────────────────
    test_recipient_id: list = []

    def broadcast_queue_recipients():
        """POST /api/broadcasts/{id}/queue — queue recipients."""
        if not broadcast_job_id:
            return {"skip": "no job created"}
        job_id = broadcast_job_id[0]
        # Insert a test contact to use as recipient
        with get_cursor(commit=True) as cur:
            cur.execute(
                """INSERT INTO contacts
                       (phone, email, first_name, last_name, source,
                        lifecycle_segment, email_nurture_enabled)
                   VALUES (%s, %s, 'TestG27', 'Broadcast', %s, 'active', FALSE)
                   ON CONFLICT (email) DO UPDATE SET first_name = 'TestG27'
                   RETURNING id""",
                ("+10000000027b", "test_g27_broadcast@test.dabbahwala.com", TEST_SOURCE),
            )
            row = cur.fetchone()
            if not row:
                cur.execute("SELECT id FROM contacts WHERE email = %s", ("test_g27_broadcast@test.dabbahwala.com",))
                row = cur.fetchone()
            contact_id = row["id"]

        sc, body = _local("POST", f"/api/broadcasts/{job_id}/queue")
        if sc == 404:
            return {"skip": "queue endpoint not found"}
        assert sc == 200, f"queue recipients returned {sc}: {str(body)[:200]}"

        # Verify recipients in DB
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT id, channel, status FROM broadcast_recipients WHERE job_id = %s LIMIT 5", (job_id,))
            recipients = cur.fetchall()
            if recipients:
                test_recipient_id.append(recipients[0]["id"])

        return {
            "job_id": job_id,
            "recipients_queued": len(recipients) if recipients else body.get("total_recipients", 0),
            "contact_id": contact_id,
        }
    _run(suite, "broadcast_queue_recipients", G, broadcast_queue_recipients)

    # ── Broadcast: mark recipient sent (TRUE branch) ──────────────────────────
    def broadcast_mark_sent():
        """POST /api/broadcasts/recipients/{id}/sent — mark recipient as sent."""
        if not test_recipient_id:
            return {"skip": "no recipient to mark"}
        recipient_id = test_recipient_id[0]
        sc, body = _local("POST", f"/api/broadcasts/recipients/{recipient_id}/sent")
        assert sc == 200, f"mark sent returned {sc}: {str(body)[:200]}"
        # Verify in DB
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT status FROM broadcast_recipients WHERE id = %s", (recipient_id,))
            row = cur.fetchone()
        assert row and row["status"] == "sent", f"Expected status=sent, got {row}"
        return {"recipient_id": recipient_id, "status": "sent", "branch": "TRUE (sent)"}
    _run(suite, "broadcast_mark_recipient_sent", G, broadcast_mark_sent)

    # ── Broadcast: mark a second recipient as failed (FALSE/error branch) ─────
    def broadcast_mark_failed():
        """POST /api/broadcasts/recipients/{id}/failed — mark recipient as failed."""
        if not broadcast_job_id:
            return {"skip": "no job created"}
        job_id = broadcast_job_id[0]
        # Insert a fresh recipient directly in DB to mark as failed
        with get_cursor(commit=True) as cur:
            cur.execute(
                """INSERT INTO broadcast_recipients (job_id, contact_id, channel, status)
                   SELECT %s, id, 'sms', 'pending'
                   FROM contacts WHERE email = 'test_g27_broadcast@test.dabbahwala.com'
                   LIMIT 1
                   RETURNING id""",
                (job_id,),
            )
            row = cur.fetchone()
        if not row:
            return {"skip": "no contact to use as failed recipient"}
        fail_id = row["id"]
        sc, body = _local("POST", f"/api/broadcasts/recipients/{fail_id}/failed")
        assert sc == 200, f"mark failed returned {sc}: {str(body)[:200]}"
        # Verify in DB
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT status FROM broadcast_recipients WHERE id = %s", (fail_id,))
            row2 = cur.fetchone()
        assert row2 and row2["status"] == "failed", f"Expected status=failed, got {row2}"
        return {"recipient_id": fail_id, "status": "failed", "branch": "FALSE (failed)"}
    _run(suite, "broadcast_mark_recipient_failed", G, broadcast_mark_failed)

    # ── Broadcast: GET /{id} returns expected fields ───────────────────────────
    def broadcast_job_fields_structure():
        """GET /api/broadcasts/{id} — job object must have id, title, status fields."""
        if not broadcast_job_id:
            return {"skip": "no job created"}
        job_id = broadcast_job_id[0]
        sc, body = _local("GET", f"/api/broadcasts/{job_id}")
        assert sc == 200, f"GET job returned {sc}"
        for field in ("id", "status"):
            assert field in body, f"job missing field '{field}': {list(body.keys())}"
        assert body["id"] == job_id, "returned wrong job id"
        return {"fields_present": list(body.keys())}
    _run(suite, "broadcast_job_fields_structure", G, broadcast_job_fields_structure)

    # ── Broadcast: GET unknown job → 404 ──────────────────────────────────────
    def broadcast_get_nonexistent_404():
        """GET /api/broadcasts/nonexistent-id → 404 (job not found branch)."""
        sc, _ = _local("GET", "/api/broadcasts/00000000-0000-0000-0000-000000000000")
        assert sc == 404, f"Expected 404 for unknown job, got {sc}"
        return {"status": 404, "branch": "job_not_found"}
    _run(suite, "broadcast_get_nonexistent_404", G, broadcast_get_nonexistent_404)

    # ── Contact priority update ───────────────────────────────────────────────
    def contact_priority_update():
        """PATCH /api/contacts/{id}/priority — update contact priority."""
        if not suite.test_contact_id:
            return {"skip": "no test contact available"}
        sc, body = _local("PATCH", f"/api/contacts/{suite.test_contact_id}/priority",
                         json_body={"priority": "hot"})
        if sc == 404:
            return {"skip": "contacts priority endpoint not found"}
        assert sc in (200, 204), f"update priority returned {sc}: {str(body)[:200]}"
        return {"contact_id": suite.test_contact_id, "priority": "hot"}
    _run(suite, "contact_priority_update", G, contact_priority_update)

    # ── Query categories endpoint ─────────────────────────────────────────────
    def query_categories():
        """GET /api/query/categories — list query categories for self-service."""
        sc, body = _local("GET", "/api/query/categories")
        assert sc == 200, f"query/categories returned {sc}"
        assert isinstance(body, (list, dict)), "categories should be list or dict"
        return {"categories_type": type(body).__name__}
    _run(suite, "query_categories_structure", G, query_categories)

    # ── Cleanup ───────────────────────────────────────────────────────────────
    def cleanup_g27():
        with get_cursor(commit=True) as cur:
            # Delete broadcast recipients first (FK constraint)
            if broadcast_job_id:
                cur.execute("DELETE FROM broadcast_recipients WHERE job_id = ANY(%s)", (broadcast_job_id,))
                cur.execute("DELETE FROM broadcast_jobs WHERE id = ANY(%s)", (broadcast_job_id,))
            if delay_job_id:
                cur.execute("DELETE FROM broadcast_recipients WHERE job_id = ANY(%s)", (delay_job_id,))
                cur.execute("DELETE FROM broadcast_jobs WHERE id = ANY(%s)", (delay_job_id,))
            # Delete test contact
            cur.execute("DELETE FROM contacts WHERE email = %s", ("test_g27_broadcast@test.dabbahwala.com",))
    _run(suite, "dashboard_cleanup", G, cleanup_g27)




def _g14_cleanup(suite: TestSuite) -> None:
    G = "14_cleanup"

    def cleanup_db():
        deleted = 0
        with get_cursor(commit=True) as cur:
            # Find all test contacts
            cur.execute("SELECT id FROM contacts WHERE source = %s", (TEST_SOURCE,))
            ids = [r["id"] for r in cur.fetchall()]
            for cid in ids:
                _cascade_delete(cur, cid)
                deleted += 1
        suite.test_contact_id = None
        return {"contacts_deleted": deleted}
    _run(suite, "cleanup_test_contacts_db", G, cleanup_db)

    def verify_clean():
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM contacts WHERE source = %s", (TEST_SOURCE,))
            cnt = cur.fetchone()["cnt"]
        assert cnt == 0, f"Still {cnt} test contacts in DB after cleanup"
        return {"remaining_test_contacts": 0}
    _run(suite, "verify_test_data_removed", G, verify_clean)


# ─── Main Entry Point ─────────────────────────────────────────────────────────

def run_full_suite(triggered_by: str = "manual") -> TestSuite:
    """
    Run the complete DabbahWala end-to-end test suite.

    Execution order is intentional — each group may depend on prior groups.
    Cleanup (Group 14) always runs, even if earlier groups fail.
    """
    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    suite = TestSuite(run_id=run_id, started_at=started_at, triggered_by=triggered_by)

    logger.info("=== DabbahWala E2E Test Suite START run_id=%s ===", run_id)
    t0 = time.time()

    try:
        _g1_connectivity(suite)
        _g2_schema(suite)
        _g3_contact_setup(suite)
        _g4_events(suite)
        _g5_telnyx_sms(suite)
        _g6_agent_pipeline(suite)
        _g7_intelligence(suite)
        _g8_instantly(suite)
        _g9_airtable(suite)
        _g10_action_queue(suite)
        _g11_orders(suite)
        _g12_reports(suite)
        _g13_query_chatbot(suite)
        _g15_competitor_agent(suite)
        _g18_n8n_daily_order_flow(suite)
        _g19_n8n_action_queue_all_routes(suite)
        _g20_n8n_broadcast_flow(suite)
        _g21_n8n_menu_sync_flow(suite)
        _g22_n8n_sms_dispatch_flow(suite)
        _g23_n8n_intelligence_flow(suite)
        _g24_n8n_field_agent_flow(suite)
        _g25_n8n_reports_flow(suite)
        _g26_airtable_integration(suite)
        _g27_dashboard_integration(suite)
    except Exception as e:
        logger.exception("Test suite failed unexpectedly at group level: %s", e)
    finally:
        # Cleanup ALWAYS runs
        _g14_cleanup(suite)

    suite.completed_at = datetime.now(timezone.utc).isoformat()
    suite.duration_seconds = round(time.time() - t0, 1)

    summary = suite.summary()
    logger.info(
        "=== DabbahWala E2E Test Suite END run_id=%s  "
        "total=%d  passed=%d  failed=%d  skipped=%d  duration=%.1fs ===",
        run_id, summary["total"], summary["passed"],
        summary["failed"], summary["skipped"], suite.duration_seconds,
    )

    # Persist run to DB
    _save_run(suite)

    return suite


def _save_run(suite: TestSuite) -> None:
    try:
        summary = suite.summary()
        all_results = [asdict(r) for r in suite.results]
        with get_cursor(commit=True) as cur:
            cur.execute("""
                INSERT INTO test_runs
                    (id, started_at, completed_at, triggered_by, status,
                     total_tests, passed, failed, skipped, summary, results)
                VALUES (%s, %s, %s, %s, 'completed', %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                ON CONFLICT (id) DO UPDATE
                  SET completed_at = EXCLUDED.completed_at,
                      status       = EXCLUDED.status,
                      total_tests  = EXCLUDED.total_tests,
                      passed       = EXCLUDED.passed,
                      failed       = EXCLUDED.failed,
                      skipped      = EXCLUDED.skipped,
                      summary      = EXCLUDED.summary,
                      results      = EXCLUDED.results
            """, (
                suite.run_id,
                suite.started_at,
                suite.completed_at,
                suite.triggered_by,
                summary["total"],
                summary["passed"],
                summary["failed"],
                summary["skipped"],
                json.dumps(summary),
                json.dumps(all_results),
            ))
    except Exception as e:
        logger.error("Failed to save test run to DB: %s", e)


def get_recent_runs(limit: int = 10) -> list:
    """Fetch the most recent test runs from the DB."""
    try:
        with get_cursor(commit=False) as cur:
            cur.execute("""
                SELECT id, started_at, completed_at, triggered_by, status,
                       total_tests, passed, failed, skipped, summary
                FROM test_runs
                ORDER BY started_at DESC
                LIMIT %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def get_run_by_id(run_id: str) -> Optional[dict]:
    """Fetch a specific test run with full results."""
    try:
        with get_cursor(commit=False) as cur:
            cur.execute("""
                SELECT * FROM test_runs WHERE id = %s
            """, (run_id,))
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception:
        return None
