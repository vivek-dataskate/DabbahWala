"""
Tests for app/routers/webhooks.py
====================================

Covers:
  - GET  /api/webhooks/campaigns
  - POST /api/webhooks/campaign-stats
  - POST /api/webhooks/instantly
  - POST /api/webhooks/telnyx
  - GET  /api/webhooks/shipday
  - POST /api/webhooks/shipday

Run with:
    pytest tests/test_webhooks.py -v
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


# ---------------------------------------------------------------------------
# 1. GET /api/webhooks/campaigns
# ---------------------------------------------------------------------------

class TestCampaignsWebhook:
    def test_list_campaigns(self, client):
        """fetchall returns one campaign row → 200 with campaigns list."""
        row = {
            "campaign_name": "DW-PromoStandard-ActiveEngaged",
            "campaign_id": "id1",
            "label": "DW Promo Standard",
            "leads_count": 100,
            "emails_sent": 80,
            "unique_opens": 20,
            "opens": 25,
            "replies": 5,
            "clicks": 3,
            "bounces": 2,
            "open_rate": 0.25,
            "reply_rate": 0.06,
            "stats_synced_at": None,
        }
        cur = _make_cursor(rows=[row])

        with patch("app.routers.webhooks.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/webhooks/campaigns")

        assert resp.status_code == 200
        data = resp.json()
        assert "campaigns" in data
        assert data["total"] == 1
        assert data["campaigns"][0]["campaign_name"] == "DW-PromoStandard-ActiveEngaged"

    def test_list_campaigns_empty(self, client):
        """No campaigns in DB → 200 with empty list and total 0."""
        cur = _make_cursor(rows=[])

        with patch("app.routers.webhooks.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/webhooks/campaigns")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["campaigns"] == []


# ---------------------------------------------------------------------------
# 2. POST /api/webhooks/campaign-stats
# ---------------------------------------------------------------------------

class TestCampaignStats:
    def test_update_stats(self, client):
        """Valid campaign stats body → 200 {status:'ok', campaign_id, updated}."""
        cur = MagicMock()
        cur.rowcount = 1

        with patch("app.routers.webhooks.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/webhooks/campaign-stats",
                json={
                    "campaign_id": "c1",
                    "emails_sent": 100,
                    "unique_opens": 20,
                    "opens": 25,
                    "replies": 5,
                    "clicks": 3,
                    "bounces": 2,
                    "open_rate": 0.2,
                    "reply_rate": 0.05,
                    "leads_count": 50,
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["campaign_id"] == "c1"
        assert "updated" in data


# ---------------------------------------------------------------------------
# 3. POST /api/webhooks/instantly
# ---------------------------------------------------------------------------

class TestInstantlyWebhook:
    def test_email_open_event(self, client):
        """
        Known campaign + existing contact → 200 {status:'ok', contact_id, is_new:False}.
        """
        cur = MagicMock()
        # _dabbahwala_campaign_ids() call → fetchall returns one campaign row
        cur.fetchall.return_value = [{"instantly_campaign_id": "c1"}]
        # _upsert_contact → contact_id=1, is_new=False
        # ingest_event fetchone → ignored

        with patch("app.routers.webhooks.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("app.routers.webhooks._upsert_contact",
                   return_value=(1, False)):
            resp = client.post(
                "/api/webhooks/instantly",
                json={
                    "email": "a@b.com",
                    "event_type": "email_open",
                    "campaign_id": "c1",
                    "lead": {"email": "a@b.com", "first_name": "Alice", "last_name": "B"},
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["contact_id"] == 1
        assert data["is_new"] is False

    def test_new_contact(self, client):
        """
        Known campaign, contact not in DB yet → 200 {status:'ok', is_new:True}.
        """
        cur = MagicMock()
        cur.fetchall.return_value = [{"instantly_campaign_id": "c1"}]

        with patch("app.routers.webhooks.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("app.routers.webhooks._upsert_contact",
                   return_value=(99, True)):
            resp = client.post(
                "/api/webhooks/instantly",
                json={
                    "email": "new@b.com",
                    "event_type": "email_open",
                    "campaign_id": "c1",
                    "lead": {"email": "new@b.com", "first_name": "New", "last_name": "User"},
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["is_new"] is True


# ---------------------------------------------------------------------------
# 4. POST /api/webhooks/telnyx
# ---------------------------------------------------------------------------

class TestTelnyxWebhook:
    def test_inbound_sms(self, client):
        """
        Valid message.received payload for a known contact → 200
        {status:'ok', msg_id, contact_id}.
        """
        cur = MagicMock()
        # First get_cursor call (SELECT contact by phone) → fetchone returns contact
        # Second get_cursor call (store_telnyx_message) → fetchone returns msg_id
        cur.fetchone.side_effect = [
            {"id": 1, "email": "t@t.com"},      # contact lookup
            {"store_telnyx_message": 42},        # stored message id
        ]

        # threading is used inline in webhooks.py; patch at the stdlib level
        with patch("app.routers.webhooks.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("threading.Thread") as mock_thread:
            resp = client.post(
                "/api/webhooks/telnyx",
                json={
                    "data": {
                        "event_type": "message.received",
                        "id": "evt1",
                        "payload": {
                            "from": {"phone_number": "+1234567890"},
                            "to": [{"phone_number": "+18444322224", "status": "received"}],
                            "id": "msg1",
                            "text": "Hello",
                            "direction": "inbound",
                        },
                    }
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["msg_id"] == 42
        assert data["contact_id"] == 1


# ---------------------------------------------------------------------------
# 5. Shipday webhooks
# ---------------------------------------------------------------------------

class TestShipdayWebhook:
    def test_shipday_ping(self, client):
        """GET /api/webhooks/shipday → 200 {status:'ok'}."""
        resp = client.get("/api/webhooks/shipday")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_shipday_order_delivered(self, client):
        """
        POST a delivered order webhook for a known order → 200
        with status:'ok' and the order_id echoed back.
        """
        cur = MagicMock()
        # fetchone → existing shipday_orders_raw row
        cur.fetchone.return_value = {
            "contact_id": 2,
            "customer_phone": "+1234567890",
            "customer_email": "c@d.com",
            "order_number": "ORD-001",
        }
        cur.rowcount = 1

        # threading is used inline in webhooks.py; patch at the stdlib level
        with patch("app.routers.webhooks.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("threading.Timer") as mock_timer, \
             patch("threading.Thread") as mock_thread:
            resp = client.post(
                "/api/webhooks/shipday",
                json={
                    "orderId": "SD-001",
                    "orderStatus": "delivered",
                    "actualDeliveryTime": "2026-01-01T12:00:00",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["order_id"] == "SD-001"
