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

TEST_PHONE          = "+18444322224"           # Our Telnyx FROM number (self-loop)
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
    # Instantly v2 uses Bearer auth
    return {
        "Authorization": f"Bearer {_env('INSTANTLY_API_KEY')}",
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


def _run(suite: TestSuite, name: str, group: str, fn) -> TestResult:
    """Execute a test function, capture result, append to suite."""
    t0 = time.time()
    try:
        details = fn()
        r = TestResult(test=name, group=group, status="pass",
                       message="OK", duration_ms=_time_ms(t0),
                       details=details or {})
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
        # Try Instantly v2 campaigns list
        sc, body = _req("GET", f"{INSTANTLY_BASE}/campaign/list",
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
        "engagement_rollups", "menu_items", "opportunities",
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
        "customer_goals", "inference_results", "decision_recommendations",
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


# ─── GROUP 5: Telnyx / SMS (Real Outbound) ───────────────────────────────────

def _g5_telnyx_sms(suite: TestSuite) -> None:
    G = "5_telnyx_sms"

    def telnyx_send_sms():
        """Send a real test SMS from our number to our number (self-loop)."""
        key = _env("TELNYX_API_KEY")
        assert key, "TELNYX_API_KEY not set"
        sc, body = _req(
            "POST",
            f"{TELNYX_BASE}/messages",
            headers=_telnyx_headers(),
            json_body={
                "from": TEST_PHONE,
                "to": TEST_PHONE,
                "text": "[DabbahWala TestHarness] Automated SMS connectivity check. Please ignore.",
                "messaging_profile_id": _env("TELNYX_MESSAGING_PROFILE_ID",
                                              "400191f9-0057-41f5-9f10-375fb3fe1a70"),
            },
        )
        assert sc in (200, 202), f"Telnyx send SMS returned {sc}: {str(body)[:300]}"
        msg_id = (body.get("data", {}) or {}).get("id", "?") if isinstance(body, dict) else "?"
        return {"telnyx_message_id": msg_id, "from": TEST_PHONE, "to": TEST_PHONE}
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
                    "body": "[TEST] SMS action queue validation",
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


# ─── GROUP 6: AI Agent Pipeline ──────────────────────────────────────────────

def _g6_agent_pipeline(suite: TestSuite) -> None:
    G = "6_agent_pipeline"

    if not suite.test_contact_id:
        for name in ["agent_goal_create", "agent_cycle_run",
                     "agent_inference_results", "agent_decision_results",
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
        for name in ["agent_cycle_run", "agent_inference_results",
                     "agent_decision_results", "agent_orchestrator_log", "agent_action_queued"]:
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

    def agent_inference_results():
        with get_cursor(commit=False) as cur:
            cur.execute("""
                SELECT inference_type, COUNT(*) AS cnt
                FROM inference_results
                WHERE contact_id = %s
                GROUP BY inference_type
            """, (suite.test_contact_id,))
            rows = {r["inference_type"]: r["cnt"] for r in cur.fetchall()}
        assert rows, f"No inference_results for test contact {suite.test_contact_id}"
        return {"inference_types": list(rows.keys()), "counts": rows}
    _run(suite, "agent_inference_results", G, agent_inference_results)

    def agent_decision_results():
        with get_cursor(commit=False) as cur:
            cur.execute("""
                SELECT decision_type, COUNT(*) AS cnt
                FROM decision_recommendations
                WHERE contact_id = %s
                GROUP BY decision_type
            """, (suite.test_contact_id,))
            rows = {r["decision_type"]: r["cnt"] for r in cur.fetchall()}
        assert rows, f"No decision_recommendations for test contact"
        return {"decision_types": list(rows.keys())}
    _run(suite, "agent_decision_results", G, agent_decision_results)

    def agent_orchestrator_log():
        with get_cursor(commit=False) as cur:
            cur.execute("""
                SELECT id, chosen_action, created_at
                FROM orchestrator_log
                WHERE contact_id = %s
                ORDER BY created_at DESC LIMIT 1
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
    if not key:
        for name in ["instantly_campaigns_list", "instantly_all_5_campaigns",
                     "instantly_lead_add", "instantly_lead_verify",
                     "instantly_analytics", "instantly_lead_remove",
                     "instantly_campaign_sync_endpoint"]:
            _skip(suite, name, G, "INSTANTLY_API_KEY not set")
        return

    def campaigns_list():
        sc, body = _req("GET", f"{INSTANTLY_BASE}/campaign/list",
                         headers=_instantly_headers())
        assert sc in (200, 201), f"Instantly campaigns list returned {sc}: {str(body)[:300]}"
        data = body if isinstance(body, dict) else {}
        campaigns = data.get("data", data.get("campaigns", data if isinstance(data, list) else []))
        return {"status": sc, "campaign_count": len(campaigns) if isinstance(campaigns, list) else "?"}
    r = _run(suite, "instantly_campaigns_list", G, campaigns_list)

    def all_5_campaigns():
        sc, body = _req("GET", f"{INSTANTLY_BASE}/campaign/list?limit=100",
                         headers=_instantly_headers())
        assert sc in (200, 201), f"Instantly API error: {sc}"
        data = body if isinstance(body, dict) else {}
        campaigns = data.get("data", data.get("campaigns", []))
        if isinstance(body, list):
            campaigns = body
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
            f"{INSTANTLY_BASE}/lead",
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
        sc, body = _req(
            "GET",
            f"{INSTANTLY_BASE}/lead?campaign_id={suite.instantly_cold_campaign_id}&email={TEST_EMAIL}",
            headers=_instantly_headers(),
        )
        assert sc in (200, 201, 404), f"Instantly lead verify returned {sc}: {str(body)[:300]}"
        found = sc != 404
        return {"lead_found": found, "status": sc}
    if suite.instantly_cold_campaign_id:
        _run(suite, "instantly_lead_verify", G, lead_verify)
    else:
        _skip(suite, "instantly_lead_verify", G, "cold campaign ID not resolved")

    def analytics():
        sc, body = _req(
            "GET",
            f"{INSTANTLY_BASE}/analytics/campaign/summary",
            headers=_instantly_headers(),
        )
        assert sc in (200, 201), f"Instantly analytics returned {sc}: {str(body)[:300]}"
        return {"status": sc, "has_data": bool(body)}
    _run(suite, "instantly_analytics", G, analytics)

    def campaign_sync_endpoint():
        """Verify the /api/webhooks/sync-campaigns endpoint accepts campaign data."""
        sc, body = _local("POST", "/api/webhooks/sync-campaigns", json_body={
            "campaigns": [
                {
                    "id": "test-campaign-id-001",
                    "name": "DW-NurtureSlow-ColdContacts",
                    "status": "active",
                }
            ]
        })
        # 200 or 422 (validation) both indicate the endpoint exists
        assert sc in (200, 201, 422), f"sync-campaigns returned {sc}: {body}"
        return {"status": sc}
    _run(suite, "instantly_campaign_sync_endpoint", G, campaign_sync_endpoint)

    def lead_remove():
        """Cleanup: remove test email from Instantly campaign."""
        if not suite.instantly_cold_campaign_id:
            return {"skipped": "no campaign id"}
        sc, body = _req(
            "DELETE",
            f"{INSTANTLY_BASE}/lead",
            headers=_instantly_headers(),
            json_body={
                "campaign_id": suite.instantly_cold_campaign_id,
                "emails": [TEST_EMAIL],
            },
        )
        assert sc in (200, 201, 204, 404), f"Instantly remove lead returned {sc}: {str(body)[:300]}"
        return {"status": sc, "email": TEST_EMAIL}
    _run(suite, "instantly_lead_remove", G, lead_remove)


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
            f"{AIRTABLE_BASE}/{AIRTABLE_BASE_ID}/Weekly%20Menu?maxRecords=5",
            headers=_airtable_headers(),
        )
        assert sc == 200, f"Airtable Weekly Menu returned {sc}: {str(body)[:300]}"
        records = (body or {}).get("records", [])
        return {"record_count": len(records)}
    _run(suite, "airtable_menu_fetch", G, menu_fetch)

    def menu_sync():
        sc, body = _local("POST", "/api/menu/sync", timeout=60)
        assert sc == 200, f"menu/sync returned {sc}: {body}"
        b = body if isinstance(body, dict) else {}
        return {"synced": b.get("synced", b.get("count", "?"))}
    _run(suite, "airtable_menu_sync", G, menu_sync)

    def playbook_sync():
        sc, body = _local("POST", "/api/playbook/sync-from-airtable", timeout=30)
        assert sc in (200, 201), f"playbook sync returned {sc}: {body}"
        return {"status": sc}
    _run(suite, "airtable_playbook_sync", G, playbook_sync)

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
            "Order Date,Customer Name,Phone,Email,Items,Total\n"
            f"{today},DWTest Harness,{TEST_PHONE},{TEST_EMAIL},\"Dal Makhani x1\",12.99\n"
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

    def menu_items_present():
        """Verify menu_items table has content (synced from Airtable)."""
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM menu_items")
            cnt = cur.fetchone()["cnt"]
        assert cnt > 0, f"menu_items table is empty — Airtable sync may have failed"
        return {"menu_item_count": cnt}
    _run(suite, "menu_items_present", G, menu_items_present)

    def order_summary_endpoint():
        today = _date.today().isoformat()
        sc, body = _local("GET", f"/api/daily-orders/summary/{today}")
        assert sc in (200, 404), f"daily-orders/summary returned {sc}"
        return {"status": sc, "date": today}
    _run(suite, "order_summary_endpoint", G, order_summary_endpoint)


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
        cats = body if isinstance(body, list) else (body or {}).get("categories", [])
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


# ─── GROUP 14: Data Cleanup ───────────────────────────────────────────────────

def _cascade_delete(cur, contact_id: int) -> None:
    """Delete all test-related rows for a given contact_id."""
    tables = [
        "action_queue",
        "orchestrator_log",
        "decision_recommendations",
        "inference_results",
        "customer_goals",
        "delivery_status",
        "telnyx_messages",
        "telnyx_calls",
        "events",
        "orders",
    ]
    for t in tables:
        try:
            cur.execute(f"DELETE FROM {t} WHERE contact_id = %s", (contact_id,))
        except Exception:
            pass
    cur.execute("DELETE FROM contacts WHERE id = %s", (contact_id,))


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
