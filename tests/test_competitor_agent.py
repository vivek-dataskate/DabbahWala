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


# ---------------------------------------------------------------------------
# _tool_call exception handler (lines 104-107)
# ---------------------------------------------------------------------------

class TestToolCallException:
    def test_tool_call_exception_returns_empty_dict(self, monkeypatch):
        """_tool_call returns {} when Claude raises (lines 104-107)."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        from app.routers.competitor_agent import _tool_call

        client_instance = MagicMock()
        client_instance.messages.create.side_effect = Exception("API error")

        tool = {"name": "test_tool", "input_schema": {"type": "object", "properties": {}}}
        result = _tool_call(client_instance, "system", "user", tool)
        assert result == {}


# ---------------------------------------------------------------------------
# _parse_email_samples edge cases (lines 133-137, 160-166, 177-178)
# ---------------------------------------------------------------------------

class TestParseEmailSamples:
    def test_sample_dir_not_found_returns_empty(self, tmp_path):
        """_parse_email_samples returns [] when sample dir doesn't exist."""
        import app.routers.competitor_agent as ca_module
        from app.routers.competitor_agent import _parse_email_samples

        original = ca_module.SAMPLE_DIR
        ca_module.SAMPLE_DIR = str(tmp_path / "nonexistent")
        try:
            result = _parse_email_samples()
        finally:
            ca_module.SAMPLE_DIR = original
        assert result == []

    def test_parse_multipart_html_fallback(self, tmp_path):
        """_parse_email_samples extracts html when no plain text part (lines 133-137)."""
        import email as _email_module
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from app.routers.competitor_agent import _parse_email_samples
        import app.routers.competitor_agent as ca_module

        # Build a multipart email with only HTML body
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Test HTML"
        msg["From"] = "test@cookunity.com"
        msg["Date"] = "Mon, 26 Feb 2026 10:00:00 +0000"
        html_part = MIMEText("<html><body><p>Hello World</p></body></html>", "html")
        msg.attach(html_part)

        eml_path = tmp_path / "test.eml"
        eml_path.write_bytes(msg.as_bytes())

        original = ca_module.SAMPLE_DIR
        ca_module.SAMPLE_DIR = str(tmp_path)
        try:
            result = _parse_email_samples()
        finally:
            ca_module.SAMPLE_DIR = original

        assert len(result) == 1
        assert result[0]["subject"] == "Test HTML"

    def test_parse_non_multipart_email(self, tmp_path):
        """_parse_email_samples parses non-multipart plain text (lines 160-166)."""
        from email.mime.text import MIMEText
        from app.routers.competitor_agent import _parse_email_samples
        import app.routers.competitor_agent as ca_module

        msg = MIMEText("Hello, subscribe now!", "plain")
        msg["Subject"] = "Plain Text"
        msg["From"] = "test@cookunity.com"
        msg["Date"] = "Mon, 26 Feb 2026 10:00:00 +0000"

        eml_path = tmp_path / "plain.eml"
        eml_path.write_bytes(msg.as_bytes())

        original = ca_module.SAMPLE_DIR
        ca_module.SAMPLE_DIR = str(tmp_path)
        try:
            result = _parse_email_samples()
        finally:
            ca_module.SAMPLE_DIR = original

        assert len(result) == 1
        assert "Plain Text" in result[0]["subject"]

    def test_parse_email_exception_skipped(self, tmp_path):
        """_parse_email_samples skips files that fail to parse (lines 177-178)."""
        from app.routers.competitor_agent import _parse_email_samples
        import app.routers.competitor_agent as ca_module

        # Write a corrupted .eml file
        bad_path = tmp_path / "corrupted.eml"
        bad_path.write_bytes(b"THIS IS NOT A VALID EMAIL FILE\x00\x01\xff")

        original = ca_module.SAMPLE_DIR
        ca_module.SAMPLE_DIR = str(tmp_path)
        try:
            with patch("email.message_from_bytes", side_effect=Exception("parse error")):
                result = _parse_email_samples()
        finally:
            ca_module.SAMPLE_DIR = original

        # Should return empty (file skipped)
        assert result == []


# ---------------------------------------------------------------------------
# _scrape_competitor_websites non-200 status (lines 226-227)
# ---------------------------------------------------------------------------

class TestScrapeCompetitorWebsites:
    def test_non_200_response_logged_skipped(self):
        """Non-200 HTTP status from competitor site is logged and skipped (lines 226-227)."""
        from app.routers.competitor_agent import _scrape_competitor_websites

        mock_response = MagicMock()
        mock_response.status_code = 403  # non-200
        mock_response.text = ""

        with patch("httpx.get", return_value=mock_response):
            result = _scrape_competitor_websites()

        # All sites returned 403 — nothing scraped
        assert result == []


# ---------------------------------------------------------------------------
# _inject_hypotheses exception per hypothesis (lines 473-474)
# ---------------------------------------------------------------------------

class TestInjectHypothesesException:
    def test_inject_exception_logged_skipped(self):
        """Individual hypothesis injection failure is logged and skipped (lines 473-474)."""
        from app.routers.competitor_agent import _inject_hypotheses

        hypothesis = {
            "hypothesis": "Test hypothesis",
            "experiment_type": "cohort_message",
            "cohort_description": "Test segment",
            "cohort_filter": {},
            "message_template": "Hi {first_name}!",
            "success_threshold": 0.10,
        }

        with patch("app.routers.competitor_agent.get_cursor",
                   side_effect=Exception("DB write failed")):
            count = _inject_hypotheses([hypothesis])

        assert count == 0  # nothing injected, exception swallowed


# ---------------------------------------------------------------------------
# _log_run exception handler (lines 510-511)
# ---------------------------------------------------------------------------

class TestLogRunException:
    def test_log_run_exception_silenced(self):
        """_log_run swallows DB exceptions (lines 510-511)."""
        from app.routers.competitor_agent import _log_run

        with patch("app.routers.competitor_agent.get_cursor",
                   side_effect=Exception("audit log failed")):
            # Should not raise
            _log_run(3, 2, 5, "completed", summary="ok")
