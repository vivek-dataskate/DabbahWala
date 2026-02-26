"""
Tests for app/routers/events.py
================================

Covers:
  Endpoint tests (TestClient with mocked DB)
    - POST /api/events/ingest — with email, with phone, orphaned contact, missing event_type

Run with:
    pytest tests/test_events.py -v
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
# POST /api/events/ingest
# ─────────────────────────────────────────────────────────────────────────────

class TestIngestEvent:
    def test_ingest_with_email(self, client):
        """Contact resolved by email, event stored — returns 200 with event_id."""
        # _resolve_email short-circuits when contact_email is provided (no DB call).
        # The write cursor returns the event_id from the ingest_event() SQL function.
        write_cur = _make_cursor(fetchone_val={"ingest_event": 42})

        with patch(
            "app.routers.events.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(write_cur),
        ):
            resp = client.post(
                "/api/events/ingest",
                json={
                    "contact_email": "alice@example.com",
                    "event_type": "page_view",
                    "metadata": {"page": "/menu"},
                },
            )

        assert resp.status_code == 200
        assert resp.json()["event_id"] == 42

    def test_ingest_with_phone(self, client):
        """Contact resolved via phone lookup, event stored — returns 200 with event_id."""
        # First get_cursor call: phone lookup (commit=False) → returns email row.
        # Second get_cursor call: write (commit=True) → returns event_id row.
        phone_cur = _make_cursor(fetchone_val={"email": "bob@example.com"})
        write_cur = _make_cursor(fetchone_val={"ingest_event": 99})

        call_count = [0]

        @contextmanager
        def _multi_cursor(commit=False):
            call_count[0] += 1
            if call_count[0] == 1:
                yield phone_cur
            else:
                yield write_cur

        with patch("app.routers.events.get_cursor", side_effect=_multi_cursor):
            resp = client.post(
                "/api/events/ingest",
                json={
                    "contact_phone": "+971501234567",
                    "event_type": "order_placed",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["event_id"] == 99

    def test_ingest_no_contact(self, client):
        """When the postgres ingest_event function raises 'Contact not found',
        the endpoint returns 404 (event cannot be stored without a contact)."""
        write_cur = _make_cursor()
        write_cur.fetchone.side_effect = Exception("Contact not found for email: ghost@example.com")

        with patch(
            "app.routers.events.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(write_cur),
        ):
            resp = client.post(
                "/api/events/ingest",
                json={
                    "contact_email": "ghost@example.com",
                    "event_type": "page_view",
                },
            )

        assert resp.status_code == 404
        assert "Contact not found" in resp.json()["detail"]

    def test_ingest_missing_event_type(self, client):
        """Omitting the required event_type field fails Pydantic validation — 422."""
        resp = client.post(
            "/api/events/ingest",
            json={"contact_email": "alice@example.com"},
        )

        assert resp.status_code == 422
