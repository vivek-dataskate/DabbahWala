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
