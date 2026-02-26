"""
Tests for app/routers/lifecycle.py
====================================

Covers:
  Endpoint tests (TestClient with mocked DB)
    - POST /api/lifecycle/run — returns counts, zero counts, DB error → 500

Run with:
    pytest tests/test_lifecycle.py -v
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
# POST /api/lifecycle/run
# ─────────────────────────────────────────────────────────────────────────────

class TestLifecycleRun:
    def test_run_returns_counts(self, client):
        """run_lifecycle_cycle() returns non-zero counts — 200 with correct values."""
        cur = _make_cursor(
            fetchone_val={"contacts_updated": 5, "campaigns_queued": 2}
        )

        with patch(
            "app.routers.lifecycle.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.post("/api/lifecycle/run")

        assert resp.status_code == 200
        data = resp.json()
        assert data["contacts_updated"] == 5
        assert data["campaigns_queued"] == 2

    def test_run_zero_contacts(self, client):
        """run_lifecycle_cycle() returns zeroes — 200 with zero values."""
        cur = _make_cursor(
            fetchone_val={"contacts_updated": 0, "campaigns_queued": 0}
        )

        with patch(
            "app.routers.lifecycle.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.post("/api/lifecycle/run")

        assert resp.status_code == 200
        data = resp.json()
        assert data["contacts_updated"] == 0
        assert data["campaigns_queued"] == 0

    def test_run_db_error(self, client):
        """DB exception propagates up — the TestClient (raise_server_exceptions=True)
        re-raises it rather than returning a 500 response, so we assert the exception
        is raised."""
        with pytest.raises(Exception, match="connection refused"):
            with patch(
                "app.routers.lifecycle.get_cursor",
                side_effect=Exception("connection refused"),
            ):
                client.post("/api/lifecycle/run")
