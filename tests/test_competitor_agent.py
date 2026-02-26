"""
Tests for app/routers/competitor_agent.py
==========================================

Router prefix: /api/competitor-agent

Covers:
  - POST /run
  - GET  /runs
  - GET  /experiments

All DB calls are mocked via patch on app.routers.competitor_agent.get_cursor.
All Claude calls are mocked via patch on anthropic.Anthropic.
HTTP scraping is mocked via patch on httpx.
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
    response.usage.input_tokens = 500
    response.usage.output_tokens = 250

    client_instance = MagicMock()
    client_instance.messages.create.return_value = response
    return client_instance


def _make_httpx_mock(text: str = "<html><body>competitor content</body></html>"):
    """Return a mock httpx client that returns static HTML."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = text

    mock_client = MagicMock()
    mock_client.get.return_value = mock_response
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    return mock_client


# ---------------------------------------------------------------------------
# TestCompetitorRun
# ---------------------------------------------------------------------------

class TestCompetitorRun:
    """Tests for POST /run."""

    def test_run_no_key(self, client, monkeypatch):
        """POST /run without ANTHROPIC_API_KEY returns 500."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        cur = _make_cursor(rows=[])

        with patch(
            "app.routers.competitor_agent.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.post("/api/competitor-agent/run")

        assert resp.status_code == 500

    def test_run_with_mocks(self, client, monkeypatch):
        """POST /run with mocked Claude and httpx returns 200 with expected keys."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        hypotheses_input = {
            "hypotheses": [
                {
                    "hypothesis": "Offer a loyalty tier badge after 5 orders",
                    "experiment_type": "loyalty_program",
                    "target_segment": "regulars",
                    "channel": "sms",
                    "message_template": "You've unlocked Gold status!",
                    "rationale": "CookUnity uses loyalty tiers effectively",
                    "source": "competitor_analysis",
                }
            ]
        }

        db_cur = _make_cursor(rows=[], fetchone_val={"id": 1})

        call_count = [0]

        @contextmanager
        def _multi_cursor(commit=False):
            call_count[0] += 1
            yield db_cur

        claude_mock = _make_claude_mock(hypotheses_input)
        httpx_mock = _make_httpx_mock()

        with patch("app.routers.competitor_agent.get_cursor", side_effect=_multi_cursor), \
             patch("anthropic.Anthropic", return_value=claude_mock), \
             patch("httpx.Client", return_value=httpx_mock):
            resp = client.post("/api/competitor-agent/run")

        assert resp.status_code == 200
        data = resp.json()
        # Should contain the standard result fields
        assert "timestamp" in data
        assert "hypotheses_queued" in data
        assert "sources_processed" in data


# ---------------------------------------------------------------------------
# TestListRuns
# ---------------------------------------------------------------------------

class TestListRuns:
    """Tests for GET /runs."""

    def test_list_runs_empty(self, client):
        """GET /runs with no rows returns count:0 and empty list."""
        cur = _make_cursor(rows=[])

        with patch(
            "app.routers.competitor_agent.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.get("/api/competitor-agent/runs")

        assert resp.status_code == 200
        data = resp.json()
        assert data["runs"] == []
        assert data["count"] == 0

    def test_list_runs_with_data(self, client):
        """GET /runs with one row returns count:1."""
        row = {
            "id": 1,
            "sources_processed": 5,
            "email_samples_parsed": 3,
            "websites_scraped": 2,
            "hypotheses_queued": 8,
            "status": "success",
            "summary": "Found 8 new hypotheses",
            "created_at": None,
        }
        cur = _make_cursor(rows=[row])

        with patch(
            "app.routers.competitor_agent.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.get("/api/competitor-agent/runs")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["runs"][0]["id"] == 1


# ---------------------------------------------------------------------------
# TestListExperiments
# ---------------------------------------------------------------------------

class TestListExperiments:
    """Tests for GET /experiments."""

    def test_list_experiments_empty(self, client):
        """GET /experiments with no rows returns count:0 and empty list."""
        cur = _make_cursor(rows=[])

        with patch(
            "app.routers.competitor_agent.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.get("/api/competitor-agent/experiments")

        assert resp.status_code == 200
        data = resp.json()
        assert data["experiments"] == []
        assert data["count"] == 0
