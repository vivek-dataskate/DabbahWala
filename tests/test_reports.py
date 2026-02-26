"""
Tests for app/routers/reports.py
====================================

Covers:
  Endpoint tests (TestClient with mocked DB)
    - GET  /api/reports/daily/{report_date} — existing report, missing report → 404
    - POST /api/reports/daily/{report_date} — generate report → stored proc result

Run with:
    pytest tests/test_reports.py -v
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

@contextmanager
def _cursor_ctx(cur):
    yield cur


def _make_cursor(rows=None, fetchone_val=None):
    c = MagicMock()
    c.fetchall.return_value = rows or []
    c.fetchone.return_value = fetchone_val
    return c


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/reports/daily/{report_date}
# ─────────────────────────────────────────────────────────────────────────────

class TestGetDailyReport:
    def test_get_existing_report(self, client):
        """A report row exists for the requested date — 200 with report data."""
        report_row = {"date": "2026-01-01", "orders": 5}
        cur = _make_cursor(fetchone_val=report_row)

        with patch(
            "app.routers.reports.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.get("/api/reports/daily/2026-01-01")

        assert resp.status_code == 200
        data = resp.json()
        assert data["date"] == "2026-01-01"
        assert data["orders"] == 5

    def test_get_missing_report(self, client):
        """No report exists for the requested date — 404."""
        cur = _make_cursor(fetchone_val=None)

        with patch(
            "app.routers.reports.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.get("/api/reports/daily/2026-01-01")

        assert resp.status_code == 404
        assert "detail" in resp.json()


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/reports/daily/{report_date}
# ─────────────────────────────────────────────────────────────────────────────

class TestGenerateDailyReport:
    def test_generate_report(self, client):
        """generate_daily_report stored proc runs and returns its JSON result — 200."""
        proc_result = {"status": "ok"}
        # The router does: cur.fetchone()["generate_daily_report"]
        cur = _make_cursor(fetchone_val={"generate_daily_report": proc_result})

        with patch(
            "app.routers.reports.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.post("/api/reports/daily/2026-01-01")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
