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

    def test_field_agent_msg_missing_agent_name_422(self, client):
        """POST /api/telnyx/field-agent-message without agent_name — 422."""
        resp = client.post(
            "/api/telnyx/field-agent-message",
            json={
                "contact_phone": "+11234567890",
                "body": "Some message.",
            },
        )
        assert resp.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/telnyx/pending
# ─────────────────────────────────────────────────────────────────────────────

class TestGetPendingSms:
    def test_get_pending_returns_list(self, client):
        """GET /api/telnyx/pending returns pending SMS rows."""
        pending_rows = [
            {"contact_id": 1, "email": "alice@example.com", "phone": "+11234567890", "pending_sms": 2}
        ]
        cur = _make_cursor(rows=pending_rows)

        with patch("app.routers.sms.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/telnyx/pending")

        assert resp.status_code == 200
        data = resp.json()
        assert "pending" in data
        assert data["count"] == 1
        assert data["pending"][0]["email"] == "alice@example.com"

    def test_get_pending_empty(self, client):
        """GET /api/telnyx/pending with no pending SMS — empty list."""
        cur = _make_cursor(rows=[])

        with patch("app.routers.sms.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/telnyx/pending")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["pending"] == []


# ─────────────────────────────────────────────────────────────────────────────
# Threading / agent cycle behavior
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentCycleFiring:
    def test_inbound_message_fires_agent_thread(self, client):
        """POST inbound SMS triggers a background threading.Thread for agent cycle."""
        cur = _make_cursor(fetchone_val={"store_telnyx_message": 5})

        with patch("app.routers.sms._resolve_email", return_value="customer@example.com"), \
             patch("app.routers.sms.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("app.routers.sms.threading.Thread") as mock_thread:
            mock_thread_instance = MagicMock()
            mock_thread.return_value = mock_thread_instance

            resp = client.post(
                "/api/telnyx/message",
                json={
                    "contact_email": "customer@example.com",
                    "direction": "inbound",
                    "from_number": "+11234567890",
                    "to_number": "+18444322224",
                    "body": "Hello?",
                },
            )

        assert resp.status_code == 200
        # Thread should be created and started for inbound messages
        mock_thread.assert_called_once()
        mock_thread_instance.start.assert_called_once()

    def test_outbound_message_does_not_fire_agent_thread(self, client):
        """POST outbound SMS does NOT fire an agent cycle thread."""
        cur = _make_cursor(fetchone_val={"store_telnyx_message": 6})

        with patch("app.routers.sms._resolve_email", return_value="customer@example.com"), \
             patch("app.routers.sms.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("app.routers.sms.threading.Thread") as mock_thread:
            resp = client.post(
                "/api/telnyx/message",
                json={
                    "contact_email": "customer@example.com",
                    "direction": "outbound",
                    "from_number": "+18444322224",
                    "to_number": "+11234567890",
                    "body": "Your lunch is ready!",
                },
            )

        assert resp.status_code == 200
        # No thread should be started for outbound messages
        mock_thread.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Auto-create contact for unknown inbound number
# ─────────────────────────────────────────────────────────────────────────────

class TestAutoCreateContact:
    def test_inbound_from_unknown_number_creates_contact(self, client):
        """Inbound SMS from unknown phone auto-creates a contact and stores the message."""
        from fastapi import HTTPException

        # _resolve_email raises 404 (contact not found) → triggers auto-create path
        # Then get_cursor is called for INSERT contact, INSERT message, INSERT event
        # fetchone chain: INSERT contact RETURNING id, INSERT message RETURNING id
        call_count = [0]
        cur = MagicMock()

        def _fetchone():
            call_count[0] += 1
            if call_count[0] == 1:
                return {"id": 99}   # contact INSERT RETURNING id
            return {"id": 101}      # message INSERT RETURNING id

        cur.fetchone.side_effect = _fetchone

        @contextmanager
        def _multi_cur(commit=False):
            yield cur

        # _resolve_email raises 404, triggering the auto-create branch
        with patch(
            "app.routers.sms._resolve_email",
            side_effect=HTTPException(status_code=404, detail="Contact not found"),
        ), patch("app.routers.sms.get_cursor", side_effect=_multi_cur), \
           patch("app.routers.sms.threading.Thread") as mock_thread:
            mock_thread_instance = MagicMock()
            mock_thread.return_value = mock_thread_instance

            resp = client.post(
                "/api/telnyx/message",
                json={
                    "direction": "inbound",
                    "from_number": "+19995550123",
                    "to_number": "+18444322224",
                    "body": "Is this the food place?",
                },
            )

        assert resp.status_code == 200
        # Agent cycle is fired even for auto-created contacts
        mock_thread.assert_called_once()
        mock_thread_instance.start.assert_called_once()


# ---------------------------------------------------------------------------
# _resolve_email helper — direct tests
# ---------------------------------------------------------------------------

class TestResolveEmail:
    """Test _resolve_email helper directly."""

    def test_returns_email_directly_when_provided(self):
        """When email is given, returns it lowercase stripped without DB lookup."""
        from app.routers.sms import _resolve_email
        result = _resolve_email(None, "  Alice@Example.com  ")
        assert result == "alice@example.com"

    def test_raises_400_when_no_phone_or_email(self):
        """Raises HTTPException 400 when both phone and email are None/empty."""
        from fastapi import HTTPException
        from app.routers.sms import _resolve_email
        import pytest

        with pytest.raises(HTTPException) as exc_info:
            _resolve_email(None, None)
        assert exc_info.value.status_code == 400

    def test_resolves_email_from_phone(self):
        """When only phone given, looks up email in DB."""
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch
        from app.routers.sms import _resolve_email

        cur = MagicMock()
        cur.fetchone.return_value = {"email": "alice@example.com"}

        @contextmanager
        def _cursor_ctx(commit=False):
            yield cur

        with patch("app.routers.sms.get_cursor", side_effect=_cursor_ctx):
            result = _resolve_email("+14041111111", None)

        assert result == "alice@example.com"

    def test_raises_404_when_phone_not_found(self):
        """Raises HTTPException 404 when phone not in DB."""
        from contextlib import contextmanager
        from fastapi import HTTPException
        from unittest.mock import MagicMock, patch
        from app.routers.sms import _resolve_email
        import pytest

        cur = MagicMock()
        cur.fetchone.return_value = None

        @contextmanager
        def _cursor_ctx(commit=False):
            yield cur

        with patch("app.routers.sms.get_cursor", side_effect=_cursor_ctx):
            with pytest.raises(HTTPException) as exc_info:
                _resolve_email("+14040000000", None)

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Additional store_message error paths
# ---------------------------------------------------------------------------

class TestStoreMessageErrors:
    def test_store_message_contact_not_found_outbound(self, client):
        """Outbound store_message raises 404 when contact not found in stored proc."""
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch

        cur = MagicMock()
        # resolve_email succeeds (email given directly)
        # but stored proc raises "Contact not found"
        cur.fetchone.side_effect = Exception("Contact not found: alice@unknown.com")

        @contextmanager
        def _cursor_ctx(commit=True):
            yield cur

        with patch("app.routers.sms.get_cursor", side_effect=_cursor_ctx):
            resp = client.post("/api/telnyx/message", json={
                "contact_email": "alice@unknown.com",
                "direction": "outbound",
                "from_number": "+18444322224",
                "to_number": "+14041111111",
                "body": "Your order is ready!",
            })

        assert resp.status_code == 404

    def test_store_call_contact_not_found(self, client):
        """store_call raises 404 when contact not found in DB."""
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch

        cur = MagicMock()
        cur.fetchone.side_effect = Exception("Contact not found: nobody@x.com")

        @contextmanager
        def _cursor_ctx(commit=True):
            yield cur

        with patch("app.routers.sms.get_cursor", side_effect=_cursor_ctx):
            resp = client.post("/api/telnyx/call", json={
                "contact_email": "nobody@x.com",
                "direction": "outbound",
                "from_number": "+18444322224",
                "to_number": "+14041111111",
                "duration_sec": 120,
            })

        assert resp.status_code == 404

    def test_field_agent_message_contact_not_found(self, client):
        """POST /api/telnyx/field-agent-message 404 when contact not found."""
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch

        cur = MagicMock()
        # First call is get_cursor for _resolve_email (returns email from phone)
        cur.fetchone.return_value = {"email": "agent@x.com"}

        # Second cursor call (store_telnyx_message) raises "Contact not found"
        call_count = [0]

        @contextmanager
        def _cursor_ctx(commit=False):
            call_count[0] += 1
            if call_count[0] == 1:
                cur2 = MagicMock()
                cur2.fetchone.return_value = {"email": "agent@x.com"}
                yield cur2
            else:
                cur3 = MagicMock()
                cur3.fetchone.side_effect = Exception("Contact not found: agent@x.com")
                yield cur3

        with patch("app.routers.sms.get_cursor", side_effect=_cursor_ctx):
            resp = client.post("/api/telnyx/field-agent-message", json={
                "contact_phone": "+14041111111",
                "agent_name": "Driver Bob",
                "body": "Dropping off your order now!",
                "sent_at": "2026-01-15T12:00:00Z",
            })

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# _fire_agent_cycle and _fire_agent_cycle_by_id direct tests (lines 16-21, 36-40)
# ---------------------------------------------------------------------------

class TestFireAgentCycleDirect:
    def test_fire_agent_cycle_by_id_exception_silenced(self):
        """_fire_agent_cycle_by_id swallows exception from _run_full_cycle (lines 16-21)."""
        from app.routers.sms import _fire_agent_cycle_by_id
        with patch("app.routers.agents._run_full_cycle", side_effect=RuntimeError("crash")):
            # Should not raise
            _fire_agent_cycle_by_id(1, "test")

    def test_fire_agent_cycle_no_contact_returns_early(self):
        """_fire_agent_cycle with unknown email → no contact → returns early (lines 36-37)."""
        from app.routers.sms import _fire_agent_cycle
        cur = MagicMock()
        cur.fetchone.return_value = None  # no contact

        @contextmanager
        def _ctx(commit=False):
            yield cur

        with patch("app.routers.sms.get_cursor", side_effect=_ctx):
            _fire_agent_cycle("unknown@x.com", "test")
        # No exception and no _run_full_cycle call since contact not found

    def test_fire_agent_cycle_with_contact(self):
        """_fire_agent_cycle with known email → calls _run_full_cycle (lines 39-40)."""
        from app.routers.sms import _fire_agent_cycle
        cur = MagicMock()
        cur.fetchone.return_value = {"id": 42}

        @contextmanager
        def _ctx(commit=False):
            yield cur

        with patch("app.routers.sms.get_cursor", side_effect=_ctx), \
             patch("app.routers.agents._run_full_cycle") as mock_run:
            _fire_agent_cycle("alice@example.com", "sms_inbound")
        mock_run.assert_called_once_with(42)

    def test_fire_agent_cycle_exception_silenced(self):
        """_fire_agent_cycle swallows _run_full_cycle exceptions."""
        from app.routers.sms import _fire_agent_cycle
        cur = MagicMock()
        cur.fetchone.return_value = {"id": 5}

        @contextmanager
        def _ctx(commit=False):
            yield cur

        with patch("app.routers.sms.get_cursor", side_effect=_ctx), \
             patch("app.routers.agents._run_full_cycle", side_effect=RuntimeError("boom")):
            # Should not raise
            _fire_agent_cycle("x@example.com", "trigger")


# ---------------------------------------------------------------------------
# store_message: resolve_email raises 404 for non-inbound → re-raise (line 136)
# ---------------------------------------------------------------------------

class TestStoreMessageReraise:
    def test_outbound_resolve_email_404_reraises(self, client):
        """For outbound direction, _resolve_email raising 404 propagates (line 136)."""
        from fastapi import HTTPException

        with patch(
            "app.routers.sms._resolve_email",
            side_effect=HTTPException(status_code=404, detail="Contact not found"),
        ):
            resp = client.post("/api/telnyx/message", json={
                "direction": "outbound",
                "from_number": "+18444322224",
                "to_number": "+14041111111",
                "body": "Hello!",
            })

        assert resp.status_code == 404

    def test_store_message_unexpected_error_reraises(self):
        """Unexpected exception in store_telnyx_message logs and reraises (lines 165-166)."""
        import pytest
        from app.models import TelnyxMessageIn
        from app.routers.sms import store_message

        payload = TelnyxMessageIn(
            direction="outbound",
            from_number="+18444322224",
            to_number="+14041111111",
            body="Hello!",
            contact_email="test@example.com",
        )

        cur = MagicMock()
        cur.fetchone.side_effect = Exception("unexpected db failure xyz")

        @contextmanager
        def _ctx(commit=False):
            yield cur

        with patch("app.routers.sms._resolve_email", return_value="test@example.com"), \
             patch("app.routers.sms.get_cursor", side_effect=_ctx):
            with pytest.raises(Exception, match="unexpected db failure xyz"):
                store_message(payload)

    def test_auto_create_contact_insert_fails_reraises(self):
        """Auto-create path: contact INSERT followed by SELECT returning None → reraises (lines 104-105)."""
        import pytest
        from fastapi import HTTPException
        from app.models import TelnyxMessageIn
        from app.routers.sms import store_message

        payload = TelnyxMessageIn(
            direction="inbound",
            from_number="+19995550123",
            to_number="+18444322224",
            body="Who dis?",
        )

        cur = MagicMock()
        cur.fetchone.return_value = None  # SELECT after INSERT returns None → re-raise

        @contextmanager
        def _ctx(commit=False):
            yield cur

        with patch(
            "app.routers.sms._resolve_email",
            side_effect=HTTPException(status_code=404, detail="Contact not found"),
        ), patch("app.routers.sms.get_cursor", side_effect=_ctx):
            with pytest.raises(HTTPException) as exc_info:
                store_message(payload)
        assert exc_info.value.status_code == 404

    def test_store_call_unexpected_error_reraises(self):
        """Unexpected exception in store_telnyx_call reraises (lines ~216-217)."""
        import pytest
        from app.models import TelnyxCallIn
        from app.routers.sms import store_call

        payload = TelnyxCallIn(
            direction="outbound",
            from_number="+18444322224",
            to_number="+14041111111",
            duration_sec=60,
            contact_email="a@b.com",
        )

        cur = MagicMock()
        cur.fetchone.side_effect = Exception("unexpected db failure xyz")

        @contextmanager
        def _ctx(commit=False):
            yield cur

        with patch("app.routers.sms._resolve_email", return_value="a@b.com"), \
             patch("app.routers.sms.get_cursor", side_effect=_ctx):
            with pytest.raises(Exception, match="unexpected db failure xyz"):
                store_call(payload)

    def test_field_agent_message_unexpected_error_reraises(self):
        """Unexpected exception in field-agent-message reraises (line 254)."""
        import pytest
        from app.models import FieldAgentSmsIn
        from app.routers.sms import log_field_agent_sms

        payload = FieldAgentSmsIn(
            contact_phone="+14041111111",
            agent_name="Driver Sam",
            body="On my way!",
        )

        cur = MagicMock()
        cur.fetchone.side_effect = Exception("unexpected failure xyz")

        @contextmanager
        def _ctx(commit=False):
            yield cur

        with patch("app.routers.sms._resolve_email", return_value="b@b.com"), \
             patch("app.routers.sms.get_cursor", side_effect=_ctx):
            with pytest.raises(Exception, match="unexpected failure xyz"):
                log_field_agent_sms(payload)
