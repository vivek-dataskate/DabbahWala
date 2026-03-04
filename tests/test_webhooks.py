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
    c.rowcount = 1
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

    def test_list_campaigns_db_error_returns_empty(self, client):
        """DB exception returns 200 with empty list and error field (graceful degradation)."""
        with patch("app.routers.webhooks.get_cursor",
                   side_effect=Exception("connection refused")):
            resp = client.get("/api/webhooks/campaigns")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert "error" in data


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

    def test_update_stats_partial_fields(self, client):
        """Only required campaign_id with one optional field still updates."""
        cur = MagicMock()
        cur.rowcount = 1

        with patch("app.routers.webhooks.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/webhooks/campaign-stats",
                json={"campaign_id": "c2", "emails_sent": 50},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["campaign_id"] == "c2"

    def test_update_stats_db_error_returns_error_status(self, client):
        """DB exception returns status='error' (not 500)."""
        with patch("app.routers.webhooks.get_cursor",
                   side_effect=Exception("DB write failed")):
            resp = client.post(
                "/api/webhooks/campaign-stats",
                json={"campaign_id": "c-bad"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
        assert "detail" in data

    def test_update_stats_zero_rows_updated(self, client):
        """rowcount=0 when campaign_id not found in campaign_routing."""
        cur = MagicMock()
        cur.rowcount = 0

        with patch("app.routers.webhooks.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/webhooks/campaign-stats",
                json={"campaign_id": "c-missing", "emails_sent": 10},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] == 0


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

    def test_non_dabbahwala_campaign_ignored(self, client):
        """Campaign ID not in campaign_routing returns ignored status."""
        cur = MagicMock()
        cur.fetchall.return_value = [{"instantly_campaign_id": "c1"}]

        with patch("app.routers.webhooks.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/webhooks/instantly",
                json={
                    "event_type": "email_open",
                    "campaign_id": "other-campaign-999",
                    "lead": {"email": "someone@example.com"},
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ignored"
        assert "campaign_id" in data

    def test_unactionable_event_type_ignored(self, client):
        """Non-engagement event types (e.g. email_unsubscribed) are ignored."""
        cur = MagicMock()
        cur.fetchall.return_value = [{"instantly_campaign_id": "c1"}]

        with patch("app.routers.webhooks.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/webhooks/instantly",
                json={
                    "event_type": "email_unsubscribed",
                    "campaign_id": "c1",
                    "lead": {"email": "opt@example.com"},
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ignored"

    def test_lead_with_no_contact_info_ignored(self, client):
        """Lead with empty email and phone returns ignored."""
        cur = MagicMock()
        cur.fetchall.return_value = [{"instantly_campaign_id": "c1"}]

        with patch("app.routers.webhooks.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/webhooks/instantly",
                json={
                    "event_type": "email_open",
                    "campaign_id": "c1",
                    "lead": {"email": "", "phone": ""},
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ignored"


# ---------------------------------------------------------------------------
# 4. POST /api/webhooks/telnyx
# ---------------------------------------------------------------------------

class TestTelnyxWebhook:
    def test_inbound_sms_known_contact(self, client):
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

    def test_non_message_received_event_ignored(self, client):
        """Events other than message.received return ignored without DB access."""
        resp = client.post(
            "/api/webhooks/telnyx",
            json={
                "data": {
                    "event_type": "message.sent",
                    "payload": {},
                }
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ignored"
        assert data.get("event_type") == "message.sent"

    def test_unknown_phone_number_returns_ignored(self, client):
        """Phone number not in contacts returns ignored with contact_not_found reason."""
        cur = MagicMock()
        cur.fetchone.return_value = None  # no contact found

        with patch("app.routers.webhooks.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/webhooks/telnyx",
                json={
                    "data": {
                        "event_type": "message.received",
                        "id": "evt2",
                        "payload": {
                            "from": {"phone_number": "+9999999999"},
                            "to": [{"phone_number": "+18444322224", "status": "received"}],
                            "id": "msg2",
                            "text": "Hello unknown",
                        },
                    }
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ignored"
        assert data["reason"] == "contact_not_found"

    def test_missing_from_number_returns_ignored(self, client):
        """Payload with empty from.phone_number returns ignored."""
        resp = client.post(
            "/api/webhooks/telnyx",
            json={
                "data": {
                    "event_type": "message.received",
                    "payload": {
                        "from": {},
                        "to": [],
                        "id": "msg3",
                        "text": "",
                    },
                }
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ignored"

    def test_store_telnyx_message_db_error_returns_error_status(self, client):
        """DB exception during store_telnyx_message returns status=error."""
        cur = MagicMock()
        cur.fetchone.side_effect = [
            {"id": 10, "email": "cust@example.com"},  # contact found
        ]
        # Second cursor (commit=True) raises on execute
        store_cur = MagicMock()
        store_cur.execute.side_effect = Exception("DB write failed")

        call_count = [0]

        @contextmanager
        def _multi_cursor(commit=False):
            call_count[0] += 1
            if call_count[0] == 1:
                yield cur
            else:
                yield store_cur

        with patch("app.routers.webhooks.get_cursor",
                   side_effect=_multi_cursor):
            resp = client.post(
                "/api/webhooks/telnyx",
                json={
                    "data": {
                        "event_type": "message.received",
                        "id": "evt3",
                        "payload": {
                            "from": {"phone_number": "+1234567890"},
                            "to": [{"phone_number": "+18444322224", "status": "received"}],
                            "id": "msg4",
                            "text": "Test",
                        },
                    }
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"


# ---------------------------------------------------------------------------
# 5. Shipday webhooks
# ---------------------------------------------------------------------------

class TestShipdayWebhook:
    def test_shipday_ping(self, client):
        """GET /api/webhooks/shipday → 200 {status:'ok'}."""
        resp = client.get("/api/webhooks/shipday")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_shipday_order_delivered_schedules_timer(self, client):
        """
        POST a DELIVERED order webhook for a known order → 200 with
        status:'ok', order_id echoed back, agent_cycle='scheduled_4h'.
        """
        cur = MagicMock()
        cur.fetchone.return_value = {
            "contact_id": 2,
            "customer_phone": "+1234567890",
            "customer_email": "c@d.com",
            "order_number": "ORD-001",
        }
        cur.rowcount = 1

        with patch("app.routers.webhooks.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("threading.Timer") as mock_timer, \
             patch("threading.Thread") as mock_thread:
            resp = client.post(
                "/api/webhooks/shipday",
                json={
                    "orderId": "SD-001",
                    "orderStatus": "DELIVERED",
                    "actualDeliveryTime": "2026-01-01T12:00:00",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["order_id"] == "SD-001"
        assert data["mapped_status"] == "delivered"
        assert data["agent_cycle"] == "scheduled_4h"

    def test_shipday_order_failed_fires_immediate_thread(self, client):
        """FAILED status triggers immediate agent thread, not a delayed timer."""
        cur = MagicMock()
        cur.fetchone.return_value = {
            "contact_id": 3,
            "customer_phone": "+1234567890",
            "customer_email": "f@d.com",
            "order_number": "ORD-002",
        }
        cur.rowcount = 1

        with patch("app.routers.webhooks.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("threading.Thread") as mock_thread, \
             patch("threading.Timer") as mock_timer:
            resp = client.post(
                "/api/webhooks/shipday",
                json={"orderId": "SD-002", "orderStatus": "FAILED"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["mapped_status"] == "failed"
        assert data["agent_cycle"] == "triggered"
        mock_thread.assert_called_once()
        mock_timer.assert_not_called()

    def test_shipday_order_not_found_returns_ignored(self, client):
        """Order ID not in shipday_orders_raw returns ignored."""
        cur = MagicMock()
        cur.fetchone.return_value = None

        with patch("app.routers.webhooks.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/webhooks/shipday",
                json={"orderId": "SD-UNKNOWN", "orderStatus": "DELIVERED"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ignored"
        assert data["reason"] == "order_not_found"

    def test_shipday_invalid_auth_token_returns_401(self, monkeypatch, client):
        """When SHIPDAY_WEBHOOK_TOKEN is configured, wrong token returns 401."""
        monkeypatch.setenv("SHIPDAY_WEBHOOK_TOKEN", "correct-secret-xyz")

        resp = client.post(
            "/api/webhooks/shipday",
            content=b'{"orderId": "SD-001", "orderStatus": "DELIVERED"}',
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer wrong-token",
            },
        )

        assert resp.status_code == 401

    def test_shipday_empty_body_verification_ping(self, client):
        """Empty body returns 200 ok (Shipday verification ping)."""
        resp = client.post(
            "/api/webhooks/shipday",
            content=b"",
            headers={"Content-Type": "application/json"},
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_shipday_no_order_id_in_payload(self, client):
        """JSON body without orderId (e.g. verification) returns 200 ok."""
        resp = client.post("/api/webhooks/shipday", json={"ping": "verify"})

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_shipday_in_transit_skips_agent_cycle(self, client):
        """Non-terminal IN_TRANSIT status does not trigger any agent cycle."""
        cur = MagicMock()
        cur.fetchone.return_value = {
            "contact_id": 4,
            "customer_phone": "+1234567890",
            "customer_email": "g@d.com",
            "order_number": "ORD-003",
        }
        cur.rowcount = 1

        with patch("app.routers.webhooks.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("threading.Thread") as mock_thread, \
             patch("threading.Timer") as mock_timer:
            resp = client.post(
                "/api/webhooks/shipday",
                json={"orderId": "SD-003", "orderStatus": "IN_TRANSIT"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_cycle"] == "skipped"
        mock_thread.assert_not_called()
        mock_timer.assert_not_called()


# ---------------------------------------------------------------------------
# 6. _fire_agent_cycle direct tests (lines 36-44)
# ---------------------------------------------------------------------------

class TestFireAgentCycle:
    def test_fire_agent_cycle_calls_run_full_cycle(self):
        """_fire_agent_cycle calls _run_full_cycle with the given contact_id."""
        from app.routers.webhooks import _fire_agent_cycle
        with patch("app.routers.agents._run_full_cycle") as mock_run:
            _fire_agent_cycle(42, "test_trigger")
        mock_run.assert_called_once_with(42)

    def test_fire_agent_cycle_exception_silenced(self):
        """_fire_agent_cycle swallows exceptions from _run_full_cycle."""
        from app.routers.webhooks import _fire_agent_cycle
        with patch("app.routers.agents._run_full_cycle", side_effect=RuntimeError("crash")):
            # Should not raise
            _fire_agent_cycle(1, "test_trigger")


# ---------------------------------------------------------------------------
# 7. _dabbahwala_campaign_ids exception (lines 57-59)
# ---------------------------------------------------------------------------

class TestDabbahwalaCampaignIds:
    def test_db_exception_returns_empty_set(self):
        """_dabbahwala_campaign_ids returns empty set when DB raises."""
        from app.routers.webhooks import _dabbahwala_campaign_ids
        with patch("app.routers.webhooks.get_cursor", side_effect=Exception("DB down")):
            result = _dabbahwala_campaign_ids()
        assert result == set()


# ---------------------------------------------------------------------------
# 8. instantly_webhook edge cases (lines 170-171, 199, 235-240)
# ---------------------------------------------------------------------------

class TestInstantlyWebhookEdgeCases:
    def test_non_json_body_ignored(self, client):
        """Non-JSON body → {status: ignored, reason: non-JSON body}."""
        resp = client.post(
            "/api/webhooks/instantly",
            content=b"not-valid-json",
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ignored"
        assert data["reason"] == "non-JSON body"

    def test_no_campaign_id_processes_anyway(self, client):
        """No campaign_id → logs warning, processes event if actionable."""
        cur = MagicMock()
        cur.execute.side_effect = Exception("ingest_event failed")  # non-fatal

        with patch("app.routers.webhooks.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("app.routers.webhooks._upsert_contact", return_value=(1, False)):
            resp = client.post("/api/webhooks/instantly", json={
                "event_type": "email_open",
                "lead": {"email": "test@example.com"},
                # no campaign_id → covers line 199
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_ingest_event_exception_swallowed(self, client):
        """ingest_event exception in instantly_webhook is non-fatal (lines 235-240)."""
        call_count = [0]
        cur = MagicMock()

        def _execute(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("ingest failed")

        cur.execute.side_effect = _execute

        # _dabbahwala_campaign_ids uses get_cursor, then the upsert block uses it too
        campaign_cur = MagicMock()
        campaign_cur.fetchall.return_value = [{"instantly_campaign_id": "c-x"}]

        n = [0]

        @contextmanager
        def _multi_cur(commit=False):
            n[0] += 1
            if n[0] == 1:
                yield campaign_cur
            else:
                yield cur

        with patch("app.routers.webhooks.get_cursor", side_effect=_multi_cur), \
             patch("app.routers.webhooks._upsert_contact", return_value=(7, False)):
            resp = client.post("/api/webhooks/instantly", json={
                "event_type": "email_open",
                "campaign_id": "c-x",
                "lead": {"email": "alpha@example.com"},
            })

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# 9. telnyx_webhook non-JSON body (lines 268-270)
# ---------------------------------------------------------------------------

class TestTelnyxWebhookEdgeCases:
    def test_non_json_body_ignored(self, client):
        """Non-JSON body → {status: ignored, reason: non-JSON body}."""
        resp = client.post(
            "/api/webhooks/telnyx",
            content=b"not-valid-json",
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ignored"
        assert data["reason"] == "non-JSON body"


# ---------------------------------------------------------------------------
# 10. shipday_webhook edge cases (lines 404-406, 487-488)
# ---------------------------------------------------------------------------

class TestShipdayWebhookEdgeCases:
    def test_non_json_body_returns_ok(self, client):
        """Non-JSON but non-empty body → 200 ok (graceful, no auth token set)."""
        resp = client.post(
            "/api/webhooks/shipday",
            content=b"not-valid-json-but-not-empty",
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_ingest_event_exception_swallowed(self, client):
        """ingest_event exception in shipday_webhook is non-fatal (lines 487-488)."""
        call_count = [0]
        cur = MagicMock()

        def _execute(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 4:
                raise Exception("ingest_event failed")

        cur.execute.side_effect = _execute
        cur.fetchone.return_value = {
            "contact_id": 5,
            "customer_phone": "+1234567890",
            "customer_email": "d@d.com",
            "order_number": "ORD-777",
        }

        with patch("app.routers.webhooks.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("threading.Thread"), \
             patch("threading.Timer"):
            resp = client.post("/api/webhooks/shipday", json={
                "orderId": "SD-777",
                "orderStatus": "IN_TRANSIT",
            })

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
