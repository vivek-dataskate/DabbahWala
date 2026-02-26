"""
Tests for app/routers/field_agent.py
======================================

Router prefix: /api/field-agent

Covers:
  - POST /analyze-call/{call_id}
  - POST /daily-brief
  - GET  /reviews
  - GET  /scorecard
  - GET  /pending-calls

All DB calls are mocked via patch on app.routers.field_agent.get_cursor.
All Claude calls are mocked via patch on anthropic.Anthropic.
"""

import json
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


def _make_claude_mock(tool_input: dict, tool_name: str = "call_review"):
    """Return a mock anthropic.Anthropic client whose messages.create returns a tool_use block."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = tool_input

    response = MagicMock()
    response.content = [block]
    response.stop_reason = "tool_use"
    response.usage = MagicMock()
    response.usage.input_tokens = 400
    response.usage.output_tokens = 200

    client_instance = MagicMock()
    client_instance.messages.create.return_value = response
    return client_instance


# ---------------------------------------------------------------------------
# TestAnalyzeCall
# ---------------------------------------------------------------------------

class TestAnalyzeCall:
    """Tests for POST /analyze-call/{call_id}."""

    def test_analyze_call_not_found(self, client):
        """POST /analyze-call/{call_id} when call not found returns 404."""
        # _fetch_call uses get_cursor and raises HTTPException when fetchone returns None
        cur = _make_cursor(fetchone_val=None)

        with patch(
            "app.routers.field_agent.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.post("/api/field-agent/analyze-call/9999")

        assert resp.status_code == 404

    def test_analyze_call_with_mock_claude(self, client, monkeypatch):
        """POST /analyze-call/{call_id} with valid call runs Claude and returns review."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        call_row = {
            "id": 1,
            "contact_id": 2,
            "direction": "outbound",
            "from_number": "+18444322224",
            "to_number": "+971501234567",
            "duration_sec": 120,
            "transcript": "Good call, customer seemed interested",
            "summary": "Customer interested in reorder",
            "agent_name": "Ahmed",
            "started_at": None,
            "ended_at": None,
            "first_name": "Ali",
            "last_name": "Hassan",
            "phone": "+971501234567",
            "email": "ali@example.com",
            "lifecycle_segment": "active",
            "total_orders": 5,
            "last_order_at": None,
        }

        review_input = {
            "pitch_quality_score": 7.5,
            "asked_for_order": True,
            "customer_signal": "interested",
            "objections_raised": [],
            "honest_assessment": "Good call. Ahmed asked for the order.",
            "recommended_next_action": "hand_to_automation",
            "next_action_timing": "24h",
            "next_action_reason": "Customer is warm — follow up by SMS",
        }

        call_cur = _make_cursor(fetchone_val=call_row)
        orders_cur = _make_cursor(rows=[])
        opp_cur = _make_cursor(fetchone_val=None)
        review_insert_cur = _make_cursor(fetchone_val={"id": 10})
        opportunity_insert_cur = _make_cursor(fetchone_val={"create_opportunity": 20})

        call_count = [0]

        @contextmanager
        def _multi_cursor(commit=False):
            call_count[0] += 1
            if call_count[0] == 1:
                yield call_cur       # _fetch_call
            elif call_count[0] == 2:
                yield orders_cur     # _fetch_customer_order_history
            elif call_count[0] == 3:
                yield opp_cur        # _fetch_open_opportunity
            elif call_count[0] == 4:
                yield opportunity_insert_cur  # _create_opportunity
            else:
                yield review_insert_cur       # INSERT INTO field_agent_reviews

        claude_mock = _make_claude_mock(review_input)

        with patch("app.routers.field_agent.get_cursor", side_effect=_multi_cursor), \
             patch("anthropic.Anthropic", return_value=claude_mock):
            resp = client.post("/api/field-agent/analyze-call/1")

        assert resp.status_code == 200
        data = resp.json()
        assert "review_id" in data
        assert "call_id" in data
        assert "review" in data


# ---------------------------------------------------------------------------
# TestDailyBrief
# ---------------------------------------------------------------------------

class TestDailyBrief:
    """Tests for POST /daily-brief."""

    def test_daily_brief_empty(self, client):
        """POST /daily-brief with no contacts returns created:0 and empty brief."""
        # get_top_contacts_to_call returns empty list
        cur = _make_cursor(fetchone_val={"get_top_contacts_to_call": json.dumps([])})

        with patch(
            "app.routers.field_agent.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.post("/api/field-agent/daily-brief")

        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 0
        assert data["brief"] == []

    def test_daily_brief_creates_opps(self, client, monkeypatch):
        """POST /daily-brief with contacts creates opportunities for each."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        contacts = [
            {
                "contact_id": 1,
                "first_name": "Ali",
                "last_name": "Hassan",
                "phone": "+971501234567",
                "days_since_last_order": 21,
                "total_orders": 8,
                "lifetime_spend": 400,
            }
        ]

        # get_top_contacts_to_call returns our contact list
        brief_cur = _make_cursor(
            fetchone_val={"get_top_contacts_to_call": json.dumps(contacts)}
        )
        opp_cur = _make_cursor(fetchone_val={"create_opportunity": 100})

        call_count = [0]

        @contextmanager
        def _multi_cursor(commit=False):
            call_count[0] += 1
            if call_count[0] == 1:
                yield brief_cur   # get_top_contacts_to_call
            else:
                yield opp_cur     # _create_opportunity INSERT

        with patch("app.routers.field_agent.get_cursor", side_effect=_multi_cursor):
            resp = client.post("/api/field-agent/daily-brief")

        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] >= 1
        assert len(data["brief"]) >= 1


# ---------------------------------------------------------------------------
# TestGetReviews
# ---------------------------------------------------------------------------

class TestGetReviews:
    """Tests for GET /reviews."""

    def test_get_reviews_empty(self, client):
        """GET /reviews with no rows returns count:0."""
        cur = _make_cursor(
            fetchone_val={"get_recent_field_agent_reviews": json.dumps([])}
        )

        with patch(
            "app.routers.field_agent.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.get("/api/field-agent/reviews")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["reviews"] == []

    def test_get_reviews_with_data(self, client):
        """GET /reviews with data returns non-empty list."""
        review_data = [
            {
                "id": 1,
                "contact_id": 2,
                "agent_name": "Ahmed",
                "pitch_quality_score": 8.0,
                "asked_for_order": True,
                "customer_signal": "interested",
                "honest_assessment": "Good call",
                "recommended_next_action": "hand_to_automation",
                "status": "actioned",
                "created_at": None,
            }
        ]
        cur = _make_cursor(
            fetchone_val={"get_recent_field_agent_reviews": json.dumps(review_data)}
        )

        with patch(
            "app.routers.field_agent.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.get("/api/field-agent/reviews")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["reviews"][0]["agent_name"] == "Ahmed"


# ---------------------------------------------------------------------------
# TestScorecard
# ---------------------------------------------------------------------------

class TestScorecard:
    """Tests for GET /scorecard."""

    def test_scorecard_empty(self, client):
        """GET /scorecard with no data returns days and empty agents list."""
        cur = _make_cursor(
            fetchone_val={"get_field_agent_scorecard": json.dumps([])}
        )

        with patch(
            "app.routers.field_agent.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.get("/api/field-agent/scorecard")

        assert resp.status_code == 200
        data = resp.json()
        assert data["days"] == 30
        assert data["agents"] == []


# ---------------------------------------------------------------------------
# TestPendingCalls
# ---------------------------------------------------------------------------

class TestPendingCalls:
    """Tests for GET /pending-calls."""

    def test_pending_calls(self, client):
        """GET /pending-calls returns count and list of pending field sales call opps."""
        rows = [
            {
                "opportunity_id": 1,
                "priority": "hot",
                "reason": "21 days since last order",
                "suggested_message": "Hi Ali, it has been a while!",
                "created_at": None,
                "status": "pending",
                "first_name": "Ali",
                "last_name": "Hassan",
                "phone": "+971501234567",
                "email": "ali@example.com",
                "lifecycle_segment": "active",
                "total_orders": 5,
                "last_order_at": None,
                "days_since_last_order": 21,
            }
        ]
        cur = _make_cursor(rows=rows)

        with patch(
            "app.routers.field_agent.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.get("/api/field-agent/pending-calls")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["calls"][0]["first_name"] == "Ali"
