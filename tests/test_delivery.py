"""
Tests for app/routers/delivery.py
====================================

Covers:
  Endpoint tests (TestClient with mocked DB)
    - POST /api/delivery/status — with email, with phone, missing order_ref → 422, DB error → 500

Run with:
    pytest tests/test_delivery.py -v
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
# POST /api/delivery/status
# ─────────────────────────────────────────────────────────────────────────────

class TestDeliveryStatus:
    def test_record_delivery_status(self, client):
        """Delivery status recorded by email — 200 with returned id."""
        # _resolve_email short-circuits when contact_email is provided.
        # The write cursor returns the delivery id from the stored procedure.
        write_cur = _make_cursor(fetchone_val={"update_delivery_status": 1})

        with patch(
            "app.routers.delivery.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(write_cur),
        ):
            resp = client.post(
                "/api/delivery/status",
                json={
                    "contact_email": "alice@example.com",
                    "order_ref": "ORD-001",
                    "status": "delivered",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["id"] == 1

    def test_record_with_phone(self, client):
        """Delivery status recorded by phone lookup — 200 with returned id."""
        # First cursor: phone → email lookup (commit=False).
        # Second cursor: write call (commit=True) → returns delivery id.
        phone_cur = _make_cursor(fetchone_val={"email": "bob@example.com"})
        write_cur = _make_cursor(fetchone_val={"update_delivery_status": 7})

        call_count = [0]

        @contextmanager
        def _multi_cursor(commit=False):
            call_count[0] += 1
            if call_count[0] == 1:
                yield phone_cur
            else:
                yield write_cur

        with patch("app.routers.delivery.get_cursor", side_effect=_multi_cursor):
            resp = client.post(
                "/api/delivery/status",
                json={
                    "contact_phone": "+971501234567",
                    "order_ref": "ORD-007",
                    "status": "out_for_delivery",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["id"] == 7

    def test_missing_order_ref(self, client):
        """The status field is required but order_ref is optional in the model.
        The required field here is 'status' — omitting it produces 422."""
        resp = client.post(
            "/api/delivery/status",
            json={"contact_email": "alice@example.com", "order_ref": "ORD-001"},
        )

        assert resp.status_code == 422

    def test_db_error(self, client):
        """A DB exception during the write propagates up — the TestClient
        (raise_server_exceptions=True) re-raises it rather than returning a 500
        response, so we assert the exception is raised."""
        write_cur = _make_cursor()
        write_cur.fetchone.side_effect = Exception("DB write failed")

        with pytest.raises(Exception, match="DB write failed"):
            with patch(
                "app.routers.delivery.get_cursor",
                side_effect=lambda commit=False: _cursor_ctx(write_cur),
            ):
                client.post(
                    "/api/delivery/status",
                    json={
                        "contact_email": "alice@example.com",
                        "order_ref": "ORD-001",
                        "status": "delivered",
                    },
                )
