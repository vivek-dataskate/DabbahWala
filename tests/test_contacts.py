"""
Tests for app/routers/contacts.py
====================================

Covers:
  Endpoint tests (TestClient with mocked DB)
    - PATCH /api/contacts/{id}/priority — set high, set do_not_contact, invalid value → 422
    - PATCH /api/contacts/{id}/notes   — update notes

Run with:
    pytest tests/test_contacts.py -v
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


def _make_cursor(rows=None, fetchone_val=None, rowcount=1):
    c = MagicMock()
    c.fetchall.return_value = rows or []
    c.fetchone.return_value = fetchone_val
    c.rowcount = rowcount
    return c


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /api/contacts/{contact_id}/priority
# ─────────────────────────────────────────────────────────────────────────────

class TestContactPriority:
    def test_set_priority_high(self, client):
        """Setting priority_override='high' on an existing contact — 200."""
        cur = _make_cursor(rowcount=1)

        with patch(
            "app.routers.contacts.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.patch(
                "/api/contacts/1/priority",
                json={"priority_override": "high"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["contact_id"] == 1
        assert data["priority_override"] == "high"

    def test_set_priority_do_not_contact(self, client):
        """Setting priority_override='do_not_contact' — 200."""
        cur = _make_cursor(rowcount=1)

        with patch(
            "app.routers.contacts.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.patch(
                "/api/contacts/1/priority",
                json={"priority_override": "do_not_contact"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["contact_id"] == 1
        assert data["priority_override"] == "do_not_contact"

    def test_invalid_priority(self, client):
        """An unrecognised priority_override value is rejected by the router — 422."""
        # The router does its own validation check and raises HTTPException(422).
        # No DB call should be made.
        resp = client.patch(
            "/api/contacts/1/priority",
            json={"priority_override": "invalid_value"},
        )

        assert resp.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /api/contacts/{contact_id}/notes
# ─────────────────────────────────────────────────────────────────────────────

class TestContactNotes:
    def test_update_notes(self, client):
        """Updating sales_notes on an existing contact — 200 with echoed values."""
        cur = _make_cursor(rowcount=1)

        with patch(
            "app.routers.contacts.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.patch(
                "/api/contacts/1/notes",
                json={"sales_notes": "Good lead"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["contact_id"] == 1
        assert data["sales_notes"] == "Good lead"
