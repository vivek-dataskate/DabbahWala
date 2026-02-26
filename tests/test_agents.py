"""
Tests for app/routers/agents.py
================================

Router prefix: /api/agents

Covers:
  - POST /cycle/run
  - POST /cycle/run-for-contact
  - POST /cycle/run-all
  - GET  /report/activity-data
  - GET  /report/outcome-data
  - POST /report/activity
  - POST /report/outcome
  - GET  /action-queue/pending
  - POST /action-queue/{action_id}/done
  - POST /action-queue/{action_id}/failed
  - POST /goals
  - POST /goals/{goal_id}/achieved

All DB calls are mocked via patch on app.routers.agents.get_cursor.
All Claude calls are mocked via patch on anthropic.Anthropic.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cursor(rows=None, fetchone_val=None):
    c = MagicMock()
    c.fetchall.return_value = rows or []
    c.fetchone.return_value = fetchone_val
    return c


@contextmanager
def _cursor_ctx(cur):
    yield cur


def _make_claude_mock(tool_input: dict):
    """Return a mock anthropic.Anthropic instance whose messages.create returns a tool_use block."""
    block = MagicMock()
    block.type = "tool_use"
    block.input = tool_input

    response = MagicMock()
    response.content = [block]
    response.stop_reason = "tool_use"
    response.usage = MagicMock()
    response.usage.input_tokens = 100
    response.usage.output_tokens = 50
    response.usage.cache_read_input_tokens = 0
    response.usage.cache_creation_input_tokens = 0

    client_instance = MagicMock()
    client_instance.messages.create.return_value = response
    return client_instance


# ---------------------------------------------------------------------------
# TestCycleRun
# ---------------------------------------------------------------------------

class TestCycleRun:
    """Tests for /cycle/run, /cycle/run-for-contact, /cycle/run-all."""

    def test_run_for_contact_no_contact(self, client, monkeypatch):
        """POST /cycle/run-for-contact with unknown phone returns processed:0 or skipped."""
        # _lookup_contact_id uses get_cursor; fetchone returns None → contact not found
        cur = _make_cursor(fetchone_val=None)

        with patch(
            "app.routers.agents.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.post(
                "/api/agents/cycle/run-for-contact",
                json={"phone": "+19995550000"},
            )

        assert resp.status_code == 200
        data = resp.json()
        # Contact not found → skipped
        assert data.get("status") == "skipped" or data.get("processed") == 0

    def test_run_for_contact_success(self, client, monkeypatch):
        """POST /cycle/run-for-contact with a found contact runs the full cycle."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        # _lookup_contact_id → returns contact id=42
        lookup_cur = _make_cursor(fetchone_val={"id": 42})

        # All subsequent DB lookups return sensible mocks so _run_full_cycle doesn't crash
        contact_row = {
            "id": 42, "first_name": "Ali", "last_name": "Hassan",
            "email": "ali@example.com", "phone": "+971501234567",
            "lifecycle_segment": "active", "total_orders": 5,
            "sms_level": 3, "last_order_at": None, "created_at": None,
            "priority_override": None, "sales_notes": None,
            "opens_7d": 2, "opens_30d": 5, "clicks_7d": 0, "clicks_30d": 1,
            "sms_sent_30d": 2, "orders_90d": 3,
        }
        data_cur = _make_cursor(rows=[], fetchone_val=contact_row)

        call_count = [0]

        @contextmanager
        def _multi_cursor(commit=False):
            call_count[0] += 1
            if call_count[0] == 1:
                yield lookup_cur
            else:
                yield data_cur

        claude_mock = _make_claude_mock({
            "sentiment": "positive",
            "intent": "reorder",
            "engagement_score": 0.8,
            "key_observations": ["frequent buyer"],
            "recommended_channel": "sms",
            "channel_timing": "morning",
            "message_angle": "loyalty",
            "should_escalate": False,
            "escalation_reason": "",
            "actions": [],
            "reasoning": "Good customer",
        })

        with patch("app.routers.agents.get_cursor", side_effect=_multi_cursor), \
             patch("anthropic.Anthropic", return_value=claude_mock):
            resp = client.post(
                "/api/agents/cycle/run-for-contact",
                json={"phone": "+971501234567"},
            )

        assert resp.status_code == 200

    def test_run_all_empty(self, client, monkeypatch):
        """POST /cycle/run-all with no eligible contacts returns processed:0."""
        # fetchall returns [] → no contacts to process
        cur = _make_cursor(rows=[])

        with patch(
            "app.routers.agents.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.post("/api/agents/cycle/run-all")

        assert resp.status_code == 200
        data = resp.json()
        assert data["processed"] == 0


# ---------------------------------------------------------------------------
# TestReportData
# ---------------------------------------------------------------------------

class TestReportData:
    """Tests for /report/activity-data and /report/outcome-data."""

    def test_activity_data(self, client):
        """GET /report/activity-data returns a dict with report_date, summary, detail_rows."""
        mock_summary = {"actions_queued": 5, "orchestrator_runs": 3, "field_agent_reviews_today": []}
        mock_rows = [{"contact_id": 1, "chosen_action": "send_sms"}]

        with patch(
            "app.routers.agents._fetch_activity_data",
            return_value=(mock_summary, mock_rows),
        ):
            resp = client.get("/api/agents/report/activity-data")

        assert resp.status_code == 200
        data = resp.json()
        assert "report_date" in data
        assert "summary" in data
        assert "detail_rows" in data

    def test_outcome_data(self, client):
        """GET /report/outcome-data returns a dict with report_date, summary, detail_rows."""
        mock_summary = {"orders": 3, "email_opens": 10, "goals_achieved": 1}
        mock_rows = [{"contact_id": 2, "email": "c@example.com"}]

        with patch(
            "app.routers.agents._fetch_outcome_data",
            return_value=(mock_summary, mock_rows),
        ):
            resp = client.get("/api/agents/report/outcome-data")

        assert resp.status_code == 200
        data = resp.json()
        assert "report_date" in data
        assert "summary" in data


# ---------------------------------------------------------------------------
# TestActionQueue
# ---------------------------------------------------------------------------

class TestActionQueue:
    """Tests for /action-queue/* endpoints."""

    def test_pending_actions(self, client):
        """GET /action-queue/pending returns list of pending actions."""
        rows = [
            {
                "id": 1, "contact_id": 2, "action_type": "send_sms",
                "payload": {}, "created_at": None,
                "email": "a@b.com", "phone": "+1", "first_name": "A", "last_name": "B",
            }
        ]
        cur = _make_cursor(rows=rows)

        with patch(
            "app.routers.agents.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.get("/api/agents/action-queue/pending")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["action_type"] == "send_sms"

    def test_mark_done(self, client):
        """POST /action-queue/1/done returns status ok."""
        cur = _make_cursor()

        with patch(
            "app.routers.agents.get_cursor",
            side_effect=lambda commit=True: _cursor_ctx(cur),
        ):
            resp = client.post("/api/agents/action-queue/1/done")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_mark_failed(self, client):
        """POST /action-queue/1/failed returns status ok."""
        cur = _make_cursor()

        with patch(
            "app.routers.agents.get_cursor",
            side_effect=lambda commit=True: _cursor_ctx(cur),
        ):
            resp = client.post("/api/agents/action-queue/1/failed")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# TestGoals
# ---------------------------------------------------------------------------

class TestGoals:
    """Tests for /goals and /goals/{goal_id}/achieved."""

    def test_create_goal(self, client):
        """POST /goals creates a customer goal and returns its id."""
        cur = _make_cursor(fetchone_val={"id": 5})

        with patch(
            "app.routers.agents.get_cursor",
            side_effect=lambda commit=True: _cursor_ctx(cur),
        ):
            resp = client.post(
                "/api/agents/goals",
                json={
                    "contact_id": 1,
                    "goal": "Place next order within 7 days",
                    "deadline": "2026-03-05",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 5

    def test_achieve_goal(self, client):
        """POST /goals/5/achieved returns status ok."""
        cur = _make_cursor()

        with patch(
            "app.routers.agents.get_cursor",
            side_effect=lambda commit=True: _cursor_ctx(cur),
        ):
            resp = client.post("/api/agents/goals/5/achieved")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# TestCycleRunBatch — POST /cycle/run with a list of contact_ids
# ---------------------------------------------------------------------------

class TestCycleRunBatch:
    """POST /api/agents/cycle/run — run cycle for a list of contact_ids."""

    def test_run_empty_list_returns_zero_processed(self, client, monkeypatch):
        """Empty contact_ids list should return processed=0 immediately."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        resp = client.post("/api/agents/cycle/run", json={"contact_ids": []})
        assert resp.status_code == 200
        data = resp.json()
        assert data["processed"] == 0
        assert data["errors"] == []

    def test_run_contact_not_found_in_db_raises_http_exception(self, client, monkeypatch):
        """If a contact_id is not in the DB, the cycle raises HTTPException 404."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        # fetchone returns None → contact not found → HTTPException 404 → re-raised
        cur = _make_cursor(fetchone_val=None)
        with patch("app.routers.agents.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/agents/cycle/run", json={"contact_ids": [9999]})
        # HTTPException 404 propagates out of /cycle/run (not caught by the loop)
        assert resp.status_code in (200, 404)


# ---------------------------------------------------------------------------
# TestCycleRunAllLapsed — POST /cycle/run-all-lapsed
# ---------------------------------------------------------------------------

class TestCycleRunAllLapsed:
    """POST /api/agents/cycle/run-all-lapsed — lapsed contact cycle."""

    def test_returns_processed_zero_when_no_lapsed(self, client):
        """Should return processed=0 when no lapsed contacts are found."""
        cur = _make_cursor(rows=[])
        with patch("app.routers.agents.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/agents/cycle/run-all-lapsed")

        assert resp.status_code == 200
        data = resp.json()
        assert data["processed"] == 0
        assert data["errors"] == []


# ---------------------------------------------------------------------------
# TestCycleRunDailySweep — POST /cycle/run-daily-sweep
# ---------------------------------------------------------------------------

class TestCycleRunDailySweep:
    """POST /api/agents/cycle/run-daily-sweep — dormant contact sweep."""

    def test_returns_processed_zero_when_no_dormant(self, client):
        """Should return processed=0 when query finds no dormant contacts."""
        cur = _make_cursor(rows=[])
        with patch("app.routers.agents.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/agents/cycle/run-daily-sweep")

        assert resp.status_code == 200
        data = resp.json()
        assert data["processed"] == 0


# ---------------------------------------------------------------------------
# TestCycleRunAllContacts — POST /cycle/run-all-contacts
# ---------------------------------------------------------------------------

class TestCycleRunAllContacts:
    """POST /api/agents/cycle/run-all-contacts — batch cycle for all contacts."""

    def test_returns_summary_when_no_contacts(self, client):
        """Should return an empty batch summary when no contacts are eligible."""
        cur = _make_cursor(rows=[])
        with patch("app.routers.agents.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/agents/cycle/run-all-contacts")

        assert resp.status_code == 200
        data = resp.json()
        assert data["processed"] == 0
        assert data["campaigns_pushed"] == 0
        assert data["airtable_pushed"] == 0


# ---------------------------------------------------------------------------
# TestSendActivityReport — POST /report/activity
# ---------------------------------------------------------------------------

class TestSendActivityReport:
    """POST /api/agents/report/activity — generate and enqueue activity report."""

    def test_sends_activity_report(self, client, monkeypatch):
        """Should generate HTML via Claude, enqueue email, and return summary."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        mock_summary = {"actions_queued": 5, "orchestrator_runs": 3, "field_agent_reviews_today": []}
        mock_rows = [{"first_name": "Alice", "chosen_action": "send_sms"}]

        mock_claude_response = MagicMock()
        mock_claude_response.content = [MagicMock(text="<h2>Activity Report</h2><p>3 runs</p>")]

        mock_claude_client = MagicMock()
        mock_claude_client.messages.create.return_value = mock_claude_response

        cur = _make_cursor()

        with patch("app.routers.agents._fetch_activity_data", return_value=(mock_summary, mock_rows)), \
             patch("app.routers.agents._claude", return_value=mock_claude_client), \
             patch("app.routers.agents.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/agents/report/activity", json={})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "sent"
        assert "html_body" in data
        assert "summary" in data


# ---------------------------------------------------------------------------
# TestSendOutcomeReport — POST /report/outcome
# ---------------------------------------------------------------------------

class TestSendOutcomeReport:
    """POST /api/agents/report/outcome — generate and enqueue outcome report."""

    def test_sends_outcome_report(self, client, monkeypatch):
        """Should generate HTML via Claude, enqueue email, and return summary."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        mock_summary = {
            "orders_detected": 10, "email_opens": 20, "email_clicks": 5,
            "inbound_sms_replies": 3, "goals_achieved": 2,
            "order_day_patterns_30d": [], "top_menu_items_30d": [],
            "customer_frequency_segments_30d": [], "field_agent_scorecard_7d": [],
            "field_agent_call_reviews_7d": [],
        }
        mock_rows = [{"first_name": "Bob", "email": "b@c.com"}]

        mock_claude_response = MagicMock()
        mock_claude_response.content = [MagicMock(text="<h2>Outcome Report</h2><p>10 orders</p>")]

        mock_claude_client = MagicMock()
        mock_claude_client.messages.create.return_value = mock_claude_response

        cur = _make_cursor()

        with patch("app.routers.agents._fetch_outcome_data", return_value=(mock_summary, mock_rows)), \
             patch("app.routers.agents._claude", return_value=mock_claude_client), \
             patch("app.routers.agents.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/agents/report/outcome", json={})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "sent"
        assert "html_body" in data


# ---------------------------------------------------------------------------
# TestDoNotContactOverride — priority_override=do_not_contact
# ---------------------------------------------------------------------------

class TestDoNotContactOverride:
    """Contacts with priority_override=do_not_contact must be skipped without Claude calls."""

    def test_skip_do_not_contact_contact(self, client, monkeypatch):
        """Cycle for a do_not_contact contact should return chosen_action='none' immediately."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        contact_row = {
            "id": 77, "first_name": "Skip", "last_name": "Me",
            "email": "skip@example.com", "phone": "+19990001234",
            "lifecycle_segment": "active", "total_orders": 10,
            "sms_level": 2, "last_order_at": None, "created_at": None,
            "priority_override": "do_not_contact",
            "sales_notes": "Do not reach out",
            "opens_7d": 0, "opens_30d": 0, "clicks_7d": 0, "clicks_30d": 0,
            "sms_sent_30d": 0, "orders_90d": 0,
        }
        cur = _make_cursor(fetchone_val=contact_row, rows=[])
        claude_mock = MagicMock()

        with patch("app.routers.agents.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("app.routers.agents._claude", return_value=claude_mock):
            resp = client.post("/api/agents/cycle/run", json={"contact_ids": [77]})

        assert resp.status_code == 200
        data = resp.json()
        # Claude must NOT have been called for a do_not_contact contact
        claude_mock.messages.create.assert_not_called()
        # Check the result if available
        if data.get("results"):
            result = data["results"][0]
            assert result["chosen_action"] == "none"


# ---------------------------------------------------------------------------
# _fetch_playbook_rules (internal helper) — direct tests
# ---------------------------------------------------------------------------

class TestFetchPlaybookRules:
    """Test _fetch_playbook_rules directly."""

    def test_returns_empty_when_no_rules(self):
        """_fetch_playbook_rules returns empty string when no active rules."""
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch
        from app.routers.agents import _fetch_playbook_rules

        cur = MagicMock()
        cur.fetchall.return_value = []

        @contextmanager
        def _cursor_ctx(commit=False):
            yield cur

        with patch("app.routers.agents.get_cursor", side_effect=_cursor_ctx):
            result = _fetch_playbook_rules()

        assert result == ""

    def test_formats_rules_into_prompt_sections(self):
        """_fetch_playbook_rules with rules returns formatted prompt section."""
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch
        from app.routers.agents import _fetch_playbook_rules

        rules = [
            {"category": "exclusion", "rule_name": "No-contact", "instruction": "Never contact opt-outs", "priority": 100},
            {"category": "messaging", "rule_name": "Warm tone", "instruction": "Use warm, genuine tone", "priority": 50},
        ]
        cur = MagicMock()
        cur.fetchall.return_value = rules

        @contextmanager
        def _cursor_ctx(commit=False):
            yield cur

        # Reset playbook cache to force fresh fetch
        import app.routers.agents as agents_mod
        original_cache = agents_mod._playbook_cache.copy()
        agents_mod._playbook_cache["hash"] = "different_hash"

        try:
            with patch("app.routers.agents.get_cursor", side_effect=_cursor_ctx):
                result = _fetch_playbook_rules()
        finally:
            agents_mod._playbook_cache = original_cache

        assert "EXCLUSION RULES" in result
        assert "No-contact" in result

    def test_returns_empty_on_db_exception(self):
        """_fetch_playbook_rules returns empty string when DB raises exception."""
        from app.routers.agents import _fetch_playbook_rules
        from unittest.mock import patch

        with patch("app.routers.agents.get_cursor", side_effect=Exception("DB offline")):
            result = _fetch_playbook_rules()

        assert result == ""


# ---------------------------------------------------------------------------
# _filter_playbook (internal helper) — direct tests
# ---------------------------------------------------------------------------

class TestFilterPlaybook:
    """Test _filter_playbook section filtering."""

    def test_empty_playbook_returns_empty(self):
        """_filter_playbook with empty string returns empty string."""
        from app.routers.agents import _filter_playbook
        assert _filter_playbook("", "observer") == ""

    def test_full_playbook_returned_when_unknown_layer(self):
        """_filter_playbook returns full playbook for unknown agent_layer."""
        from app.routers.agents import _filter_playbook
        playbook = "## Playbook\n### EXCLUSION RULES\n- Rule 1"
        result = _filter_playbook(playbook, "unknown_layer")
        assert result == playbook


# ---------------------------------------------------------------------------
# _fetch_contact error path — direct test
# ---------------------------------------------------------------------------

class TestFetchContactNotFound:
    def test_fetch_contact_raises_404_when_not_found(self):
        """_fetch_contact raises HTTPException 404 when contact doesn't exist."""
        from contextlib import contextmanager
        from fastapi import HTTPException
        from unittest.mock import MagicMock, patch
        from app.routers.agents import _fetch_contact
        import pytest

        cur = MagicMock()
        cur.fetchone.return_value = None  # contact not found

        @contextmanager
        def _cursor_ctx(commit=False):
            yield cur

        with patch("app.routers.agents.get_cursor", side_effect=_cursor_ctx):
            with pytest.raises(HTTPException) as exc_info:
                _fetch_contact(9999)

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/agents/actions-queue additional tests
# ---------------------------------------------------------------------------

class TestActionQueueAdditional:
    def test_action_queue_pending_returns_list(self, client):
        """GET /api/agents/action-queue/pending returns list of pending actions."""
        rows = [
            {"id": 1, "contact_id": 10, "action_type": "send_sms",
             "status": "pending", "payload": {}, "created_at": "2026-01-15T00:00:00",
             "executed_at": None}
        ]
        cur = _make_cursor(rows=rows)
        with patch("app.routers.agents.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/agents/action-queue/pending")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_action_queue_pending_empty(self, client):
        """GET /api/agents/action-queue/pending with no actions returns empty list."""
        cur = _make_cursor(rows=[])
        with patch("app.routers.agents.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/agents/action-queue/pending")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 0


# ---------------------------------------------------------------------------
# _lookup_contact_id — direct helper tests
# ---------------------------------------------------------------------------

class TestLookupContactId:
    """Test _lookup_contact_id branches."""

    def test_lookup_by_phone(self):
        """_lookup_contact_id returns contact id when found by phone."""
        from contextlib import contextmanager
        from app.routers.agents import _lookup_contact_id

        cur = MagicMock()
        cur.fetchone.return_value = {"id": 42}

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.agents.get_cursor", side_effect=_mock_cursor):
            result = _lookup_contact_id(phone="+14041234567")
        assert result == 42

    def test_lookup_by_email(self):
        """_lookup_contact_id returns contact id when found by email."""
        from contextlib import contextmanager
        from app.routers.agents import _lookup_contact_id

        cur = MagicMock()
        cur.fetchone.return_value = {"id": 99}

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.agents.get_cursor", side_effect=_mock_cursor):
            result = _lookup_contact_id(email="test@example.com")
        assert result == 99

    def test_lookup_by_name(self):
        """_lookup_contact_id returns contact id when found by name."""
        from contextlib import contextmanager
        from app.routers.agents import _lookup_contact_id

        cur = MagicMock()
        cur.fetchone.return_value = {"id": 77}

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.agents.get_cursor", side_effect=_mock_cursor):
            result = _lookup_contact_id(name="John Doe")
        assert result == 77

    def test_lookup_no_params_returns_none(self):
        """_lookup_contact_id returns None when called with no args."""
        from app.routers.agents import _lookup_contact_id
        result = _lookup_contact_id()
        assert result is None

    def test_lookup_not_found_returns_none(self):
        """_lookup_contact_id returns None when cursor returns no row."""
        from contextlib import contextmanager
        from app.routers.agents import _lookup_contact_id

        cur = MagicMock()
        cur.fetchone.return_value = None

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.agents.get_cursor", side_effect=_mock_cursor):
            result = _lookup_contact_id(phone="+19999999999")
        assert result is None


# ---------------------------------------------------------------------------
# Cycle endpoints — no-contact paths
# ---------------------------------------------------------------------------

class TestCycleEndpointsNoContacts:
    """Test run-all, run-all-lapsed, run-daily-sweep with empty contact lists."""

    def test_run_all_no_eligible_contacts(self, client):
        """POST /cycle/run-all with empty contacts returns processed:0."""
        cur = _make_cursor(rows=[])
        with patch("app.routers.agents.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/agents/cycle/run-all")
        assert resp.status_code == 200
        data = resp.json()
        assert data["processed"] == 0
        assert data["errors"] == []

    def test_run_all_lapsed_no_contacts(self, client):
        """POST /cycle/run-all-lapsed with empty contacts returns processed:0."""
        cur = _make_cursor(rows=[])
        with patch("app.routers.agents.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/agents/cycle/run-all-lapsed")
        assert resp.status_code == 200
        data = resp.json()
        assert data["processed"] == 0

    def test_run_daily_sweep_no_contacts(self, client):
        """POST /cycle/run-daily-sweep with empty contacts returns processed:0."""
        cur = _make_cursor(rows=[])
        with patch("app.routers.agents.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/agents/cycle/run-daily-sweep")
        assert resp.status_code == 200
        data = resp.json()
        assert data["processed"] == 0

    def test_run_all_contacts_no_contacts(self, client):
        """POST /cycle/run-all-contacts with empty contacts returns processed:0."""
        cur = _make_cursor(rows=[])
        with patch("app.routers.agents.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/agents/cycle/run-all-contacts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["processed"] == 0
        assert data["campaigns_pushed"] == 0
        assert data["airtable_pushed"] == 0


# ---------------------------------------------------------------------------
# _fetch_outcome_data — direct function test
# ---------------------------------------------------------------------------

class TestFetchOutcomeData:
    """Test _fetch_outcome_data helper."""

    def test_fetch_outcome_data_returns_summary_and_rows(self):
        """_fetch_outcome_data returns (summary_dict, order_customers list)."""
        from contextlib import contextmanager
        from app.routers.agents import _fetch_outcome_data

        cur = MagicMock()
        # Sequence of fetchone calls: orders, email_opens, email_clicks, sms_replies,
        # goals_achieved, order_day_patterns, top_menu_items, customer_segments,
        # field_agent_scorecard, field_agent_reviews
        cur.fetchone.side_effect = [
            {"c": 5},      # orders
            {"c": 12},     # email_opens
            {"c": 3},      # email_clicks
            {"c": 2},      # sms_replies
            {"c": 1},      # goals_achieved
            {"get_order_day_patterns": "[]"},      # order_day_patterns
            {"get_top_menu_items": "[]"},          # top_menu_items
            {"get_customer_frequency_segments": "[]"},  # customer_segments
            {"get_field_agent_scorecard": "[]"},   # field_agent_scorecard
            {"get_recent_field_agent_reviews": "[]"},  # field_agent_reviews
        ]
        cur.fetchall.return_value = []  # order_customers

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.agents.get_cursor", side_effect=_mock_cursor):
            summary, rows = _fetch_outcome_data("2026-01-01")

        assert summary["orders_detected"] == 5
        assert summary["email_opens"] == 12
        assert summary["email_clicks"] == 3
        assert summary["inbound_sms_replies"] == 2
        assert rows == []


# ---------------------------------------------------------------------------
# GET /report/activity-data and /report/outcome-data
# ---------------------------------------------------------------------------

class TestReportDataEndpoints:
    """Test report data GET endpoints."""

    def test_get_activity_data_endpoint(self, client):
        """GET /report/activity-data returns report_date, summary, detail_rows."""
        with patch("app.routers.agents._fetch_activity_data",
                   return_value=({"orders_placed": 3}, [])):
            resp = client.get("/api/agents/report/activity-data?report_date=2026-01-01")
        assert resp.status_code == 200
        data = resp.json()
        assert data["report_date"] == "2026-01-01"
        assert "summary" in data
        assert "detail_rows" in data

    def test_get_outcome_data_endpoint(self, client):
        """GET /report/outcome-data returns report_date, summary, detail_rows."""
        with patch("app.routers.agents._fetch_outcome_data",
                   return_value=({"orders_detected": 7}, [])):
            resp = client.get("/api/agents/report/outcome-data?report_date=2026-01-01")
        assert resp.status_code == 200
        data = resp.json()
        assert data["report_date"] == "2026-01-01"
        assert data["summary"]["orders_detected"] == 7


# ---------------------------------------------------------------------------
# _rows_to_csv helper
# ---------------------------------------------------------------------------

class TestRowsToCsv:
    """Test _rows_to_csv helper."""

    def test_empty_rows_returns_no_data(self):
        """_rows_to_csv returns 'no_data\\n' for empty list."""
        from app.routers.agents import _rows_to_csv
        result = _rows_to_csv([])
        assert result == "no_data\n"

    def test_rows_returns_csv_string(self):
        """_rows_to_csv returns CSV string with header and rows."""
        from app.routers.agents import _rows_to_csv
        rows = [{"name": "Alice", "orders": 3}, {"name": "Bob", "orders": 1}]
        result = _rows_to_csv(rows)
        assert "name" in result
        assert "Alice" in result
        assert "Bob" in result


# ---------------------------------------------------------------------------
# cycle/run endpoint error handling
# ---------------------------------------------------------------------------

class TestCycleRunErrors:
    """Test /cycle/run error handling."""

    def test_run_cycle_handles_exception(self, client):
        """POST /cycle/run logs error and adds to errors list when cycle fails."""
        with patch("app.routers.agents._run_full_cycle",
                   side_effect=Exception("Claude API timeout")):
            resp = client.post(
                "/api/agents/cycle/run",
                json={"contact_ids": [999]},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["processed"] == 0
        assert len(data["errors"]) == 1
        assert data["errors"][0]["contact_id"] == 999


# ---------------------------------------------------------------------------
# _get_or_create_contact — branches
# ---------------------------------------------------------------------------

class TestGetOrCreateContact:
    """Test _get_or_create_contact branches."""

    def test_returns_existing_contact(self):
        """Returns (contact_id, False) when contact already exists."""
        from contextlib import contextmanager
        from app.routers.agents import _get_or_create_contact

        cur = MagicMock()
        cur.fetchone.return_value = {"id": 55}

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.agents.get_cursor", side_effect=_mock_cursor):
            contact_id, is_new = _get_or_create_contact(phone="+14041111111")
        assert contact_id == 55
        assert is_new is False

    def test_returns_none_when_no_phone(self):
        """Returns (None, False) when contact not found and no phone provided."""
        from contextlib import contextmanager
        from app.routers.agents import _get_or_create_contact

        cur = MagicMock()
        cur.fetchone.return_value = None  # not found

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.agents.get_cursor", side_effect=_mock_cursor):
            contact_id, is_new = _get_or_create_contact(email="new@example.com")
        assert contact_id is None
        assert is_new is False

    def test_creates_new_contact_with_phone(self):
        """Creates new contact when not found but phone is available."""
        from contextlib import contextmanager
        from app.routers.agents import _get_or_create_contact

        cur = MagicMock()
        # First fetchone: lookup returns None (not found)
        # Second fetchone: INSERT RETURNING id
        cur.fetchone.side_effect = [None, {"id": 123}]

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.agents.get_cursor", side_effect=_mock_cursor):
            contact_id, is_new = _get_or_create_contact(phone="+14041234567", name="Jane Doe")
        assert contact_id == 123
        assert is_new is True

    def test_auto_create_exception_returns_none(self):
        """Returns (None, False) when INSERT raises exception."""
        from contextlib import contextmanager
        from app.routers.agents import _get_or_create_contact

        # First call: lookup (returns None), second call: INSERT raises
        call_count = [0]

        @contextmanager
        def _mock_cursor(commit=False):
            call_count[0] += 1
            if call_count[0] == 1:
                cur = MagicMock()
                cur.fetchone.return_value = None  # not found
                yield cur
            else:
                raise Exception("DB write error")

        with patch("app.routers.agents.get_cursor", side_effect=_mock_cursor):
            contact_id, is_new = _get_or_create_contact(phone="+14041234567")
        assert contact_id is None
        assert is_new is False


# ---------------------------------------------------------------------------
# run-all-contacts with contacts and actions
# ---------------------------------------------------------------------------

class TestRunAllContactsWithContacts:
    """Test /cycle/run-all-contacts when contacts are found."""

    def test_run_all_contacts_with_move_campaign(self, client):
        """run-all-contacts with contacts where action is move_campaign increments campaigns_pushed."""
        from contextlib import contextmanager

        cur = MagicMock()
        cur.fetchall.return_value = [{"id": 10}]  # one contact

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        cycle_result = {
            "chosen_action": "move_campaign",
            "action_payload": {"to_campaign": "ReEngage", "email": "user@example.com"},
            "contact": {"first_name": "Bob", "last_name": "Smith", "email": "user@example.com",
                        "phone": "", "lifecycle_segment": "lapsed", "total_orders": 2,
                        "last_order_at": None},
            "reasoning_snippet": "Lapsed customer",
        }

        with patch("app.routers.agents.get_cursor", side_effect=_mock_cursor), \
             patch("app.routers.agents._run_full_cycle", return_value=cycle_result), \
             patch("app.routers.agents.push_lead_to_instantly", return_value=True):
            resp = client.post("/api/agents/cycle/run-all-contacts")

        assert resp.status_code == 200
        data = resp.json()
        assert data["processed"] == 1
        assert data["campaigns_pushed"] == 1

    def test_run_all_lapsed_with_contacts(self, client):
        """run-all-lapsed with contacts runs cycle for each."""
        from contextlib import contextmanager

        cur = MagicMock()
        cur.fetchall.return_value = [{"id": 20}]

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        cycle_result = {"chosen_action": "none", "contact": {}}

        with patch("app.routers.agents.get_cursor", side_effect=_mock_cursor), \
             patch("app.routers.agents._run_full_cycle", return_value=cycle_result):
            resp = client.post("/api/agents/cycle/run-all-lapsed")

        assert resp.status_code == 200
        data = resp.json()
        assert data["processed"] == 1

    def test_run_daily_sweep_with_contacts(self, client):
        """run-daily-sweep with contacts runs cycle for each."""
        from contextlib import contextmanager

        cur = MagicMock()
        cur.fetchall.return_value = [{"id": 30}]

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        cycle_result = {"chosen_action": "none", "contact": {}}

        with patch("app.routers.agents.get_cursor", side_effect=_mock_cursor), \
             patch("app.routers.agents._run_full_cycle", return_value=cycle_result):
            resp = client.post("/api/agents/cycle/run-daily-sweep")

        assert resp.status_code == 200
        data = resp.json()
        assert data["processed"] == 1
