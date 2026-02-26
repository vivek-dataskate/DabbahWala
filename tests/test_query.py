"""
Tests for app/routers/query.py
================================

Covers:
  Endpoint tests (TestClient with mocked DB)
    - GET  /api/query/categories               — list all categories
    - POST /api/query/                         — pipeline_snapshot, daily_summary,
                                                 unknown category, customer_lookup,
                                                 revenue_trends, order_analytics
    - POST /api/query/execute-opportunity/{id} — not found → 404, pending → queued

Run with:
    pytest tests/test_query.py -v
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


def _multi_fetchone(*values):
    """Return a cursor whose fetchone() cycles through `values`."""
    cur = MagicMock()
    cur.fetchall.return_value = []
    it = iter(values)

    def _next():
        try:
            return next(it)
        except StopIteration:
            return None

    cur.fetchone.side_effect = _next
    return cur


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/query/categories
# ─────────────────────────────────────────────────────────────────────────────

class TestCategories:
    def test_list_categories(self, client):
        """GET /api/query/categories — 200 with at least 5 keys in categories dict."""
        resp = client.get("/api/query/categories")

        assert resp.status_code == 200
        data = resp.json()
        assert "categories" in data
        assert len(data["categories"]) >= 5


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/query/
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleQuery:
    def test_pipeline_snapshot(self, client):
        """POST pipeline_snapshot — queries lifecycle segments, returns answer."""
        # _handle_pipeline_snapshot first calls the stored proc (fetchone=None → fallback),
        # then runs 4 direct queries: segments (fetchall), total (fetchone), email_promo,
        # sms_promo
        segment_rows = [{"lifecycle_segment": "active", "cnt": 50}]
        count_row = {"total": 50}
        promo_row = {"cnt": 30}

        call_count = [0]
        cur = MagicMock()
        cur.fetchall.return_value = segment_rows

        def _fetchone():
            call_count[0] += 1
            if call_count[0] == 1:
                return None  # stored proc returns None → trigger fallback
            if call_count[0] == 2:
                return count_row
            return promo_row

        cur.fetchone.side_effect = _fetchone

        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "pipeline_snapshot"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["category"] == "pipeline_snapshot"
        assert "answer" in data

    def test_daily_summary(self, client):
        """POST daily_summary — returns structured answer about today's activity."""
        # _handle_daily_summary uses one cursor for all queries:
        # fetchall → events list, fetchone → order_row, fetchone → week_row,
        # fetchall → transitions, fetchone → pending_opps {"cnt":N},
        # fetchone → pending_campaigns {"cnt":N}
        order_row = {"order_count": 5, "revenue": 120.0}
        count_row = {"cnt": 3}
        call_count = [0]
        cur = MagicMock()
        cur.fetchall.return_value = []  # events and transitions

        def _fetchone():
            call_count[0] += 1
            if call_count[0] in (1, 2):
                return order_row  # today orders and week orders
            return count_row      # pending_opps and pending_campaigns cnt

        cur.fetchone.side_effect = _fetchone

        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "daily_summary"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["category"] == "daily_summary"
        assert "answer" in data

    def test_unknown_category(self, client):
        """POST with unknown category — 200 with error message in answer."""
        resp = client.post("/api/query/", json={"category": "not_a_real_category"})

        assert resp.status_code == 200
        data = resp.json()
        assert "answer" in data
        # The handler includes "Unknown category" or lists available categories
        answer_lower = data["answer"].lower()
        assert "unknown" in answer_lower or "not_a_real_category" in answer_lower

    def test_customer_lookup_by_email(self, client):
        """POST customer_lookup with email that doesn't exist — 200 with 'not found'."""
        cur = _make_cursor(fetchone_val=None)

        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/query/",
                json={"category": "customer_lookup", "contact_email": "missing@test.com"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "answer" in data
        answer_lower = data["answer"].lower()
        assert "not found" in answer_lower or "no customer" in answer_lower

    def test_revenue_trends(self, client):
        """POST revenue_trends with no data — 200 with an answer string."""
        cur = _make_cursor(rows=[])

        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "revenue_trends"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["category"] == "revenue_trends"
        assert "answer" in data

    def test_order_analytics(self, client):
        """POST order_analytics — 200 with answer."""
        # _handle_order_analytics uses one cursor for all queries:
        # fetchall → top_dishes, fetchall → daily, fetchone → avg_row,
        # fetchone → repeat_row, fetchall → sources
        dish_rows = [
            {"item_name": "Dal", "total_qty": 10, "order_count": 5, "total_revenue": 89.90}
        ]
        avg_row = {"total_orders": 10, "avg_order": 8.99}
        repeat_row = {"total_customers": 8, "repeat_customers": 3}

        fetchall_call = [0]
        fetchone_call = [0]
        cur = MagicMock()

        def _fetchall():
            fetchall_call[0] += 1
            if fetchall_call[0] == 1:
                return dish_rows  # top_dishes
            return []             # daily and sources

        def _fetchone():
            fetchone_call[0] += 1
            if fetchone_call[0] == 1:
                return avg_row
            return repeat_row

        cur.fetchall.side_effect = _fetchall
        cur.fetchone.side_effect = _fetchone

        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "order_analytics"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["category"] == "order_analytics"
        assert "answer" in data


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/query/execute-opportunity/{opportunity_id}
# ─────────────────────────────────────────────────────────────────────────────

class TestExecuteOpportunity:
    def test_execute_not_found(self, client):
        """POST execute-opportunity/999 — opportunity not found — 404."""
        cur = _make_cursor(fetchone_val=None)

        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/execute-opportunity/999")

        assert resp.status_code == 404

    def test_execute_pending(self, client):
        """POST execute-opportunity/1 — enqueues action — 200 {status:'queued'}."""
        opp_row = {
            "id": 1,
            "contact_id": 2,
            "action": "send_sms",
            "suggested_message": "Hi there! Check our menu.",
            "priority": "warm",
            "first_name": "John",
            "last_name": "Doe",
            "email": "john@example.com",
            "phone": "+12145550001",
        }
        action_row = {"id": 42}

        call_count = [0]
        cur = MagicMock()

        def _fetchone():
            call_count[0] += 1
            if call_count[0] == 1:
                return opp_row  # SELECT opportunity
            return action_row   # RETURNING id from INSERT

        cur.fetchone.side_effect = _fetchone

        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/execute-opportunity/1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        assert "action_id" in data


# ─────────────────────────────────────────────────────────────────────────────
# TestCampaignPerformance
# ─────────────────────────────────────────────────────────────────────────────

class TestCampaignPerformance:
    def test_campaign_performance_empty(self, client):
        """campaign_performance with no data returns 'No campaign data available yet.'"""
        cur = _make_cursor(rows=[])
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "campaign_performance"})
        assert resp.status_code == 200
        data = resp.json()
        assert "No campaign data" in data["answer"] or "campaign" in data["answer"].lower()

    def test_campaign_performance_with_data(self, client):
        """campaign_performance with rows returns formatted answer."""
        camp_rows = [
            {"current_campaign": "DW-Promo", "contacts": 50, "opens": 10, "clicks": 3, "orders": 2},
        ]
        pending_rows = [{"to_campaign": "DW-Promo", "cnt": 5}]
        cur = MagicMock()
        cur.fetchall.side_effect = [camp_rows, pending_rows]
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "campaign_performance"})
        assert resp.status_code == 200
        data = resp.json()
        assert "DW-Promo" in data["answer"]


# ─────────────────────────────────────────────────────────────────────────────
# TestWhoToContact
# ─────────────────────────────────────────────────────────────────────────────

class TestWhoToContact:
    def test_who_to_contact_empty(self, client):
        """who_to_contact with no opportunities returns 'No pending opportunities'."""
        cur = _make_cursor(rows=[])
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "who_to_contact"})
        assert resp.status_code == 200
        data = resp.json()
        assert "No pending opportunities" in data["answer"] or "intelligence" in data["answer"].lower()

    def test_who_to_contact_with_hot_opportunity(self, client):
        """who_to_contact with hot opportunity returns formatted contacts."""
        opp_rows = [
            {
                "id": 1, "contact_id": 10, "action": "send_sms", "priority": "hot",
                "reason": "Lapsed re-engaged", "suggested_message": "Hi Alice!",
                "confidence_score": 0.90,
                "first_name": "Alice", "last_name": "Smith",
                "email": "alice@test.com", "phone": "+14041111111",
                "lifecycle_segment": "active", "total_orders": 5,
            }
        ]
        cur = MagicMock()
        cur.fetchall.side_effect = [opp_rows, []]  # opps, reactivation
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "who_to_contact"})
        assert resp.status_code == 200
        data = resp.json()
        assert "HOT" in data["answer"] or "Alice" in data["answer"]


# ─────────────────────────────────────────────────────────────────────────────
# TestOrderSummaryByOrderDate
# ─────────────────────────────────────────────────────────────────────────────

class TestOrderSummaryByOrderDate:
    def test_empty_returns_no_orders_message(self, client):
        """order_summary_by_order_date with no data returns 'No orders found'."""
        cur = MagicMock()
        cur.fetchall.return_value = []
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "order_summary_by_order_date",
                "date_from": "2026-01-01",
                "date_to": "2026-01-02",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "No orders" in data["answer"]

    def test_with_data_returns_table(self, client):
        """order_summary_by_order_date with rows returns formatted markdown table."""
        day_rows = [
            {"order_day": "2026-01-15", "order_count": 5, "unique_customers": 4,
             "revenue": 89.90, "avg_order_value": 17.98}
        ]
        source_rows = [{"source": "Website", "cnt": 5, "revenue": 89.90}]
        cur = MagicMock()
        cur.fetchall.side_effect = [day_rows, source_rows, []]  # days, sources, items
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "order_summary_by_order_date",
                "date_from": "2026-01-15",
                "date_to": "2026-01-20",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "2026-01-15" in data["answer"]


# ─────────────────────────────────────────────────────────────────────────────
# TestOrderSummaryByDeliveryDate
# ─────────────────────────────────────────────────────────────────────────────

class TestOrderSummaryByDeliveryDate:
    def test_empty_returns_message(self, client):
        """order_summary_by_delivery_date with no data returns 'No orders found'."""
        cur = _make_cursor(rows=[])
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "order_summary_by_delivery_date",
                "date_from": "2026-01-01",
                "date_to": "2026-01-01",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "No orders" in data["answer"]

    def test_with_data_returns_summary(self, client):
        """order_summary_by_delivery_date with multi-day range returns table."""
        day_rows = [
            {"delivery_date": "2026-01-15", "order_count": 8, "unique_customers": 6,
             "revenue": 150.0, "avg_order_value": 18.75},
        ]
        cur = MagicMock()
        cur.fetchall.side_effect = [day_rows, []]  # days, items (not single day)
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "order_summary_by_delivery_date",
                "date_from": "2026-01-14",
                "date_to": "2026-01-16",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "8" in data["answer"] or "Orders" in data["answer"]


# ─────────────────────────────────────────────────────────────────────────────
# TestCommunicationHistory
# ─────────────────────────────────────────────────────────────────────────────

class TestCommunicationHistory:
    def test_no_identifier_returns_please_provide(self, client):
        """communication_history with no email/phone/name returns guidance message."""
        resp = client.post("/api/query/", json={"category": "communication_history"})
        assert resp.status_code == 200
        data = resp.json()
        assert "Please provide" in data["answer"]

    def test_lookup_by_email_not_found(self, client):
        """communication_history with unknown email returns 'No customer found'."""
        cur = _make_cursor(fetchone_val=None)
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "communication_history",
                "contact_email": "nobody@test.com",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "No customer" in data["answer"]

    def test_lookup_by_phone_not_found(self, client):
        """communication_history with unknown phone returns 'No customer found'."""
        cur = _make_cursor(fetchone_val=None)
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "communication_history",
                "contact_phone": "+10000000001",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "No customer" in data["answer"]

    def test_lookup_by_name_multiple_matches(self, client):
        """communication_history with name matching multiple contacts returns list."""
        name_rows = [
            {"id": 1, "first_name": "Alice", "last_name": "Smith",
             "email": "a@x.com", "phone": "+14041111111"},
            {"id": 2, "first_name": "Alice", "last_name": "Jones",
             "email": "a2@x.com", "phone": None},
        ]
        cur = _make_cursor(rows=name_rows)
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "communication_history",
                "contact_name": "Alice",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "Alice" in data["answer"]

    def test_lookup_by_email_found_no_phone(self, client):
        """communication_history for contact with no phone number returns guidance."""
        contact_row = {"id": 5, "first_name": "Bob", "last_name": "Jones",
                       "email": "bob@test.com", "phone": ""}
        cur = _make_cursor(fetchone_val=contact_row)
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "communication_history",
                "contact_email": "bob@test.com",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "phone number" in data["answer"].lower() or "No phone" in data["answer"]


# ─────────────────────────────────────────────────────────────────────────────
# TestGroundTeamNotes
# ─────────────────────────────────────────────────────────────────────────────

class TestGroundTeamNotes:
    def test_ground_team_notes_empty(self, client):
        """ground_team_notes with no results returns 'No ground team notes found'."""
        cur = _make_cursor(rows=[])
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "ground_team_notes"})
        assert resp.status_code == 200
        data = resp.json()
        assert "No ground team notes" in data["answer"]

    def test_ground_team_notes_with_data(self, client):
        """ground_team_notes with rows returns formatted notes."""
        note_rows = [{"title": "Morning Route", "body": "All deliveries complete",
                      "author": "Driver1", "created_at": "2026-01-15",
                      "body_preview": "All deliveries complete"}]
        cur = _make_cursor(rows=note_rows)
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "ground_team_notes"})
        assert resp.status_code == 200
        data = resp.json()
        assert "Morning Route" in data["answer"] or "Driver1" in data["answer"]


# ─────────────────────────────────────────────────────────────────────────────
# TestAdCopies
# ─────────────────────────────────────────────────────────────────────────────

class TestAdCopies:
    def test_ad_copies_empty(self, client):
        """ad_copies with no results returns 'No ad copies found'."""
        cur = _make_cursor(rows=[])
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "ad_copies"})
        assert resp.status_code == 200
        data = resp.json()
        assert "No ad copies" in data["answer"]

    def test_ad_copies_with_data(self, client):
        """ad_copies with rows returns formatted copies."""
        copy_rows = [{"title": "Eid Special", "body": "Order now!",
                      "created_at": "2026-01-15", "body_preview": "Order now!"}]
        cur = _make_cursor(rows=copy_rows)
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "ad_copies"})
        assert resp.status_code == 200
        data = resp.json()
        assert "Eid Special" in data["answer"]


# ─────────────────────────────────────────────────────────────────────────────
# TestSubmitInput / TestTeamNotes
# ─────────────────────────────────────────────────────────────────────────────

class TestSubmitInput:
    def test_submit_input_saves_note(self, client):
        """submit_input with valid content saves note and returns confirmation."""
        cur = _make_cursor(fetchone_val={"id": 99})
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=True: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "submit_input",
                "question": "Delivered to 5 houses on Elm Street today, all happy!",
                "author": "Driver1",
                "input_type": "ground_note",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "99" in data["answer"] or "saved" in data["answer"].lower()

    def test_submit_input_too_short(self, client):
        """submit_input with content < 5 chars returns guidance message."""
        resp = client.post("/api/query/", json={
            "category": "submit_input",
            "question": "Hi",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "Please provide" in data["answer"] or "5 characters" in data["answer"]

    def test_team_notes_browse_empty(self, client):
        """team_notes in browse mode with no notes returns guidance message."""
        cur = _make_cursor(rows=[])
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "team_notes",
                "question": "",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "No team notes" in data["answer"]

    def test_team_notes_browse_with_data(self, client):
        """team_notes browse mode with rows returns formatted list."""
        note_rows = [
            {"id": 1, "content_type": "ground_note", "title": "Route Update",
             "body": "Took alternate route due to traffic", "author": "Bob",
             "created_at": "2026-01-15"},
        ]
        cur = _make_cursor(rows=note_rows)
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "team_notes",
                "question": "",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "Route Update" in data["answer"] or "Bob" in data["answer"]


# ─────────────────────────────────────────────────────────────────────────────
# TestSmsPerformance / TestEmailPerformance
# ─────────────────────────────────────────────────────────────────────────────

class TestSmsPerformance:
    def test_sms_performance_empty(self, client):
        """sms_performance with no rows returns 'No SMS broadcasts found'."""
        cur = _make_cursor(rows=[])
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "sms_performance"})
        assert resp.status_code == 200
        data = resp.json()
        assert "No SMS broadcasts" in data["answer"]

    def test_sms_performance_with_data(self, client):
        """sms_performance with rows returns formatted stats."""
        rows = [{
            "id": 1, "title": "Eid Blast", "broadcast_type": "promotional",
            "target_type": "all_customers", "target_date": None,
            "status": "completed", "total_recipients": 100,
            "sent_sms": 95, "failed_count": 5,
            "created_by": "admin", "created_at": "2026-01-15T00:00:00",
            "completed_at": "2026-01-15T01:00:00",
        }]
        cur = _make_cursor(rows=rows)
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "sms_performance",
                "date_from": "2026-01-01", "date_to": "2026-01-31",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "Eid Blast" in data["answer"]


class TestEmailPerformance:
    def test_email_performance_empty(self, client):
        """email_performance with no rows returns 'No email broadcasts found'."""
        cur = _make_cursor(rows=[])
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "email_performance"})
        assert resp.status_code == 200
        data = resp.json()
        assert "No email broadcasts" in data["answer"]

    def test_email_performance_with_data(self, client):
        """email_performance with rows returns formatted stats."""
        rows = [{
            "id": 2, "title": "Weekly Newsletter", "broadcast_type": "promotional",
            "email_subject": "This week's menu",
            "target_type": "all_customers", "target_date": None,
            "status": "completed", "total_recipients": 200,
            "sent_email": 190, "failed_count": 10,
            "created_by": "admin", "created_at": "2026-01-20T00:00:00",
            "completed_at": "2026-01-20T02:00:00",
        }]
        cur = _make_cursor(rows=rows)
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "email_performance"})
        assert resp.status_code == 200
        data = resp.json()
        assert "Weekly Newsletter" in data["answer"]


# ─────────────────────────────────────────────────────────────────────────────
# TestActivityReport / TestOutcomeReport
# ─────────────────────────────────────────────────────────────────────────────

class TestActivityReport:
    def test_activity_report_empty(self, client):
        """activity_report with no events returns 'No activity found'."""
        cur = _make_cursor(rows=[])
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "activity_report"})
        assert resp.status_code == 200
        data = resp.json()
        assert "No activity" in data["answer"]

    def test_activity_report_with_data(self, client):
        """activity_report with event rows returns formatted report."""
        by_type = [
            {"event_type": "email_open", "count": 45},
            {"event_type": "order_placed", "count": 12},
        ]
        by_day = [
            {"day": "2026-01-15", "event_type": "email_open", "count": 10},
        ]
        cur = MagicMock()
        cur.fetchall.side_effect = [by_type, by_day]
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "activity_report",
                "date_from": "2026-01-14", "date_to": "2026-01-16",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "Email Opens" in data["answer"] or "45" in data["answer"]


class TestOutcomeReport:
    def test_outcome_report_no_data(self, client):
        """outcome_report with empty DB returns the report header."""
        cur = MagicMock()
        cur.fetchall.return_value = []
        cur.fetchone.return_value = {"total": 0, "attributed": 0,
                                     "winback_count": 0, "winback_revenue": 0}
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "outcome_report"})
        assert resp.status_code == 200
        data = resp.json()
        assert "Outcome Report" in data["answer"]

    def test_outcome_report_with_transitions(self, client):
        """outcome_report with lifecycle transitions returns them in the answer."""
        transitions = [
            {"prev_lifecycle": "lapsed_customer", "new_lifecycle": "active_customer", "count": 5}
        ]
        pipeline = [{"lifecycle_segment": "active_customer", "count": 100}]

        fetchall_call = [0]
        fetchone_call = [0]
        cur = MagicMock()

        def _fetchall():
            fetchall_call[0] += 1
            if fetchall_call[0] == 1:
                return transitions
            if fetchall_call[0] == 2:
                return pipeline
            return []

        def _fetchone():
            fetchone_call[0] += 1
            if fetchone_call[0] == 1:
                return {"total": 20}
            if fetchone_call[0] == 2:
                return {"attributed": 8}
            return {"winback_count": 5, "winback_revenue": 350.0}

        cur.fetchall.side_effect = _fetchall
        cur.fetchone.side_effect = _fetchone
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "outcome_report"})
        assert resp.status_code == 200
        data = resp.json()
        assert "lapsed" in data["answer"].lower() or "Lifecycle" in data["answer"]


# ─────────────────────────────────────────────────────────────────────────────
# TestBroadcastHistory
# ─────────────────────────────────────────────────────────────────────────────

class TestBroadcastHistory:
    def test_broadcast_history_empty(self, client):
        """broadcast_history with no rows returns 'No broadcast history found'."""
        cur = _make_cursor(rows=[])
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "broadcast_history"})
        assert resp.status_code == 200
        data = resp.json()
        assert "No broadcast history" in data["answer"] or "broadcast" in data["answer"].lower()

    def test_broadcast_history_with_data(self, client):
        """broadcast_history with rows returns list of broadcasts."""
        rows = [{
            "id": 1, "title": "Eid Blast", "broadcast_type": "promotional",
            "channels": ["sms"], "target_type": "all_customers", "target_date": None,
            "status": "completed", "total_recipients": 100,
            "sent_sms": 95, "sent_email": 0, "failed_count": 5,
            "created_by": "admin", "created_at": "2026-01-15",
            "completed_at": "2026-01-15",
        }]
        cur = _make_cursor(rows=rows)
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "broadcast_history"})
        assert resp.status_code == 200
        data = resp.json()
        assert "Eid Blast" in data["answer"] or "broadcast" in data["answer"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# TestFreeFormNoApiKey
# ─────────────────────────────────────────────────────────────────────────────

class TestFreeForm:
    def test_free_form_no_api_key(self, client):
        """free_form with ANTHROPIC_API_KEY='' returns 'AI queries require' message."""
        with patch("app.routers.query.ANTHROPIC_API_KEY", ""):
            resp = client.post("/api/query/", json={
                "category": "free_form",
                "question": "How many customers do we have?",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "AI queries require" in data["answer"] or "ANTHROPIC_API_KEY" in data["answer"]

    def test_free_form_empty_question(self, client):
        """free_form with empty question returns 'Please type a question'."""
        with patch("app.routers.query.ANTHROPIC_API_KEY", ""):
            resp = client.post("/api/query/", json={
                "category": "free_form",
                "question": "",
            })
        assert resp.status_code == 200
        data = resp.json()
        # No API key → returns "AI queries require" before checking question
        assert "AI queries require" in data["answer"] or "Please type" in data["answer"]


# ─────────────────────────────────────────────────────────────────────────────
# TestToneEndpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestToneEndpoint:
    def test_tone_no_api_key(self, client):
        """POST /api/query/tone with no ANTHROPIC_API_KEY returns error dict."""
        with patch("app.routers.query.os") as mock_os:
            mock_os.environ.get.return_value = ""
            resp = client.post("/api/query/tone", json={
                "contact_email": "alice@test.com",
                "goal": "Re-engage lapsed customer",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_tone_contact_not_found(self, client):
        """POST /api/query/tone with unknown email returns error dict."""
        cur = _make_cursor(fetchone_val=None)
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("app.routers.query.os") as mock_os:
            mock_os.environ.get.return_value = "test-api-key"
            resp = client.post("/api/query/tone", json={
                "contact_email": "nobody@test.com",
                "goal": "Re-engage",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data


# ─────────────────────────────────────────────────────────────────────────────
# TestCustomerLookupByPhone
# ─────────────────────────────────────────────────────────────────────────────

class TestCustomerLookupExtended:
    def test_customer_lookup_by_phone_not_found(self, client):
        """customer_lookup by phone with no match returns 'No customer found with phone'."""
        cur = _make_cursor(fetchone_val=None)
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "customer_lookup",
                "contact_phone": "+10000000001",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "No customer found" in data["answer"]

    def test_customer_lookup_by_name_multiple(self, client):
        """customer_lookup by name with multiple matches returns list."""
        matches = [
            {"id": 1, "first_name": "Alice", "last_name": "A",
             "email": "a1@test.com", "lifecycle_segment": "active", "total_orders": 3},
            {"id": 2, "first_name": "Alice", "last_name": "B",
             "email": "a2@test.com", "lifecycle_segment": "lapsed_customer", "total_orders": 1},
        ]
        cur = _make_cursor(rows=matches)
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "customer_lookup",
                "contact_name": "Alice",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "Alice" in data["answer"]
        assert "2 customers" in data["answer"] or "found for" in data["answer"]

    def test_customer_lookup_by_name_no_match(self, client):
        """customer_lookup by name with no match returns 'No customer found'."""
        cur = _make_cursor(rows=[])
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "customer_lookup",
                "contact_name": "ZZUnknown",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "No customer found" in data["answer"]

    def test_customer_lookup_no_identifier(self, client):
        """customer_lookup with no email/phone/name returns guidance message."""
        resp = client.post("/api/query/", json={
            "category": "customer_lookup",
            "question": "",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "provide" in data["answer"].lower() or "Please" in data["answer"]


# ─────────────────────────────────────────────────────────────────────────────
# TestPipelineSnapshotWithStoredProc
# ─────────────────────────────────────────────────────────────────────────────

class TestPipelineSnapshotStoredProc:
    def test_pipeline_snapshot_uses_stored_proc_result(self, client):
        """pipeline_snapshot uses stored proc result when it returns a dict."""
        stored_proc_result = {
            "segments": {"active": 100, "lapsed_customer": 30},
            "email_promo_count": 80,
            "sms_promo_count": 60,
        }
        cur = _make_cursor(fetchone_val={"get_lifecycle_summary": stored_proc_result})
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "pipeline_snapshot"})
        assert resp.status_code == 200
        data = resp.json()
        assert "active" in data["answer"].lower() or "Pipeline" in data["answer"]
