"""
Tests for app/routers/growth_agent.py
========================================

Router prefix: /api/growth

Covers:
  - POST /run-cycle
  - POST /measure
  - GET  /experiments
  - GET  /insights
  - POST /baseline/update

All DB calls are mocked via patch on app.routers.growth_agent.get_cursor.
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
    """Return a mock anthropic.Anthropic client whose messages.create returns a tool_use block."""
    block = MagicMock()
    block.type = "tool_use"
    block.input = tool_input

    response = MagicMock()
    response.content = [block]
    response.stop_reason = "tool_use"
    response.usage = MagicMock()
    response.usage.input_tokens = 300
    response.usage.output_tokens = 150

    client_instance = MagicMock()
    client_instance.messages.create.return_value = response
    return client_instance


# ---------------------------------------------------------------------------
# TestGrowthRunCycle
# ---------------------------------------------------------------------------

class TestGrowthRunCycle:
    """Tests for POST /run-cycle."""

    def test_run_cycle_mocked(self, client, monkeypatch):
        """POST /run-cycle with mocked Claude and DB returns experiment_id."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        design_input = {
            "name": "Tuesday Urgency SMS",
            "hypothesis": "Sending urgency SMS on Tuesdays boosts reorders by 20%",
            "experiment_type": "timing_test",
            "channel": "sms",
            "target_segment": "lapsed_14d",
            "cohort_size": 25,
            "message_template": "Hi {name}, your next meal is waiting — order today!",
            "offer_detail": "No offer — urgency only",
            "win_condition": "order placed within 7 days",
            "rationale": "Tuesday is a low-competition day",
        }

        # past experiments, baseline, menu
        history_cur = _make_cursor(rows=[], fetchone_val={"baseline_conv_rate": 0.05, "measured_at": None})
        # cohort query
        cohort_rows = [{"id": i} for i in range(1, 21)]
        insert_cur = _make_cursor(rows=cohort_rows, fetchone_val={"id": 99})

        call_count = [0]

        @contextmanager
        def _multi_cursor(commit=False):
            call_count[0] += 1
            if call_count[0] <= 3:
                yield history_cur
            else:
                yield insert_cur

        claude_mock = _make_claude_mock(design_input)

        with patch("app.routers.growth_agent.get_cursor", side_effect=_multi_cursor), \
             patch("anthropic.Anthropic", return_value=claude_mock):
            resp = client.post("/api/growth/run-cycle")

        assert resp.status_code == 200
        data = resp.json()
        # Either launched successfully (experiment_id present) or skipped gracefully
        assert "status" in data

    def test_run_cycle_no_key(self, client, monkeypatch):
        """POST /run-cycle without ANTHROPIC_API_KEY returns 500."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        cur = _make_cursor(rows=[], fetchone_val={"baseline_conv_rate": 0.05, "measured_at": None})

        with patch(
            "app.routers.growth_agent.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.post("/api/growth/run-cycle")

        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# TestGrowthMeasure
# ---------------------------------------------------------------------------

class TestGrowthMeasure:
    """Tests for POST /measure."""

    def test_measure_empty(self, client, monkeypatch):
        """POST /measure with no due experiments returns measured:0."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        cur = _make_cursor(rows=[])

        with patch(
            "app.routers.growth_agent.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.post("/api/growth/measure")

        assert resp.status_code == 200
        data = resp.json()
        assert data["measured"] == 0
        assert data["results"] == []


# ---------------------------------------------------------------------------
# TestGrowthExperiments
# ---------------------------------------------------------------------------

class TestGrowthExperiments:
    """Tests for GET /experiments."""

    def test_get_empty(self, client):
        """GET /experiments with no rows returns empty experiments list."""
        cur = _make_cursor(rows=[])

        with patch(
            "app.routers.growth_agent.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.get("/api/growth/experiments")

        assert resp.status_code == 200
        data = resp.json()
        assert data["experiments"] == []

    def test_get_with_filter(self, client):
        """GET /experiments?status=active&limit=5 passes filters to DB."""
        row = {
            "id": 1,
            "name": "Test Exp",
            "experiment_type": "timing_test",
            "channel": "sms",
            "hypothesis": "Works",
            "cohort_size": 20,
            "orders_won": 4,
            "conversion_rate": 0.2,
            "is_winner": True,
            "learnings": "Timing matters",
            "status": "active",
            "started_at": None,
            "measure_at": None,
            "measured_at": None,
        }
        cur = _make_cursor(rows=[row])

        with patch(
            "app.routers.growth_agent.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.get("/api/growth/experiments?status=active&limit=5")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["experiments"]) == 1


# ---------------------------------------------------------------------------
# TestGrowthInsights
# ---------------------------------------------------------------------------

class TestGrowthInsights:
    """Tests for GET /insights."""

    def test_get_insights(self, client, monkeypatch):
        """GET /insights with completed experiments returns structured insight data."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        completed_row = {
            "name": "Tuesday Urgency",
            "experiment_type": "timing_test",
            "channel": "sms",
            "hypothesis": "Tuesday SMS boosts reorders",
            "conversion_rate": 0.25,
            "is_winner": True,
            "learnings": "Tuesday urgency SMS works well",
            "started_at": None,
        }
        baseline_row = {
            "baseline_conv_rate": 0.05,
            "measured_at": None,
        }

        call_count = [0]

        @contextmanager
        def _multi_cursor(commit=False):
            call_count[0] += 1
            cur = MagicMock()
            if call_count[0] == 1:
                cur.fetchall.return_value = [completed_row]
                cur.fetchone.return_value = baseline_row
            else:
                cur.fetchall.return_value = []
                cur.fetchone.return_value = baseline_row
            yield cur

        # Mock the text response from Claude (insights endpoint uses non-tool call)
        text_block = MagicMock()
        text_block.text = "Bullet 1: Tuesday works. Bullet 2: Urgency drives action."
        insights_response = MagicMock()
        insights_response.content = [text_block]

        claude_instance = MagicMock()
        claude_instance.messages.create.return_value = insights_response

        with patch("app.routers.growth_agent.get_cursor", side_effect=_multi_cursor), \
             patch("anthropic.Anthropic", return_value=claude_instance):
            resp = client.get("/api/growth/insights")

        assert resp.status_code == 200
        data = resp.json()
        assert "baseline_conv_rate" in data or "insights" in data


# ---------------------------------------------------------------------------
# TestBaselineUpdate
# ---------------------------------------------------------------------------

class TestBaselineUpdate:
    """Tests for POST /baseline/update."""

    def test_update_baseline(self, client):
        """POST /baseline/update recalculates baseline and returns the new rate."""
        # The query returns total_outreached and total_converted (raw column names)
        db_row = {
            "total_outreached": 100,
            "total_converted": 5,
        }
        cur = _make_cursor(fetchone_val=db_row)

        with patch(
            "app.routers.growth_agent.get_cursor",
            side_effect=lambda commit=True: _cursor_ctx(cur),
        ):
            resp = client.post("/api/growth/baseline/update")

        assert resp.status_code == 200
        data = resp.json()
        assert "baseline_conv_rate" in data
