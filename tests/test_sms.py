"""
Tests for app/routers/sms.py
==============================

Covers:
  Endpoint tests (TestClient with mocked DB)
    - POST /api/telnyx/message            — inbound message, outbound, missing body → 422
    - POST /api/telnyx/call               — basic call, with transcript
    - POST /api/telnyx/field-agent-message — field agent SMS

Note: sms.py resolves contact email from phone via `_resolve_email()`.
      The router uses get_cursor for the phone lookup AND for the stored proc call.
      We mock _resolve_email directly so tests focus on the endpoint behaviour, not
      the phone-to-email plumbing.

Run with:
    pytest tests/test_sms.py -v
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
# POST /api/telnyx/message
# ─────────────────────────────────────────────────────────────────────────────

class TestRecordMessage:
    def test_inbound_message(self, client):
        """POST /api/telnyx/message — inbound SMS stored, returns {id:1}."""
        # Mock _resolve_email so we don't need a phone→email DB lookup
        # Mock get_cursor for the store_telnyx_message stored proc
        cur = _make_cursor(fetchone_val={"store_telnyx_message": 1})

        with patch("app.routers.sms._resolve_email", return_value="customer@example.com"), \
             patch("app.routers.sms.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/telnyx/message",
                json={
                    "contact_phone": "+11234567890",
                    "direction": "inbound",
                    "from_number": "+11234567890",
                    "to_number": "+18444322224",
                    "body": "Hi, is my order coming?",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 1

    def test_outbound_message(self, client):
        """POST /api/telnyx/message outbound — stored, returns id."""
        cur = _make_cursor(fetchone_val={"store_telnyx_message": 2})

        with patch("app.routers.sms._resolve_email", return_value="customer@example.com"), \
             patch("app.routers.sms.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/telnyx/message",
                json={
                    "contact_email": "customer@example.com",
                    "direction": "outbound",
                    "from_number": "+18444322224",
                    "to_number": "+11234567890",
                    "body": "Your order is on the way!",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 2

    def test_message_missing_body(self, client):
        """POST /api/telnyx/message without required fields — 422."""
        resp = client.post(
            "/api/telnyx/message",
            json={
                "direction": "inbound",
                # missing from_number and to_number
            },
        )
        assert resp.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/telnyx/call
# ─────────────────────────────────────────────────────────────────────────────

class TestRecordCall:
    def test_record_call(self, client):
        """POST /api/telnyx/call — stored, returns {id:2}."""
        cur = _make_cursor(fetchone_val={"store_telnyx_call": 2})

        with patch("app.routers.sms._resolve_email", return_value="customer@example.com"), \
             patch("app.routers.sms.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/telnyx/call",
                json={
                    "contact_phone": "+11234567890",
                    "direction": "inbound",
                    "from_number": "+11234567890",
                    "to_number": "+18444322224",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 2

    def test_call_with_transcript(self, client):
        """POST /api/telnyx/call with transcript and summary — 200."""
        cur = _make_cursor(fetchone_val={"store_telnyx_call": 3})

        with patch("app.routers.sms._resolve_email", return_value="customer@example.com"), \
             patch("app.routers.sms.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/telnyx/call",
                json={
                    "contact_email": "customer@example.com",
                    "direction": "inbound",
                    "from_number": "+11234567890",
                    "to_number": "+18444322224",
                    "duration_sec": 120,
                    "transcript": "Customer asked about delivery time.",
                    "summary": "Customer inquiry about ETA.",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 3


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/telnyx/field-agent-message
# ─────────────────────────────────────────────────────────────────────────────

class TestFieldAgentMessage:
    def test_field_agent_msg(self, client):
        """POST /api/telnyx/field-agent-message — stored, returns {id:3}."""
        cur = _make_cursor(fetchone_val={"store_telnyx_message": 3})

        with patch("app.routers.sms._resolve_email", return_value="customer@example.com"), \
             patch("app.routers.sms.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/telnyx/field-agent-message",
                json={
                    "contact_phone": "+11234567890",
                    "agent_name": "John Field",
                    "body": "Called customer, they're interested in ordering again.",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 3
