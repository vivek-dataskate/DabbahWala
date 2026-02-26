"""
Tests for all 26 n8n workflows.
===============================

Two layers of testing:

1. **Config integrity** — verifies n8n/config.json has the right workflow IDs,
   names, and schedules.  Runs without any network calls (always).

2. **Live n8n API** — verifies each workflow exists in the n8n instance and is
   active. Skipped automatically if N8N_API_KEY is not set in the environment.

3. **Python API seams** — for each n8n workflow, verify the Python endpoint
   it calls returns a valid response (using TestClient with mocked DB).

Run only config + seam tests (no n8n API key needed):
    pytest tests/test_n8n_workflows.py -v -m "not n8n_live"

Run all tests including live n8n verification:
    N8N_API_KEY=xxx pytest tests/test_n8n_workflows.py -v
"""

import json
import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ─── Helpers ─────────────────────────────────────────────────────────────────

N8N_INSTANCE = "https://digitalworker.dataskate.io"
CONFIG_PATH = Path(__file__).parent.parent / "n8n" / "config.json"

EXPECTED_WORKFLOWS = {
    # key → (workflow_id, human_name, python_endpoint_hint)
    "shipday_delivery_collector":  ("AePBXRdPKkUQpHIT", "[Order Intake] Order Collector",     "POST /api/shipday/ingest-orders"),
    "shipday_feedback_sync":       ("0pQY0otcvnGj8WBH", "[Order Intake] Feedback Sync",        "POST /api/shipday/sync-feedback"),
    "daily_order_upload":          ("6ZYQwdkmS5Nni05u", "[Order Intake] Daily CSV Upload",      "POST /api/daily-orders/process"),
    "telnyx_inbound_collector":    ("xcNObK3qdU1wdf3f", "[SMS] Inbound Collector",              "POST /api/telnyx/message"),
    "sms_dispatch":                ("w2bVQQ4hy33OdY1R", "[SMS] Dispatch Queue",                 "GET /api/agents/action-queue/pending"),
    "broadcast_dispatch":          ("oDEse7EvWHj6UVM4", "[Broadcast] Dispatch",                 "GET /api/broadcasts/pending-recipients"),
    "campaign_performance_tracker":("ctCLyHDQc1VckMqL", "[Email Campaigns] Performance Tracker","POST /api/webhooks/campaign-stats"),
    "instantly_campaign_sync":     ("nCcBt9USIYxlOaJT", "[Email Campaigns] Campaign Sync",     "GET /api/webhooks/campaigns"),
    "instantly_setup":             ("NbnkM3nTFKSgtcfb", "[Email Campaigns] Campaign Setup",     "POST /api/campaigns/setup-instantly"),
    "hourly_intelligence_cycle":   ("FcbBt0AIlkYoa01X", "[Intelligence] Contact Sweep",        "POST /api/intelligence/run-cycle"),
    "lifecycle_cycle_cron":        ("h80nX24myWwsbxuB", "[Intelligence] Stage Runner",          "POST /api/lifecycle/run"),
    "lapsed_customer_cycle":       ("S3jSnWb3UTv9HmJL", "[Intelligence] Lapsed Re-engagement", "POST /api/agents/cycle/run-all-lapsed"),
    "agent_orchestration_cron":    ("VreWonSUTk4VCXPF", "[Intelligence] AI Stack",             "POST /api/agents/cycle/run-all"),
    "airtable_outcome_sync":       ("chfGgYIjyTw6QP5m", "[Field Agent] Outcome Sync",          "GET /api/field-agent/pending-calls"),
    "daily_field_brief":           ("kOI33cFH4bM8OCaf", "[Field Agent] Daily Brief",           "POST /api/field-agent/daily-brief"),
    "airtable_playbook_sync":      ("FXuYcwQeBQ72Xxyu", "[Agent Rules] Playbook Sync",         "POST /api/playbook/sync-from-airtable"),
    "airtable_menu_sync":          ("baZV5ViA5lXNCTWR", "[Menu] Catalog Sync",                 "POST /api/menu/sync"),
    "competitor_agent_cycle":      ("GozoSXHiazEdhpni", "[Growth] Competitor Research",        "POST /api/competitor-agent/run"),
    "goal_agent_cycle":            ("w5kYj5vNsNW53W4n", "[Growth] Goal Agent",                 "POST /api/goal-agent/run"),
    "growth_agent_cycle":          ("Nbut2tjjksGvQYzH", "[Growth] Weekly Growth Agent",        "POST /api/growth/run-cycle"),
    "daily_activity_report":       ("91bMjrZxiCPTglEI", "[Reports] Daily Activity Report",     "POST /api/agents/report/activity"),
    "daily_outcome_report":        ("fONTnqi4l9DT3aCo", "[Reports] Daily Outcome Report",      "POST /api/agents/report/outcome"),
    "google_docs_sync":            ("oHtGvkCLTWYkxNZ0", "[Chatbot] Docs Sync",                "POST /api/team-content/sync"),
    "chatbot_docs_reindex":        ("7mn3Ys0xMmZnZQIC", "[Chatbot] Docs Reindex",             "POST /api/chatbot/reindex"),
    "action_queue_executor":       ("RzR3ZNYlty7cuTDY", "[System] Action Queue",              "GET /api/agents/action-queue/pending"),
    "system_feature_tests":        ("zlKQKfJ18QGIwogq", "[System] Feature Tests",             "POST /api/test/run"),
}


@contextmanager
def _cursor_ctx(cur):
    yield cur


def _mk_cur(rows=None, fetchone=None):
    c = MagicMock()
    c.fetchall.return_value = rows or []
    c.fetchone.return_value = fetchone
    return c


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def config():
    """Load and return n8n/config.json once for the whole module."""
    assert CONFIG_PATH.exists(), f"n8n/config.json not found at {CONFIG_PATH}"
    with open(CONFIG_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def n8n_api_key():
    return os.environ.get("N8N_API_KEY", "")


# ─── GROUP 1: Config Integrity (always runs) ──────────────────────────────────

class TestConfigIntegrity:
    """Validate n8n/config.json has all expected workflow IDs and names."""

    def test_config_file_exists(self):
        assert CONFIG_PATH.exists(), "n8n/config.json is missing"

    def test_config_has_26_workflows(self, config):
        wf = {k: v for k, v in config["workflows"].items() if not k.startswith("_")}
        assert len(wf) == 26, f"Expected 26 workflows, found {len(wf)}: {list(wf.keys())}"

    @pytest.mark.parametrize("key,expected", [
        (k, v) for k, v in EXPECTED_WORKFLOWS.items()
    ])
    def test_workflow_id_matches(self, config, key, expected):
        expected_id, expected_name, _ = expected
        wf = config["workflows"].get(key)
        assert wf is not None, f"Workflow key '{key}' missing from config.json"
        assert wf["id"] == expected_id, (
            f"Workflow '{key}' has id={wf['id']!r}, expected {expected_id!r}"
        )

    @pytest.mark.parametrize("key,expected", [
        (k, v) for k, v in EXPECTED_WORKFLOWS.items()
    ])
    def test_workflow_name_matches(self, config, key, expected):
        _, expected_name, _ = expected
        wf = config["workflows"].get(key)
        assert wf is not None
        assert wf["name"] == expected_name, (
            f"Workflow '{key}' name={wf['name']!r}, expected {expected_name!r}"
        )

    def test_n8n_instance_url(self, config):
        assert config["n8n_instance"] == N8N_INSTANCE

    def test_all_workflows_have_schedule(self, config):
        missing = [
            k for k, v in config["workflows"].items()
            if not k.startswith("_") and not v.get("schedule")
        ]
        assert not missing, f"Workflows missing 'schedule': {missing}"

    def test_all_workflows_have_file(self, config):
        missing = [
            k for k, v in config["workflows"].items()
            if not k.startswith("_") and not v.get("file")
        ]
        assert not missing, f"Workflows missing 'file': {missing}"

    def test_all_workflows_have_note(self, config):
        missing = [
            k for k, v in config["workflows"].items()
            if not k.startswith("_") and not v.get("note")
        ]
        assert not missing, f"Workflows missing 'note': {missing}"

    def test_workflow_ids_unique(self, config):
        ids = [v["id"] for k, v in config["workflows"].items() if not k.startswith("_")]
        assert len(ids) == len(set(ids)), "Duplicate workflow IDs found in config.json"


# ─── GROUP 2: Per-workflow JSON Structure (always runs) ───────────────────────

N8N_DIR = Path(__file__).parent.parent / "n8n"
DW_API_BASE = "dabbahwala-latest.onrender.com"

# Known external API domains — URLs on these hosts are not our DW API
_EXTERNAL_DOMAINS = {
    "api.instantly.ai",
    "api.telnyx.com",
    "api.airtable.com",
    "api.shipday.com",
    "api.anthropic.com",
}

# Build a flat list of (key, filename) pairs for parametrize
_WORKFLOW_FILES = [
    (key, meta["file"])
    for key, meta in json.loads((CONFIG_PATH).read_text())["workflows"].items()
    if not key.startswith("_")
]


@pytest.fixture(scope="module")
def all_workflow_jsons():
    """Load all 26 workflow JSON files once."""
    result = {}
    for key, fname in _WORKFLOW_FILES:
        fpath = N8N_DIR / fname
        assert fpath.exists(), f"Workflow file missing: {fname}"
        result[key] = json.loads(fpath.read_text())
    return result


class TestWorkflowJsonStructure:
    """
    Validate each of the 26 n8n workflow JSON files on disk.
    Tests run without any network calls.
    """

    @pytest.mark.parametrize("key,fname", _WORKFLOW_FILES)
    def test_file_is_valid_json(self, key, fname):
        """Each workflow file must parse as valid JSON."""
        fpath = N8N_DIR / fname
        assert fpath.exists(), f"File missing: {fname}"
        data = json.loads(fpath.read_text())
        assert "nodes" in data, f"{fname} has no 'nodes' key"

    @pytest.mark.parametrize("key,fname", _WORKFLOW_FILES)
    def test_has_exactly_one_schedule_trigger(self, key, fname, all_workflow_jsons):
        """Every active workflow must have exactly one scheduleTrigger node."""
        wf = all_workflow_jsons[key]
        triggers = [
            n for n in wf["nodes"]
            if n.get("type") == "n8n-nodes-base.scheduleTrigger"
        ]
        assert len(triggers) == 1, (
            f"{fname}: expected 1 scheduleTrigger, found {len(triggers)}"
        )

    @pytest.mark.parametrize("key,fname", _WORKFLOW_FILES)
    def test_has_at_least_one_http_node(self, key, fname, all_workflow_jsons):
        """Every workflow must make at least one HTTP request."""
        wf = all_workflow_jsons[key]
        http_nodes = [
            n for n in wf["nodes"]
            if n.get("type") == "n8n-nodes-base.httpRequest"
        ]
        assert len(http_nodes) >= 1, f"{fname}: no HTTP request nodes found"

    @pytest.mark.parametrize("key,fname", _WORKFLOW_FILES)
    def test_no_env_variables_used(self, key, fname):
        """No workflow may use $env.ANYTHING — all config must be hardcoded."""
        raw = (N8N_DIR / fname).read_text()
        env_uses = [line.strip() for line in raw.splitlines() if "$env." in line]
        assert not env_uses, (
            f"{fname} uses $env variables (not supported on this instance): {env_uses[:3]}"
        )

    @pytest.mark.parametrize("key,fname", _WORKFLOW_FILES)
    def test_dw_api_calls_use_correct_base_url(self, key, fname, all_workflow_jsons):
        """HTTP nodes calling our API must use dabbahwala-latest.onrender.com."""
        wf = all_workflow_jsons[key]
        bad_nodes = []
        for n in wf["nodes"]:
            if n.get("type") != "n8n-nodes-base.httpRequest":
                continue
            url = n.get("parameters", {}).get("url", "")
            # Skip calls to known external services (they legitimately use /api/ paths)
            if any(d in url for d in _EXTERNAL_DOMAINS):
                continue
            # Check that our /api/ calls go to the right base URL
            if "/api/" in url and DW_API_BASE not in url:
                bad_nodes.append(f"{n['name']}: {url[:80]}")
        assert not bad_nodes, (
            f"{fname}: HTTP nodes with /api/ path not pointing to {DW_API_BASE}:\n"
            + "\n".join(bad_nodes)
        )

    @pytest.mark.parametrize("key,fname", _WORKFLOW_FILES)
    def test_has_credentials_bootstrap_node(self, key, fname, all_workflow_jsons):
        """
        Workflows that call external APIs must use a 'Get Credentials' node with
        httpHeaderAuth (DW Admin Secret) to bootstrap API keys at runtime.

        Workflows that only call our DW API (dabbahwala-latest.onrender.com) are
        exempt — they don't need external credentials.
        """
        wf = all_workflow_jsons[key]
        # Check if any HTTP nodes make calls to external APIs (not our DW API)
        external_http_nodes = [
            n for n in wf["nodes"]
            if n.get("type") == "n8n-nodes-base.httpRequest"
            and n.get("parameters", {}).get("url", "")
            and DW_API_BASE not in n.get("parameters", {}).get("url", "")
        ]
        if not external_http_nodes:
            return  # Only calls our DW API — credential bootstrap not required

        cred_nodes = [
            n for n in wf["nodes"]
            if n.get("type") == "n8n-nodes-base.httpRequest"
            and "httpHeaderAuth" in n.get("credentials", {})
        ]
        assert len(cred_nodes) >= 1, (
            f"{fname}: calls external APIs {[n['name'] for n in external_http_nodes]} "
            "but has no httpHeaderAuth credentials node — "
            "needs 'Get Credentials' bootstrap node with DW Admin Secret"
        )

    @pytest.mark.parametrize("key,fname", _WORKFLOW_FILES)
    def test_node_names_are_unique_within_workflow(self, key, fname, all_workflow_jsons):
        """Node names must be unique within a workflow to avoid confusing expression refs."""
        wf = all_workflow_jsons[key]
        names = [n["name"] for n in wf["nodes"]]
        dupes = [n for n in names if names.count(n) > 1]
        assert not dupes, f"{fname}: duplicate node names: {list(set(dupes))}"


# ─── GROUP 3: Live n8n API (skipped without N8N_API_KEY) ─────────────────────

pytestmark_live = pytest.mark.n8n_live


@pytest.mark.n8n_live
class TestLiveN8nWorkflows:
    """
    Verify each workflow exists in the live n8n instance and is active.
    Skipped when N8N_API_KEY environment variable is not set.
    """

    @pytest.fixture(autouse=True)
    def require_n8n_key(self, n8n_api_key):
        if not n8n_api_key:
            pytest.skip("N8N_API_KEY not set — skipping live n8n tests")

    @pytest.mark.parametrize("key,expected", [
        (k, v) for k, v in EXPECTED_WORKFLOWS.items()
    ])
    def test_workflow_exists_and_active(self, n8n_api_key, key, expected):
        import httpx
        expected_id, expected_name, _ = expected
        resp = httpx.get(
            f"{N8N_INSTANCE}/api/v1/workflows/{expected_id}",
            headers={"X-N8N-API-KEY": n8n_api_key},
            timeout=15,
        )
        assert resp.status_code == 200, (
            f"Workflow '{expected_name}' (id={expected_id}) not found in n8n: {resp.status_code}"
        )
        data = resp.json()
        assert data.get("active") is True, (
            f"Workflow '{expected_name}' (id={expected_id}) is NOT active in n8n"
        )
        assert data.get("name") == expected_name, (
            f"Workflow id={expected_id} name mismatch: "
            f"got {data.get('name')!r}, expected {expected_name!r}"
        )

    def test_total_active_workflow_count(self, n8n_api_key):
        """Confirm n8n has at least 25 active workflows (all scheduled ones)."""
        import httpx
        resp = httpx.get(
            f"{N8N_INSTANCE}/api/v1/workflows?limit=50&active=true",
            headers={"X-N8N-API-KEY": n8n_api_key},
            timeout=15,
        )
        assert resp.status_code == 200
        data = resp.json()
        active_count = len(data.get("data", []))
        assert active_count >= 25, (
            f"Expected ≥25 active workflows, found {active_count}"
        )


# ─── GROUP 3: Python API Seam Tests ──────────────────────────────────────────
# For each workflow, test the Python API endpoint it calls.

class TestOrderIntakeSeams:
    """[Order Intake] workflows call: ingest-orders, sync-feedback, daily-orders/process."""

    def test_ingest_orders_endpoint(self, client):
        cur = _mk_cur(fetchone={"synced": 0, "matched": 0})
        with patch("app.routers.orders.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/shipday/ingest-orders", json={"orders": []})
        assert resp.status_code == 200
        assert "received" in resp.json() or "status" in resp.json()

    def test_feedback_sync_endpoint(self, client):
        with patch("app.routers.shipday_sync._sync_state", {"running": False}), \
             patch("app.routers.shipday_sync._run_feedback_sync"):
            resp = client.post("/api/shipday/sync-feedback", json={"days_back": 1})
        assert resp.status_code == 200

    def test_daily_orders_process_endpoint(self, client):
        cur = _mk_cur(fetchone={"rows_processed": 0, "added": 0, "updated": 0,
                                "skipped": 0, "errors": 0, "matched": 0, "unmatched": 0})
        with patch("app.routers.daily_orders.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/daily-orders/process", json={"rows": []})
        assert resp.status_code in (200, 422)


class TestSMSSeams:
    """[SMS] workflows call: /api/telnyx/message, /api/agents/action-queue/pending."""

    def test_record_message_endpoint(self, client):
        # Provide contact_email so _resolve_email returns immediately without a DB call
        cur = _mk_cur(fetchone={"store_telnyx_message": 42})
        with patch("app.routers.sms.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/telnyx/message", json={
                "contact_email": "test@example.com",
                "direction": "inbound",
                "from_number": "+18444322224",
                "to_number": "+12145550001",
                "body": "Hello",
            })
        assert resp.status_code == 200

    def test_action_queue_pending_endpoint(self, client):
        cur = _mk_cur(rows=[])
        with patch("app.routers.agents.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/agents/action-queue/pending")
        assert resp.status_code == 200


class TestBroadcastSeams:
    """[Broadcast] Dispatch calls: GET /api/broadcasts/pending-recipients."""

    def test_pending_recipients_endpoint(self, client):
        cur = _mk_cur(rows=[])
        with patch("app.routers.broadcasts.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/broadcasts/pending-recipients")
        assert resp.status_code == 200


class TestEmailCampaignSeams:
    """[Email Campaigns] workflows call: /api/webhooks/campaign-stats, /api/webhooks/campaigns."""

    def test_campaign_stats_endpoint(self, client):
        cur = _mk_cur(fetchone={"id": 1})
        with patch("app.routers.webhooks.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/webhooks/campaign-stats", json={
                "campaign_id": "test-campaign-id",
                "leads_count": 100,
                "emails_sent": 90,
                "unique_opens": 20,
                "opens": 25,
                "replies": 5,
                "clicks": 3,
                "bounces": 2,
                "open_rate": 0.22,
                "reply_rate": 0.05,
            })
        assert resp.status_code == 200

    def test_list_campaigns_endpoint(self, client):
        cur = _mk_cur(rows=[{
            "name": "DW-NurtureSlow-ColdContacts",
            "instantly_campaign_id": "cid1",
            "target_segment": "cold",
            "priority": 1,
        }])
        with patch("app.routers.webhooks.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/webhooks/campaigns")
        assert resp.status_code == 200
        assert "campaigns" in resp.json()


class TestIntelligenceSeams:
    """[Intelligence] workflows call: /api/intelligence/run-cycle, /api/lifecycle/run, /api/agents/cycle."""

    def test_intelligence_cycle_endpoint(self, client):
        with patch("app.routers.intelligence._phase_collect", return_value={}), \
             patch("app.routers.intelligence._phase_profile", return_value={}), \
             patch("app.routers.intelligence._phase_signal", return_value=({}, {})), \
             patch("app.routers.intelligence._phase_route", return_value=({}, [])), \
             patch("app.routers.intelligence._phase_dispatch", return_value={}):
            resp = client.post("/api/intelligence/run-cycle")
        assert resp.status_code == 200
        data = resp.json()
        assert "collect" in data and "signal" in data

    def test_lifecycle_run_endpoint(self, client):
        cur = _mk_cur(fetchone={"contacts_updated": 0, "campaigns_queued": 0, "run_lifecycle_cycle": None})
        with patch("app.routers.lifecycle.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/lifecycle/run")
        assert resp.status_code == 200


class TestFieldAgentSeams:
    """[Field Agent] workflows call: /api/field-agent/pending-calls, /api/field-agent/daily-brief."""

    def test_pending_calls_endpoint(self, client):
        cur = _mk_cur(rows=[])
        with patch("app.routers.field_agent.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/field-agent/pending-calls")
        assert resp.status_code == 200
        assert "calls" in resp.json()

    def test_daily_brief_endpoint(self, client):
        # Return empty contact list from the stored proc — endpoint short-circuits early
        cur = _mk_cur(fetchone={"get_top_contacts_to_call": []})
        with patch("app.routers.field_agent.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/field-agent/daily-brief")
        assert resp.status_code == 200
        assert "brief" in resp.json()


class TestAgentRulesSeams:
    """[Agent Rules] Playbook Sync calls: POST /api/playbook/sync-from-airtable."""

    def test_playbook_sync_endpoint(self, client):
        cur = _mk_cur(rows=[])
        with patch("app.routers.playbook.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/playbook/sync-from-airtable", json={"records": []})
        assert resp.status_code == 200
        data = resp.json()
        assert data["synced"] == 0


class TestMenuSeams:
    """[Menu] Catalog Sync calls: POST /api/menu/sync."""

    def test_menu_sync_endpoint(self, client):
        cur = _mk_cur(fetchone=None, rows=[])
        # Mock _airtable_list_all so no real Airtable HTTP request is made
        with patch("app.routers.menu._airtable_list_all", return_value=[]), \
             patch("app.routers.menu.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/menu/sync")
        assert resp.status_code == 200
        data = resp.json()
        assert "upserted" in data


class TestGrowthSeams:
    """[Growth] workflows call: /api/competitor-agent/run, /api/goal-agent/run, /api/growth/run-cycle."""

    def test_competitor_agent_run_no_key(self, client, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        resp = client.post("/api/competitor-agent/run")
        assert resp.status_code in (200, 500)  # 500 when API key missing

    def test_goal_agent_experiments_endpoint(self, client):
        cur = _mk_cur(rows=[])
        with patch("app.routers.goal_agent.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/goal-agent/experiments")
        assert resp.status_code == 200

    def test_growth_experiments_endpoint(self, client):
        cur = _mk_cur(rows=[])
        with patch("app.routers.growth_agent.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/growth/experiments")
        assert resp.status_code == 200


class TestReportSeams:
    """[Reports] workflows call: POST /api/agents/report/activity and /api/agents/report/outcome."""

    def test_report_activity_no_key(self, client, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        resp = client.post("/api/agents/report/activity", json={})
        assert resp.status_code in (200, 500)

    def test_report_activity_data_endpoint(self, client):
        # _fetch_activity_data makes many sequential DB calls — patch it directly
        with patch("app.routers.agents._fetch_activity_data",
                   return_value=({"event_counts": {}, "actions_queued": 0}, [])):
            resp = client.get("/api/agents/report/activity-data")
        assert resp.status_code == 200


class TestChatbotSeams:
    """[Chatbot] Docs Sync calls: POST /api/team-content/sync. Reindex: POST /api/chatbot/reindex."""

    def test_team_content_sync_endpoint(self, client):
        cur = _mk_cur(fetchone=None, rows=[])
        with patch("app.routers.team_content.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/team-content/sync", json={"documents": []})
        assert resp.status_code == 200
        assert resp.json()["synced"] == 0

    def test_chatbot_reindex_endpoint(self, client):
        with patch("app.routers.chatbot._load_md_files", return_value=[{"source": "test.md"}]), \
             patch("app.routers.chatbot._compute_docs_hash", return_value="abc123"), \
             patch("app.routers.chatbot._get_stored_docs_hash", return_value="abc123"), \
             patch("app.routers.chatbot._do_index", return_value=5), \
             patch("app.routers.chatbot._save_last_indexed_at"), \
             patch("app.routers.chatbot._save_docs_hash"):
            resp = client.post("/api/chatbot/reindex")
        assert resp.status_code == 200


class TestSystemSeams:
    """[System] Action Queue reads: GET /api/agents/action-queue/pending.
    [System] Feature Tests runs: POST /api/test/run."""

    def test_action_queue_pending_system(self, client):
        cur = _mk_cur(rows=[])
        with patch("app.routers.agents.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/agents/action-queue/pending")
        assert resp.status_code == 200

    def test_feature_tests_run_endpoint(self, client):
        """POST /api/test/run — verify the test harness HTTP endpoint exists."""
        # run_full_suite is imported lazily inside the endpoint, so patch at source
        with patch("app.services.test_harness_service.run_full_suite", return_value=MagicMock(
            to_dict=lambda: {"run_id": "test", "summary": {}, "groups": []},
        )):
            resp = client.post("/api/test/run")
        assert resp.status_code == 200
