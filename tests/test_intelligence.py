"""
Tests for app/routers/intelligence.py
======================================

Covers:
  - POST /api/intelligence/run-cycle
  - GET  /api/intelligence/pending-actions
  - POST /api/intelligence/ingest-instantly-events

No Claude calls — intelligence is pure SQL. Only mock get_cursor.

Run with:
    pytest tests/test_intelligence.py -v
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
# 1. POST /api/intelligence/run-cycle
# ---------------------------------------------------------------------------

class TestRunCycle:
    """Tests for POST /run-cycle."""

    def test_run_cycle_returns_all_phase_keys(self, client):
        """
        Mock all five phase functions; POST /run-cycle must return 200 with
        all five phase keys in the response body.
        """
        collect_result = {
            "email_opens": 5, "email_clicks": 2, "sms_received": 3,
            "calls_completed": 1, "orders_placed": 4, "new_events_total": 15,
            "telnyx_messages": 3, "telnyx_calls": 1,
        }
        profile_result = {
            "rollups_refreshed": True, "active_contacts": 42,
            "lifecycle_distribution": {"active": 200, "lapsed_customer": 50},
        }
        signal_counts = {
            "engaged_no_order": 3, "new_customer_no_repeat": 2,
            "lapsed_reengaged": 0, "reorder_intent": 1,
            "app_customers_for_conversion": 5, "subscription_candidates": 4,
            "high_value_at_risk": 1,
        }
        raw_signals = {
            "engaged_no_order": [{"id": 1, "first_name": "Alice", "email": "a@x.com"}],
            "new_customer_no_repeat": [],
            "lapsed_reengaged": [],
            "reorder_intent": [],
            "app_customers_for_conversion": [],
            "subscription_candidates": [],
            "high_value_at_risk": [],
        }
        route_result = {
            "opportunities_created": 4, "campaign_moves": 0,
            "sms_triggered": 2, "total_actions": 4,
        }
        dispatch_result = {
            "stage_contacts_updated": 10, "stage_campaigns_queued": 2,
            "pending_campaign_moves": 1, "pending_opportunities": 5,
        }

        with patch("app.routers.intelligence._phase_collect", return_value=collect_result), \
             patch("app.routers.intelligence._phase_profile", return_value=profile_result), \
             patch("app.routers.intelligence._phase_signal",
                   return_value=(signal_counts, raw_signals)), \
             patch("app.routers.intelligence._phase_route",
                   return_value=(route_result, [])), \
             patch("app.routers.intelligence._phase_dispatch", return_value=dispatch_result), \
             patch("app.routers.intelligence.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(MagicMock())):
            resp = client.post("/api/intelligence/run-cycle")

        assert resp.status_code == 200
        data = resp.json()
        assert "timestamp" in data
        assert "collect" in data
        assert "profile" in data
        assert "signal" in data
        assert "route" in data
        assert "dispatch" in data

    def test_run_cycle_collect_reflects_data(self, client):
        """Phase data from mocked _phase_collect is returned in the response."""
        collect_result = {
            "email_opens": 10, "email_clicks": 3, "sms_received": 7,
            "calls_completed": 2, "orders_placed": 6, "new_events_total": 28,
            "telnyx_messages": 5, "telnyx_calls": 2,
        }

        with patch("app.routers.intelligence._phase_collect", return_value=collect_result), \
             patch("app.routers.intelligence._phase_profile",
                   return_value={"rollups_refreshed": True, "active_contacts": 0}), \
             patch("app.routers.intelligence._phase_signal",
                   return_value=({"engaged_no_order": 0}, {"engaged_no_order": []})), \
             patch("app.routers.intelligence._phase_route", return_value=({"opportunities_created": 0, "total_actions": 0}, [])), \
             patch("app.routers.intelligence._phase_dispatch",
                   return_value={"stage_contacts_updated": 0, "stage_campaigns_queued": 0,
                                 "pending_campaign_moves": 0, "pending_opportunities": 0}), \
             patch("app.routers.intelligence.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(MagicMock())):
            resp = client.post("/api/intelligence/run-cycle")

        assert resp.status_code == 200
        data = resp.json()
        assert data["collect"]["email_opens"] == 10
        assert data["collect"]["new_events_total"] == 28

    def test_run_cycle_with_empty_contacts(self, client):
        """
        POST /run-cycle with empty contacts (all signal lists empty) still
        completes gracefully — all phases run without error.
        """
        empty_signals = {k: [] for k in [
            "engaged_no_order", "new_customer_no_repeat", "lapsed_reengaged",
            "reorder_intent", "app_customers_for_conversion",
            "subscription_candidates", "high_value_at_risk",
        ]}
        signal_counts = {k: 0 for k in empty_signals}

        with patch("app.routers.intelligence._phase_collect",
                   return_value={"email_opens": 0, "new_events_total": 0,
                                 "telnyx_messages": 0, "telnyx_calls": 0,
                                 "email_clicks": 0, "sms_received": 0,
                                 "calls_completed": 0, "orders_placed": 0}), \
             patch("app.routers.intelligence._phase_profile",
                   return_value={"rollups_refreshed": True, "active_contacts": 0,
                                 "lifecycle_distribution": {}}), \
             patch("app.routers.intelligence._phase_signal",
                   return_value=(signal_counts, empty_signals)), \
             patch("app.routers.intelligence._phase_route",
                   return_value=({"opportunities_created": 0, "campaign_moves": 0,
                                  "sms_triggered": 0, "total_actions": 0}, [])), \
             patch("app.routers.intelligence._phase_dispatch",
                   return_value={"stage_contacts_updated": 0, "stage_campaigns_queued": 0,
                                 "pending_campaign_moves": 0, "pending_opportunities": 0}), \
             patch("app.routers.intelligence.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(MagicMock())):
            resp = client.post("/api/intelligence/run-cycle")

        assert resp.status_code == 200
        data = resp.json()
        assert data["route"]["opportunities_created"] == 0
        assert data["route"]["total_actions"] == 0

    def test_run_cycle_db_error_propagates(self, client):
        """
        When get_cursor raises an unhandled exception the endpoint propagates it.
        TestClient with raise_server_exceptions=True will re-raise it here.
        """
        with patch("app.routers.intelligence.get_cursor",
                   side_effect=Exception("connection refused")):
            with pytest.raises(Exception, match="connection refused"):
                client.post("/api/intelligence/run-cycle")


# ---------------------------------------------------------------------------
# 2. GET /api/intelligence/pending-actions
# ---------------------------------------------------------------------------

class TestPendingActions:
    """Tests for GET /pending-actions."""

    def test_returns_campaign_moves_sms_emails_calls_counts(self, client):
        """
        GET /pending-actions returns campaign_moves, sms_to_send, emails_to_send,
        and sales_calls with correct counts in the summary.
        """
        campaign_row = {
            "queue_id": 1,
            "email": "alice@example.com",
            "phone": "+14041111111",
            "first_name": "Alice",
            "last_name": "Smith",
            "from_campaign": "OLD_CAMPAIGN",
            "to_campaign": "DW-PromoStandard-ActiveEngaged",
            "instantly_campaign_id": "cid_abc",
            "instantly_campaign_name": "DW-PromoStandard-ActiveEngaged",
        }
        sms_opp = {
            "id": 10, "contact_id": 1, "action": "send_sms",
            "priority": "warm", "reason": "New customer no repeat",
            "suggested_message": "Hi Alice, come back!",
            "confidence_score": 0.80,
            "email": "alice@example.com", "phone": "+14041111111",
            "first_name": "Alice", "last_name": "Smith",
            "lifecycle_segment": "active", "total_orders": 1,
        }
        email_opp = {
            "id": 11, "contact_id": 2, "action": "send_email",
            "priority": "warm", "reason": "Engaged no order",
            "suggested_message": "Check our menu!",
            "confidence_score": 0.75,
            "email": "bob@example.com", "phone": None,
            "first_name": "Bob", "last_name": "Jones",
            "lifecycle_segment": "active", "total_orders": 0,
        }
        call_opp = {
            "id": 12, "contact_id": 3, "action": "field_sales_call",
            "priority": "hot", "reason": "High value at risk",
            "suggested_message": "Call Dave",
            "confidence_score": 0.88,
            "email": "dave@example.com", "phone": "+14042222222",
            "first_name": "Dave", "last_name": "Kumar",
            "lifecycle_segment": "active", "total_orders": 8,
        }

        cur = MagicMock()
        # First fetchall → campaign_queue rows
        # Second fetchall → all pending opportunities
        cur.fetchall.side_effect = [
            [campaign_row],
            [sms_opp, email_opp, call_opp],
        ]
        # fetchone for each SMS template lookup (one SMS opp)
        cur.fetchone.return_value = None  # no template found

        with patch("app.routers.intelligence.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/intelligence/pending-actions")

        assert resp.status_code == 200
        data = resp.json()
        assert "campaign_moves" in data
        assert "sms_to_send" in data
        assert "emails_to_send" in data
        assert "sales_calls" in data
        assert "summary" in data

        summary = data["summary"]
        assert summary["campaign_moves"] == 1
        assert summary["sms"] == 1
        assert summary["emails"] == 1
        assert summary["sales_calls"] == 1
        assert summary["total_pending"] == 4  # 1 campaign + 3 opps

    def test_pending_actions_empty(self, client):
        """When there are no pending actions the summary counts are all zero."""
        cur = MagicMock()
        cur.fetchall.return_value = []
        cur.fetchone.return_value = None

        with patch("app.routers.intelligence.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/intelligence/pending-actions")

        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["total_pending"] == 0
        assert data["campaign_moves"] == []
        assert data["sms_to_send"] == []
        assert data["emails_to_send"] == []
        assert data["sales_calls"] == []

    def test_pending_actions_with_sms_template(self, client):
        """SMS opportunities get template_body and template_key injected when template exists."""
        sms_opp = {
            "id": 20, "contact_id": 5, "action": "send_sms",
            "priority": "hot", "reason": "Reorder intent",
            "suggested_message": None,
            "confidence_score": 0.92,
            "email": "carol@example.com", "phone": "+14043333333",
            "first_name": "Carol", "last_name": "Lee",
            "lifecycle_segment": "active", "total_orders": 3,
        }

        template_row = {
            "template_key": "reorder_nudge",
            "body": "Hi {{first_name}}, ready to order today?",
            "variant": "A",
        }

        cur = MagicMock()
        cur.fetchall.side_effect = [
            [],           # campaign_queue — none
            [sms_opp],    # opportunities
        ]
        cur.fetchone.return_value = template_row  # SMS template found

        with patch("app.routers.intelligence.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/intelligence/pending-actions")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["sms_to_send"]) == 1
        sms_entry = data["sms_to_send"][0]
        assert "template_body" in sms_entry
        assert "Carol" in sms_entry["template_body"]


# ---------------------------------------------------------------------------
# 3. POST /api/intelligence/ingest-instantly-events
# ---------------------------------------------------------------------------

class TestIngestInstantlyEvents:
    """Tests for POST /ingest-instantly-events."""

    def test_ingest_open_event_for_known_contact(self, client):
        """POST a single open event for a known contact; ingested:1, errors:0."""
        cur = MagicMock()
        cur.fetchone.return_value = {"id": 42}  # contact found

        with patch("app.routers.intelligence.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/intelligence/ingest-instantly-events",
                json={
                    "events": [
                        {
                            "email": "alice@example.com",
                            "event_type": "open",
                            "campaign_id": "camp_abc123",
                        }
                    ]
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["ingested"] == 1
        assert data["errors"] == 0

    def test_ingest_click_and_reply_events(self, client):
        """POST click and reply events — both map to email_click internally."""
        cur = MagicMock()
        cur.fetchone.return_value = {"id": 10}  # same contact for both

        with patch("app.routers.intelligence.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/intelligence/ingest-instantly-events",
                json={
                    "events": [
                        {"email": "bob@example.com", "event_type": "click",
                         "campaign_id": "camp_xyz"},
                        {"email": "bob@example.com", "event_type": "reply",
                         "campaign_id": "camp_xyz"},
                    ]
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["ingested"] == 2
        assert data["errors"] == 0

    def test_ingest_unknown_contact_counted_as_error(self, client):
        """When the contact email is not in the contacts table, that event is an error."""
        cur = MagicMock()
        cur.fetchone.return_value = None  # contact not found

        with patch("app.routers.intelligence.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/intelligence/ingest-instantly-events",
                json={
                    "events": [
                        {
                            "email": "ghost@unknown.com",
                            "event_type": "open",
                            "campaign_id": "camp_ghost",
                        }
                    ]
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["ingested"] == 0
        assert data["errors"] == 1

    def test_ingest_unknown_campaign_handled_gracefully(self, client):
        """
        An event with a campaign_id not in campaign_routing is still stored
        (the endpoint does not look up campaign_routing) — contact lookup
        determines success or failure, not the campaign.
        """
        cur = MagicMock()
        cur.fetchone.return_value = {"id": 55}  # known contact

        with patch("app.routers.intelligence.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/intelligence/ingest-instantly-events",
                json={
                    "events": [
                        {
                            "email": "known@example.com",
                            "event_type": "open",
                            "campaign_id": "UNKNOWN_CAMPAIGN_ID_9999",
                        }
                    ]
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        # Contact is known → ingested successfully despite unknown campaign
        assert data["ingested"] == 1
        assert data["errors"] == 0

    def test_ingest_mixed_known_unknown_contacts(self, client):
        """Batch with one known and one unknown contact: ingested:1, errors:1."""
        call_count = [0]

        cur = MagicMock()
        # First event: contact found; second event: contact not found
        cur.fetchone.side_effect = [{"id": 1}, None]

        with patch("app.routers.intelligence.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/intelligence/ingest-instantly-events",
                json={
                    "events": [
                        {"email": "known@example.com", "event_type": "open",
                         "campaign_id": "c1"},
                        {"email": "unknown@example.com", "event_type": "open",
                         "campaign_id": "c1"},
                    ]
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["ingested"] == 1
        assert data["errors"] == 1

    def test_ingest_empty_events_list(self, client):
        """POST with empty events list returns total:0, ingested:0, errors:0."""
        cur = MagicMock()

        with patch("app.routers.intelligence.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/intelligence/ingest-instantly-events",
                json={"events": []},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["ingested"] == 0
        assert data["errors"] == 0

    def test_ingest_event_missing_email_counted_as_error(self, client):
        """Events without an email field are counted as errors (not ingested)."""
        cur = MagicMock()

        with patch("app.routers.intelligence.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/intelligence/ingest-instantly-events",
                json={
                    "events": [
                        {"event_type": "open", "campaign_id": "c1"},  # no email
                    ]
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["errors"] == 1
        assert data["ingested"] == 0


# ---------------------------------------------------------------------------
# 4. Direct phase function tests (cover internal phase code)
# ---------------------------------------------------------------------------

class TestPhaseCollect:
    """Test _phase_collect directly to cover its internal logic."""

    def test_phase_collect_no_events(self):
        """_phase_collect with empty events returns all zeros."""
        from app.routers.intelligence import _phase_collect
        cur = MagicMock()
        cur.fetchall.return_value = []
        cur.fetchone.return_value = {"cnt": 0}

        result = _phase_collect(cur)
        assert result["email_opens"] == 0
        assert result["email_clicks"] == 0
        assert result["new_events_total"] == 0
        assert result["telnyx_messages"] == 0
        assert result["telnyx_calls"] == 0

    def test_phase_collect_with_event_types(self):
        """_phase_collect tallies events by type correctly."""
        from app.routers.intelligence import _phase_collect
        event_rows = [
            {"event_type": "email_open", "cnt": 5},
            {"event_type": "email_click", "cnt": 2},
            {"event_type": "sms_received", "cnt": 3},
            {"event_type": "order_placed", "cnt": 4},
        ]
        cur = MagicMock()
        cur.fetchall.return_value = event_rows
        cur.fetchone.return_value = {"cnt": 1}  # for telnyx counts

        result = _phase_collect(cur)
        assert result["email_opens"] == 5
        assert result["email_clicks"] == 2
        assert result["orders_placed"] == 4
        assert result["new_events_total"] == 14  # 5+2+3+4


class TestPhaseProfile:
    """Test _phase_profile directly."""

    def test_phase_profile_returns_correct_keys(self):
        """_phase_profile returns rollups_refreshed, active_contacts, lifecycle_distribution."""
        from app.routers.intelligence import _phase_profile
        cur = MagicMock()
        cur.fetchone.return_value = {"active_contacts": 42}
        cur.fetchall.return_value = [
            {"lifecycle_segment": "active", "cnt": 100},
            {"lifecycle_segment": "lapsed_customer", "cnt": 30},
        ]

        result = _phase_profile(cur)
        assert result["rollups_refreshed"] is True
        assert result["active_contacts"] == 42
        assert result["lifecycle_distribution"]["active"] == 100


class TestPhaseSignal:
    """Test _phase_signal directly."""

    def test_phase_signal_all_empty(self):
        """_phase_signal returns zeros when all stored procs return empty."""
        from app.routers.intelligence import _phase_signal
        cur = MagicMock()
        cur.fetchall.return_value = []

        counts, raw = _phase_signal(cur)
        assert all(v == 0 for v in counts.values())
        assert all(len(v) == 0 for v in raw.values())

    def test_phase_signal_engaged_no_order(self):
        """_phase_signal counts engaged_no_order from detect_engaged_no_order()."""
        from app.routers.intelligence import _phase_signal
        engaged_rows = [
            {"id": 1, "email": "alice@test.com", "first_name": "Alice",
             "last_name": "Smith", "phone": "+14041111111",
             "opens_7d": 3, "clicks_7d": 1, "lifecycle_segment": "active"},
        ]
        cur = MagicMock()
        # First fetchall (detect_engaged_no_order), then the rest return []
        cur.fetchall.side_effect = [engaged_rows] + [[]] * 10

        counts, raw = _phase_signal(cur)
        assert counts["engaged_no_order"] == 1
        assert len(raw["engaged_no_order"]) == 1

    def test_phase_signal_handles_stored_proc_exception(self):
        """_phase_signal catches exceptions from broken stored procs gracefully."""
        from app.routers.intelligence import _phase_signal
        cur = MagicMock()
        cur.fetchall.side_effect = Exception("proc not found")

        # Should not raise — exceptions are caught internally
        counts, raw = _phase_signal(cur)
        # All signals will be empty due to exceptions
        assert all(v == 0 for v in counts.values())


class TestPhaseDispatch:
    """Test _phase_dispatch directly."""

    def test_phase_dispatch_returns_stage_stats(self):
        """_phase_dispatch returns stage engine stats and pending counts."""
        from app.routers.intelligence import _phase_dispatch
        cur = MagicMock()
        cur.fetchone.side_effect = [
            {"contacts_updated": 10, "campaigns_queued": 2},
            {"cnt": 5},
            {"cnt": 12},
        ]

        result = _phase_dispatch(cur)
        assert result["stage_contacts_updated"] == 10
        assert result["stage_campaigns_queued"] == 2
        assert result["pending_campaign_moves"] == 5
        assert result["pending_opportunities"] == 12


class TestRunCycleIntegration:
    """Test run-cycle without mocking phase functions (integration-style)."""

    def test_run_cycle_full_flow_no_signals(self, client):
        """POST /run-cycle with empty signals covers all 5 phases."""
        cur = MagicMock()
        cur.fetchall.return_value = []
        cur.fetchone.side_effect = [
            {"cnt": 0},
            {"cnt": 0},
            {"active_contacts": 0},
            {"contacts_updated": 0, "campaigns_queued": 0},
            {"cnt": 0},
            {"cnt": 0},
        ]

        with patch("app.routers.intelligence.get_cursor",
                   side_effect=lambda commit=True: _cursor_ctx(cur)):
            resp = client.post("/api/intelligence/run-cycle")

        assert resp.status_code == 200
        data = resp.json()
        assert data["collect"]["email_opens"] == 0
        assert data["profile"]["rollups_refreshed"] is True
        assert data["dispatch"]["stage_contacts_updated"] == 0


# ---------------------------------------------------------------------------
# _phase_route — direct tests covering signal routing paths
# ---------------------------------------------------------------------------

class TestPhaseRoute:
    """Test _phase_route directly, covering each signal route."""

    def test_route_engaged_no_order(self):
        """_phase_route creates send_email opportunity for engaged_no_order contacts."""
        from app.routers.intelligence import _phase_route
        cur = MagicMock()
        signals = {
            "engaged_no_order": [{"id": 1, "first_name": "Alice", "last_name": "Smith",
                                   "email": "alice@example.com", "phone": "+14041111111",
                                   "opens_7d": 3, "clicks_7d": 1}],
            "new_customer_no_repeat": [],
            "lapsed_reengaged": [],
            "reorder_intent": [],
            "app_customers_for_conversion": [],
            "subscription_candidates": [],
            "high_value_at_risk": [],
        }
        stats, actions = _phase_route(cur, signals)
        assert stats["opportunities_created"] == 1
        assert len(actions) == 1
        assert actions[0]["action_type"] == "send_email"

    def test_route_new_customer_no_repeat(self):
        """_phase_route creates send_sms opportunity for new_customer_no_repeat contacts."""
        from app.routers.intelligence import _phase_route
        cur = MagicMock()
        signals = {
            "engaged_no_order": [],
            "new_customer_no_repeat": [{"id": 2, "first_name": "Bob", "last_name": "Jones",
                                         "email": "bob@example.com", "phone": "+14042222222"}],
            "lapsed_reengaged": [],
            "reorder_intent": [],
            "app_customers_for_conversion": [],
            "subscription_candidates": [],
            "high_value_at_risk": [],
        }
        stats, actions = _phase_route(cur, signals)
        assert stats["opportunities_created"] == 1
        assert stats["sms_triggered"] == 1
        assert actions[0]["action_type"] == "send_sms"

    def test_route_lapsed_reengaged(self):
        """_phase_route creates sales_call opportunity for lapsed_reengaged contacts."""
        from app.routers.intelligence import _phase_route
        cur = MagicMock()
        signals = {
            "engaged_no_order": [],
            "new_customer_no_repeat": [],
            "lapsed_reengaged": [{"id": 3, "first_name": "Carol", "last_name": "Davis",
                                   "email": "carol@example.com", "phone": "+14043333333",
                                   "lifecycle_segment": "lapsed_customer"}],
            "reorder_intent": [],
            "app_customers_for_conversion": [],
            "subscription_candidates": [],
            "high_value_at_risk": [],
        }
        stats, actions = _phase_route(cur, signals)
        assert stats["opportunities_created"] == 1
        assert actions[0]["action_type"] == "sales_call"

    def test_route_reorder_intent(self):
        """_phase_route creates send_sms for reorder_intent contacts."""
        from app.routers.intelligence import _phase_route
        cur = MagicMock()
        signals = {
            "engaged_no_order": [],
            "new_customer_no_repeat": [],
            "lapsed_reengaged": [],
            "reorder_intent": [{"id": 4, "first_name": "Dave", "phone": "+14044444444",
                                 "last_name": "", "email": ""}],
            "app_customers_for_conversion": [],
            "subscription_candidates": [],
            "high_value_at_risk": [],
        }
        stats, actions = _phase_route(cur, signals)
        assert stats["opportunities_created"] == 1
        assert stats["sms_triggered"] == 1
        assert actions[0]["action_type"] == "send_sms"

    def test_route_app_customers(self):
        """_phase_route creates campaign_move for app_customers_for_conversion."""
        from app.routers.intelligence import _phase_route
        cur = MagicMock()
        signals = {
            "engaged_no_order": [],
            "new_customer_no_repeat": [],
            "lapsed_reengaged": [],
            "reorder_intent": [],
            "app_customers_for_conversion": [{"id": 5, "first_name": "Eve",
                                               "email": "eve@example.com", "phone": "+14045555555",
                                               "last_name": "", "primary_source": "Food Delivery Apps"}],
            "subscription_candidates": [],
            "high_value_at_risk": [],
        }
        stats, actions = _phase_route(cur, signals)
        assert stats["opportunities_created"] == 1
        assert stats["campaign_moves"] == 1
        assert actions[0]["action_type"] == "campaign_move"

    def test_route_subscription_candidates(self):
        """_phase_route creates send_sms for subscription_candidates."""
        from app.routers.intelligence import _phase_route
        cur = MagicMock()
        signals = {
            "engaged_no_order": [],
            "new_customer_no_repeat": [],
            "lapsed_reengaged": [],
            "reorder_intent": [],
            "app_customers_for_conversion": [],
            "subscription_candidates": [{"id": 6, "first_name": "Frank",
                                          "email": "frank@example.com", "phone": "+14046666666",
                                          "last_name": "", "total_orders": 5}],
            "high_value_at_risk": [],
        }
        stats, actions = _phase_route(cur, signals)
        assert stats["opportunities_created"] == 1
        assert stats["sms_triggered"] == 1

    def test_route_high_value_at_risk(self):
        """_phase_route creates sales_call for high_value_at_risk contacts."""
        from app.routers.intelligence import _phase_route
        cur = MagicMock()
        signals = {
            "engaged_no_order": [],
            "new_customer_no_repeat": [],
            "lapsed_reengaged": [],
            "reorder_intent": [],
            "app_customers_for_conversion": [],
            "subscription_candidates": [],
            "high_value_at_risk": [{"id": 7, "first_name": "Grace",
                                     "email": "grace@example.com", "phone": "+14047777777",
                                     "last_name": "", "total_orders": 15}],
        }
        stats, actions = _phase_route(cur, signals)
        assert stats["opportunities_created"] == 1
        assert actions[0]["action_type"] == "sales_call"

    def test_route_empty_signals_returns_zeros(self):
        """_phase_route with all empty signals returns 0 for all counts."""
        from app.routers.intelligence import _phase_route
        cur = MagicMock()
        empty_signals = {
            "engaged_no_order": [],
            "new_customer_no_repeat": [],
            "lapsed_reengaged": [],
            "reorder_intent": [],
            "app_customers_for_conversion": [],
            "subscription_candidates": [],
            "high_value_at_risk": [],
        }
        stats, actions = _phase_route(cur, empty_signals)
        assert stats["opportunities_created"] == 0
        assert actions == []
        assert stats["campaign_moves"] == 0
        assert stats["sms_triggered"] == 0
