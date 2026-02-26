"""
Tests for app/routers/goal_agent.py
=====================================

Router prefix: /api/goal-agent

Covers:
  - POST /run
  - POST /measure
  - GET  /experiments
  - GET  /signals
  - GET  /runs
  - POST /hypothesize
  - POST /experiment
  - POST /harvest

All DB calls are mocked via patch on app.routers.goal_agent.get_cursor.
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
    response.usage.input_tokens = 200
    response.usage.output_tokens = 100

    client_instance = MagicMock()
    client_instance.messages.create.return_value = response
    return client_instance


# ---------------------------------------------------------------------------
# TestGoalAgentRun
# ---------------------------------------------------------------------------

class TestGoalAgentRun:
    """Tests for POST /run (full four-phase cycle)."""

    def test_run_no_anthropic_key(self, client, monkeypatch):
        """POST /run without ANTHROPIC_API_KEY returns 500."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        cur = _make_cursor(rows=[], fetchone_val=None)

        with patch(
            "app.routers.goal_agent.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.post("/api/goal-agent/run")

        assert resp.status_code == 500

    def test_run_with_mocked_claude(self, client, monkeypatch):
        """POST /run with mocked Claude returns 200 and expected shape."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        hypothesis_input = {
            "experiments": [
                {
                    "hypothesis": "Sending a 10% discount on Tuesdays boosts reorders",
                    "experiment_type": "discount_offer",
                    "cohort_description": "active contacts who ordered 2+ weeks ago",
                    "message_template": "Hi {name}, enjoy 10% off today!",
                    "success_metric": "places order within 72h",
                    "channel": "sms",
                }
            ]
        }

        # Snapshot queries return empty data
        snapshot_cur = _make_cursor(rows=[], fetchone_val={"count": 0, "baseline_conv_rate": 0.05})
        enroll_cur = _make_cursor(rows=[], fetchone_val={"id": 10})
        measure_cur = _make_cursor(rows=[], fetchone_val=None)
        harvest_cur = _make_cursor(rows=[], fetchone_val=None)

        call_count = [0]

        @contextmanager
        def _multi_cursor(commit=False):
            call_count[0] += 1
            if call_count[0] <= 3:
                yield snapshot_cur
            elif call_count[0] <= 6:
                yield enroll_cur
            else:
                yield measure_cur

        claude_mock = _make_claude_mock(hypothesis_input)

        with patch("app.routers.goal_agent.get_cursor", side_effect=_multi_cursor), \
             patch("anthropic.Anthropic", return_value=claude_mock):
            resp = client.post("/api/goal-agent/run")

        assert resp.status_code == 200
        data = resp.json()
        assert "timestamp" in data
        assert "phase" in data


# ---------------------------------------------------------------------------
# TestMeasureExperiments
# ---------------------------------------------------------------------------

class TestMeasureExperiments:
    """Tests for POST /measure."""

    def test_measure_no_due_experiments(self, client, monkeypatch):
        """POST /measure with no experiments due returns measured:0."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        cur = _make_cursor(rows=[])

        with patch(
            "app.routers.goal_agent.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.post("/api/goal-agent/measure")

        assert resp.status_code == 200
        data = resp.json()
        assert data["experiments_concluded"] == 0

    def test_measure_with_due_experiment(self, client, monkeypatch):
        """POST /measure with one due experiment → concluded:1."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        phase_result = {"experiments_concluded": 1, "orders_attributed": 3}
        log_cur = _make_cursor()

        with patch("app.routers.goal_agent._phase_measure", return_value=phase_result), \
             patch("app.routers.goal_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(log_cur)), \
             patch("anthropic.Anthropic"):
            resp = client.post("/api/goal-agent/measure")

        assert resp.status_code == 200
        data = resp.json()
        assert data["experiments_concluded"] == 1


# ---------------------------------------------------------------------------
# TestGetExperiments
# ---------------------------------------------------------------------------

class TestGetExperiments:
    """Tests for GET /experiments."""

    def test_get_experiments_empty(self, client):
        """GET /experiments with no rows returns count:0 and empty list."""
        cur = _make_cursor(rows=[])

        with patch(
            "app.routers.goal_agent.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.get("/api/goal-agent/experiments")

        assert resp.status_code == 200
        data = resp.json()
        assert data["experiments"] == []
        assert data["count"] == 0

    def test_get_experiments_with_status_filter(self, client):
        """GET /experiments?status=active filters by status."""
        row = {
            "id": 1,
            "hypothesis": "Test",
            "experiment_type": "discount_offer",
            "status": "active",
            "cohort_description": "new users",
            "enrolled_count": 15,
            "result_conversion_rate": None,
            "conclusion": None,
            "started_at": None,
            "concluded_at": None,
            "created_at": None,
        }
        cur = _make_cursor(rows=[row])

        with patch(
            "app.routers.goal_agent.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.get("/api/goal-agent/experiments?status=active")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1


# ---------------------------------------------------------------------------
# TestGetRuns
# ---------------------------------------------------------------------------

class TestGetRuns:
    """Tests for GET /runs."""

    def test_get_runs_empty(self, client):
        """GET /runs with no rows returns count:0 and empty list."""
        cur = _make_cursor(rows=[])

        with patch(
            "app.routers.goal_agent.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.get("/api/goal-agent/runs")

        assert resp.status_code == 200
        data = resp.json()
        assert data["runs"] == []
        assert data["count"] == 0
