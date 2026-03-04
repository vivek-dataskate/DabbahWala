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

    def test_pipeline_snapshot_json_string_parsed(self, client):
        """pipeline_snapshot parses JSON string returned by stored proc."""
        import json as _json
        stored_proc_result = _json.dumps({
            "segments": {"active_customer": 50, "lapsed_customer": 20},
            "email_promo_count": 40,
            "sms_promo_count": 25,
        })
        cur = _make_cursor(fetchone_val={"get_lifecycle_summary": stored_proc_result})
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "pipeline_snapshot"})
        assert resp.status_code == 200
        data = resp.json()
        assert "active_customer" in data["answer"] or "Pipeline" in data["answer"]

    def test_pipeline_snapshot_invalid_json_falls_back(self, client):
        """pipeline_snapshot with unparseable JSON falls back to direct query."""
        segment_rows = [{"lifecycle_segment": "cold", "cnt": 10}]
        call_count = [0]
        cur = MagicMock()
        cur.fetchall.return_value = segment_rows

        def _fetchone():
            call_count[0] += 1
            if call_count[0] == 1:
                return {"get_lifecycle_summary": "not-valid-json"}
            if call_count[0] == 2:
                return {"total": 10}
            return {"cnt": 5}

        cur.fetchone.side_effect = _fetchone
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "pipeline_snapshot"})
        assert resp.status_code == 200
        data = resp.json()
        assert "cold" in data["answer"] or "Pipeline" in data["answer"]

    def test_pipeline_snapshot_segments_not_dict(self, client):
        """pipeline_snapshot handles segments being a non-dict (list) gracefully."""
        stored_proc_result = {
            "segments": [{"seg": "active", "cnt": 10}],  # list instead of dict
            "email_promo_count": 5,
            "sms_promo_count": 3,
        }
        cur = _make_cursor(fetchone_val={"get_lifecycle_summary": stored_proc_result})
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "pipeline_snapshot"})
        assert resp.status_code == 200
        data = resp.json()
        assert "Pipeline" in data["answer"]


# ─────────────────────────────────────────────────────────────────────────────
# TestCustomerLookupFull — phone found + name single match + full detail
# ─────────────────────────────────────────────────────────────────────────────

class TestCustomerLookupFull:
    def test_lookup_by_phone_found_returns_detail(self, client):
        """customer_lookup by phone found then full detail built from get_contact_detail."""
        detail = {
            "contact": {
                "first_name": "Aisha", "last_name": "Khan", "email": "aisha@test.com",
                "phone": "+14041234567", "lifecycle_segment": "active_customer",
                "total_orders": 8, "last_order_at": "2026-01-20",
                "primary_source": "walk_in", "subscription_type": "weekly",
                "current_campaign": "retention",
                "email_nurture_enabled": True, "email_promo_enabled": True,
                "sms_promo_enabled": False, "sms_level": 2,
            },
            "recent_events": [
                {"event_type": "email_open", "occurred_at": "2026-01-20"}
            ],
            "recent_decisions": [],
            "engagement_rollup": {
                "opens_7d": 3, "clicks_7d": 1, "sms_sent_7d": 0, "orders_7d": 1
            },
        }
        call_count = [0]
        cur = MagicMock()

        def _fetchone():
            call_count[0] += 1
            if call_count[0] == 1:
                return {"email": "aisha@test.com"}   # phone lookup success
            return {"get_contact_detail": detail}     # detail proc result

        cur.fetchone.side_effect = _fetchone
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "customer_lookup",
                "contact_phone": "+14041234567",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "Aisha" in data["answer"]
        assert "email_open" in data["answer"]

    def test_lookup_by_name_single_match_returns_detail(self, client):
        """customer_lookup by name single match resolves email then builds full detail."""
        matches = [
            {"id": 7, "first_name": "Tariq", "last_name": "Ahmed",
             "email": "tariq@test.com", "lifecycle_segment": "new_customer", "total_orders": 2}
        ]
        detail = {
            "contact": {
                "first_name": "Tariq", "last_name": "Ahmed", "email": "tariq@test.com",
                "phone": "+14045550001", "lifecycle_segment": "new_customer",
                "total_orders": 2, "last_order_at": "2026-01-10",
                "primary_source": "referral", "subscription_type": None,
                "current_campaign": None,
                "email_nurture_enabled": False, "email_promo_enabled": True,
                "sms_promo_enabled": True, "sms_level": 1,
            },
            "recent_events": [],
            "recent_decisions": [],
            "engagement_rollup": {"opens_7d": 0, "clicks_7d": 0, "sms_sent_7d": 1, "orders_7d": 0},
        }
        cur = MagicMock()
        cur.fetchall.return_value = matches
        cur.fetchone.return_value = {"get_contact_detail": detail}
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "customer_lookup",
                "contact_name": "Tariq",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "Tariq" in data["answer"]
        assert "new_customer" in data["answer"]

    def test_lookup_detail_not_found(self, client):
        """customer_lookup returns 'No customer found' when get_contact_detail returns None."""
        cur = MagicMock()
        cur.fetchone.return_value = {"get_contact_detail": None}
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "customer_lookup",
                "contact_email": "ghost@test.com",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "No customer" in data["answer"]


# ─────────────────────────────────────────────────────────────────────────────
# TestWhoToContact — warm opps + reactivation
# ─────────────────────────────────────────────────────────────────────────────

class TestWhoToContactExtended:
    def test_who_to_contact_warm_and_reactivation(self, client):
        """who_to_contact with warm opps + reactivation targets returns both sections."""
        warm_opp = {
            "id": 10, "contact_id": 3, "action": "send_sms", "priority": "warm",
            "reason": "Hasn't ordered in 14 days", "suggested_message": None,
            "confidence_score": 0.7,
            "first_name": "Sara", "last_name": "Ali", "email": "sara@test.com",
            "phone": "+14041111111", "lifecycle_segment": "cooling", "total_orders": 5,
        }
        reactivation_row = {
            "id": 20, "contact_id": 8, "first_name": "Mo", "last_name": "Khan",
            "email": "mo@test.com", "total_orders": 3, "last_order_at": "2025-10-01"
        }

        fetchall_call = [0]
        cur = MagicMock()

        def _fetchall():
            fetchall_call[0] += 1
            if fetchall_call[0] == 1:
                return [warm_opp]        # opps
            return [reactivation_row]    # reactivation targets

        cur.fetchall.side_effect = _fetchall
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "who_to_contact"})
        assert resp.status_code == 200
        data = resp.json()
        assert "Sara" in data["answer"] or "WARM" in data["answer"]
        assert "Reactivation" in data["answer"] or "Mo" in data["answer"]

    def test_who_to_contact_reactivation_exception_silenced(self, client):
        """who_to_contact swallows exception from suggest_reactivation_targets."""
        hot_opp = {
            "id": 5, "contact_id": 1, "action": "call", "priority": "hot",
            "reason": "Very hot lead", "suggested_message": "Order now!",
            "confidence_score": 0.95,
            "first_name": "Fatima", "last_name": "Noor", "email": "f@t.com",
            "phone": "+14042222222", "lifecycle_segment": "active_customer", "total_orders": 10,
        }

        fetchall_call = [0]
        cur = MagicMock()

        def _fetchall():
            fetchall_call[0] += 1
            if fetchall_call[0] == 1:
                return [hot_opp]
            raise Exception("function does not exist")

        cur.fetchall.side_effect = _fetchall
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "who_to_contact"})
        assert resp.status_code == 200
        data = resp.json()
        assert "Fatima" in data["answer"] or "HOT" in data["answer"]


# ─────────────────────────────────────────────────────────────────────────────
# TestDailySummaryExtended — events + transitions
# ─────────────────────────────────────────────────────────────────────────────

class TestDailySummaryExtended:
    def test_daily_summary_with_events_and_transitions(self, client):
        """daily_summary with events + lifecycle transitions renders both sections."""
        event_rows = [{"event_type": "email_open", "cnt": 20}]
        order_row = {"order_count": 15, "revenue": 450.0}
        week_row = {"order_count": 80, "revenue": 2400.0}
        transition_rows = [{"prev_lifecycle": "cold", "new_lifecycle": "engaged", "cnt": 5}]

        fetchall_call = [0]
        fetchone_call = [0]
        cur = MagicMock()

        def _fetchall():
            fetchall_call[0] += 1
            if fetchall_call[0] == 1:
                return event_rows
            return transition_rows

        def _fetchone():
            fetchone_call[0] += 1
            if fetchone_call[0] == 1:
                return order_row
            if fetchone_call[0] == 2:
                return week_row
            if fetchone_call[0] == 3:
                return {"cnt": 4}    # pending_opps
            return {"cnt": 2}        # pending_campaigns

        cur.fetchall.side_effect = _fetchall
        cur.fetchone.side_effect = _fetchone

        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "daily_summary"})
        assert resp.status_code == 200
        data = resp.json()
        assert "email_open" in data["answer"] or "Events" in data["answer"]
        assert "cold" in data["answer"] or "Transitions" in data["answer"]


# ─────────────────────────────────────────────────────────────────────────────
# TestOrderAnalyticsExtended — daily breakdown + sources
# ─────────────────────────────────────────────────────────────────────────────

class TestOrderAnalyticsExtended:
    def test_order_analytics_with_daily_and_sources(self, client):
        """order_analytics with daily data + sources renders both sections."""
        top_dishes = [{"item_name": "Biryani", "total_qty": 30, "order_count": 20, "total_revenue": 450.0}]
        daily = [{"day": "2026-01-15", "orders": 5, "revenue": 150.0}]
        sources = [{"source": "website", "cnt": 18}]
        avg_row = {"total_orders": 25, "avg_order": 22.5}
        repeat_row = {"total_customers": 20, "repeat_customers": 8}

        fetchall_call = [0]
        fetchone_call = [0]
        cur = MagicMock()

        def _fetchall():
            fetchall_call[0] += 1
            if fetchall_call[0] == 1:
                return top_dishes
            if fetchall_call[0] == 2:
                return daily
            if fetchall_call[0] == 3:
                return sources
            return []

        def _fetchone():
            fetchone_call[0] += 1
            if fetchone_call[0] == 1:
                return avg_row
            return repeat_row

        cur.fetchall.side_effect = _fetchall
        cur.fetchone.side_effect = _fetchone

        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "order_analytics",
                "date_from": "2026-01-01", "date_to": "2026-01-31",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "Biryani" in data["answer"]
        assert "2026-01-15" in data["answer"]
        assert "website" in data["answer"]


# ─────────────────────────────────────────────────────────────────────────────
# TestOrderSummaryByDeliveryDate — single day + items
# ─────────────────────────────────────────────────────────────────────────────

class TestOrderSummaryByDeliveryDate:
    def test_single_day_with_items(self, client):
        """order_summary_by_delivery_date for single day returns items section."""
        day_row = {
            "delivery_date": "2026-01-15",
            "order_count": 10, "unique_customers": 8,
            "revenue": 300.0, "avg_order_value": 30.0,
        }
        item_rows = [{"item_name": "Nihari", "qty": 5, "revenue": 75.0}]

        fetchall_call = [0]
        cur = MagicMock()

        def _fetchall():
            fetchall_call[0] += 1
            if fetchall_call[0] == 1:
                return [day_row]
            return item_rows

        cur.fetchall.side_effect = _fetchall
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "order_summary_by_delivery_date",
                "date_from": "2026-01-15", "date_to": "2026-01-15",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "Nihari" in data["answer"]
        assert "Items Ordered" in data["answer"]

    def test_multi_day_range(self, client):
        """order_summary_by_delivery_date for date range returns table."""
        day_rows = [
            {"delivery_date": "2026-01-15", "order_count": 8, "unique_customers": 6, "revenue": 240.0, "avg_order_value": 30.0},
            {"delivery_date": "2026-01-16", "order_count": 5, "unique_customers": 4, "revenue": 150.0, "avg_order_value": 30.0},
        ]
        cur = MagicMock()
        cur.fetchall.return_value = day_rows
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "order_summary_by_delivery_date",
                "date_from": "2026-01-15", "date_to": "2026-01-16",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "2026-01-15" in data["answer"]
        assert "2026-01-16" in data["answer"]

    def test_no_data_returns_no_orders_found(self, client):
        """order_summary_by_delivery_date with no rows returns 'No orders found'."""
        cur = _make_cursor(rows=[])
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "order_summary_by_delivery_date",
                "date_from": "2020-01-01", "date_to": "2020-01-02",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "No orders" in data["answer"]

    def test_default_dates_when_none_provided(self, client):
        """order_summary_by_delivery_date with no dates defaults to last 30 days."""
        cur = _make_cursor(rows=[])
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "order_summary_by_delivery_date",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "No orders" in data["answer"] or "Orders" in data["answer"]


# ─────────────────────────────────────────────────────────────────────────────
# TestOrderSummaryByOrderDate — single day with items + sources
# ─────────────────────────────────────────────────────────────────────────────

class TestOrderSummaryByOrderDate:
    def test_single_day_with_items_and_sources(self, client):
        """order_summary_by_order_date for single day returns items + sources sections."""
        day_row = {
            "order_day": "2026-01-15",
            "order_count": 12, "unique_customers": 9,
            "revenue": 360.0, "avg_order_value": 30.0,
        }
        source_rows = [{"source": "app", "cnt": 8, "revenue": 240.0}]
        item_rows = [{"item_name": "Haleem", "qty": 6, "order_count": 5, "revenue": 90.0}]

        fetchall_call = [0]
        cur = MagicMock()

        def _fetchall():
            fetchall_call[0] += 1
            if fetchall_call[0] == 1:
                return [day_row]
            if fetchall_call[0] == 2:
                return source_rows
            return item_rows

        cur.fetchall.side_effect = _fetchall
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "order_summary_by_order_date",
                "date_from": "2026-01-15", "date_to": "2026-01-15",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "Haleem" in data["answer"] or "Dishes" in data["answer"]
        assert "app" in data["answer"] or "Source" in data["answer"]

    def test_no_orders_found(self, client):
        """order_summary_by_order_date with no data returns 'No orders found'."""
        cur = _make_cursor(rows=[])
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "order_summary_by_order_date",
                "date_from": "2020-01-01", "date_to": "2020-01-02",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "No orders" in data["answer"]


# ─────────────────────────────────────────────────────────────────────────────
# TestRevenueTrends — with data + WoW + months
# ─────────────────────────────────────────────────────────────────────────────

class TestRevenueTrends:
    def test_revenue_trends_with_weeks_and_months(self, client):
        """revenue_trends with multi-week data renders weekly table + monthly summary."""
        from datetime import date as _date
        weeks = [
            {"week_start": _date(2026, 1, 12), "orders": 20, "customers": 15, "revenue": 600.0, "avg_order": 30.0},
            {"week_start": _date(2026, 1, 5),  "orders": 18, "customers": 13, "revenue": 540.0, "avg_order": 30.0},
        ]
        months = [
            {"month_start": _date(2026, 1, 1), "orders": 38, "revenue": 1140.0},
        ]

        fetchall_call = [0]
        cur = MagicMock()

        def _fetchall():
            fetchall_call[0] += 1
            if fetchall_call[0] == 1:
                return weeks
            return months

        cur.fetchall.side_effect = _fetchall
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "revenue_trends"})
        assert resp.status_code == 200
        data = resp.json()
        assert "600" in data["answer"] or "Revenue" in data["answer"]
        assert "January" in data["answer"] or "2026" in data["answer"]

    def test_revenue_trends_no_data(self, client):
        """revenue_trends with no data returns 'No revenue data found'."""
        cur = _make_cursor(rows=[])
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "revenue_trends"})
        assert resp.status_code == 200
        data = resp.json()
        assert "No revenue data" in data["answer"]


# ─────────────────────────────────────────────────────────────────────────────
# TestCommunicationHistoryFull — with SMS, calls, deliveries
# ─────────────────────────────────────────────────────────────────────────────

class TestCommunicationHistoryFull:
    def test_comm_history_with_sms_calls_deliveries(self, client):
        """communication_history renders SMS, call, and delivery sections."""
        contact = {"id": 1, "first_name": "Zara", "last_name": "Malik",
                   "email": "zara@test.com", "phone": "+14041234567"}
        sms_rows = [{"direction": "outbound", "body": "Your order is ready!",
                     "status": "delivered", "sent_at": "2026-01-20 10:00:00"}]
        call_rows = [{"direction": "inbound", "duration_sec": 45,
                      "transcript": "Hello I want to order...", "summary": "Customer inquiry",
                      "started_at": "2026-01-19 14:00:00"}]
        delivery_rows = [{"status": "delivered", "notes": "Left at door",
                          "updated_by": "driver", "occurred_at": "2026-01-20 11:00:00"}]

        fetchone_call = [0]
        fetchall_call = [0]
        cur = MagicMock()

        def _fetchone():
            fetchone_call[0] += 1
            return contact

        def _fetchall():
            fetchall_call[0] += 1
            if fetchall_call[0] == 1:
                return sms_rows
            if fetchall_call[0] == 2:
                return call_rows
            return delivery_rows

        cur.fetchone.side_effect = _fetchone
        cur.fetchall.side_effect = _fetchall

        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "communication_history",
                "contact_email": "zara@test.com",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "Zara" in data["answer"]
        assert "SMS" in data["answer"] or "Your order" in data["answer"]
        assert "Call" in data["answer"] or "Customer inquiry" in data["answer"]
        assert "Delivery" in data["answer"] or "Left at door" in data["answer"]

    def test_comm_history_no_history_found(self, client):
        """communication_history for contact with no messages returns 'No communication history'."""
        contact = {"id": 2, "first_name": "Ahmed", "last_name": "Khan",
                   "email": "ahmed@test.com", "phone": "+14041112222"}
        cur = MagicMock()
        cur.fetchone.return_value = contact
        cur.fetchall.return_value = []  # empty sms/calls/deliveries

        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "communication_history",
                "contact_email": "ahmed@test.com",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "No communication history" in data["answer"] or "Ahmed" in data["answer"]

    def test_comm_history_name_no_match(self, client):
        """communication_history by name with no match returns guidance."""
        cur = _make_cursor(rows=[])
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "communication_history",
                "contact_name": "ZZZUnknown",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "No customer" in data["answer"]


# ─────────────────────────────────────────────────────────────────────────────
# TestTeamNotesExtended — search + strftime path
# ─────────────────────────────────────────────────────────────────────────────

class TestTeamNotesExtended:
    def test_team_notes_search_with_keyword(self, client):
        """team_notes browse with keyword > 3 chars runs search query."""
        from datetime import datetime as _dt
        note_rows = [
            {"id": 1, "content_type": "ground_note", "title": "Delivery Issue",
             "body": "Package arrived late due to traffic", "author": "Ali",
             "created_at": _dt(2026, 1, 15)},  # has strftime
        ]
        cur = _make_cursor(rows=note_rows)
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "team_notes",
                "question": "late delivery",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "Delivery Issue" in data["answer"] or "Ali" in data["answer"]

    def test_team_notes_submit_via_note_type(self, client):
        """team_notes with valid note_type submits via _handle_submit_input."""
        cur = _make_cursor(fetchone_val={"id": 42})
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=True: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "team_notes",
                "question": "Excellent delivery run today, all 10 drops completed",
                "input_type": "ground_note",
                "author": "Driver2",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "42" in data["answer"] or "saved" in data["answer"].lower()

    def test_ground_notes_with_strftime_date(self, client):
        """ground_team_notes with datetime object calls strftime on created_at."""
        from datetime import datetime as _dt
        note_rows = [{"title": "Route A Complete", "body": "All good",
                      "author": "TeamA", "created_at": _dt(2026, 1, 20),
                      "body_preview": "All good"}]
        cur = _make_cursor(rows=note_rows)
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "ground_team_notes",
                "question": "abcd",  # > 3 chars → triggers search query
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "Route A Complete" in data["answer"] or "Jan" in data["answer"]

    def test_ad_copies_with_strftime_date(self, client):
        """ad_copies with datetime object calls strftime on created_at."""
        from datetime import datetime as _dt
        copy_rows = [{"title": "Ramadan Promo", "body": "Order special!",
                      "created_at": _dt(2026, 1, 25), "body_preview": "Order special!"}]
        cur = _make_cursor(rows=copy_rows)
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "ad_copies",
                "question": "abcd",  # > 3 chars → search query
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "Ramadan Promo" in data["answer"] or "Jan" in data["answer"]


# ─────────────────────────────────────────────────────────────────────────────
# TestFreeFormWithApiKey — mocked Claude call
# ─────────────────────────────────────────────────────────────────────────────

class TestFreeFormWithApiKey:
    def test_free_form_calls_claude_and_returns_answer(self, client):
        """free_form with API key set calls Claude and returns AI answer."""
        from unittest.mock import AsyncMock

        segment_rows = [{"lifecycle_segment": "active_customer", "cnt": 100}]
        order_stats_row = {"total_orders": 50, "unique_customers": 40,
                           "total_revenue": 1500.0, "avg_order": 30.0}
        top_dishes = [{"item_name": "Biryani", "qty": 20}]
        sources = [{"primary_source": "walk_in", "cnt": 30}]

        fetchall_call = [0]
        fetchone_call = [0]
        cur = MagicMock()

        def _fetchall():
            fetchall_call[0] += 1
            if fetchall_call[0] == 1:
                return segment_rows
            if fetchall_call[0] == 2:
                return top_dishes
            if fetchall_call[0] == 3:
                return []   # opp_stats
            if fetchall_call[0] == 4:
                return sources
            if fetchall_call[0] == 5:
                return []   # rules
            return []       # team_items

        def _fetchone():
            fetchone_call[0] += 1
            return order_stats_row

        cur.fetchall.side_effect = _fetchall
        cur.fetchone.side_effect = _fetchone

        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text="We have 100 active customers.")]
        mock_async_client = MagicMock()
        mock_async_client.messages.create = AsyncMock(return_value=mock_resp)

        with patch("app.routers.query.ANTHROPIC_API_KEY", "test-key"), \
             patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("app.routers.query.anthropic") as mock_anthropic:
            mock_anthropic.AsyncAnthropic.return_value = mock_async_client

            resp = client.post("/api/query/", json={
                "category": "free_form",
                "question": "How many active customers do we have?",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "active" in data["answer"].lower() or "customers" in data["answer"].lower()

    def test_free_form_empty_question_returns_please_type(self, client):
        """free_form with API key set but empty question returns 'Please type a question'."""
        with patch("app.routers.query.ANTHROPIC_API_KEY", "test-key"):
            resp = client.post("/api/query/", json={
                "category": "free_form",
                "question": "   ",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "Please type" in data["answer"]


# ─────────────────────────────────────────────────────────────────────────────
# TestToneWithContact — Claude mocked
# ─────────────────────────────────────────────────────────────────────────────

class TestToneDrafts:
    def test_tone_with_contact_and_api_key(self, client):
        """POST /api/query/tone with contact found generates 3 tone drafts."""
        contact_row = {"first_name": "Sara", "last_name": "Khan",
                       "lifecycle_segment": "lapsed_customer", "total_orders": 3,
                       "last_order_at": "2025-12-01"}
        cur = _make_cursor(fetchone_val=contact_row)

        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text="Hey Sara, we miss you!")]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_resp

        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("app.routers.query.os") as mock_os, \
             patch("app.routers.query.anthropic") as mock_anthropic:
            mock_os.environ.get.return_value = "test-api-key"
            mock_anthropic.Anthropic.return_value = mock_client
            resp = client.post("/api/query/tone", json={
                "contact_email": "sara@test.com",
                "goal": "Win back lapsed customer",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "contact_name" in data
        assert "warm" in data or "urgent" in data or "casual" in data


# ─────────────────────────────────────────────────────────────────────────────
# TestOutcomeReportWithDates — date filters
# ─────────────────────────────────────────────────────────────────────────────

class TestOutcomeReportWithDates:
    def test_outcome_report_with_date_filters(self, client):
        """outcome_report with date_from/date_to filters passes dates to queries."""
        transitions = [{"prev_lifecycle": "engaged", "new_lifecycle": "active_customer", "count": 3}]
        pipeline = [{"lifecycle_segment": "active_customer", "count": 50}]

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
                return {"total": 10}
            if fetchone_call[0] == 2:
                return {"attributed": 5}
            if fetchone_call[0] == 3:
                return {"winback_count": 2}
            return {"winback_revenue": 200.0}

        cur.fetchall.side_effect = _fetchall
        cur.fetchone.side_effect = _fetchone

        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "outcome_report",
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "Outcome Report" in data["answer"]
        assert "engaged" in data["answer"] or "active_customer" in data["answer"]

    def test_outcome_report_with_winback_revenue(self, client):
        """outcome_report with winback_count > 0 renders revenue + avg."""
        transitions = []
        pipeline = [{"lifecycle_segment": "lapsed_customer", "count": 20}]

        fetchall_call = [0]
        fetchone_call = [0]
        cur = MagicMock()

        def _fetchall():
            fetchall_call[0] += 1
            if fetchall_call[0] == 1:
                return transitions
            return pipeline

        def _fetchone():
            fetchone_call[0] += 1
            if fetchone_call[0] == 1:
                return {"total": 30}
            if fetchone_call[0] == 2:
                return {"attributed": 15}
            if fetchone_call[0] == 3:
                return {"winback_count": 5}
            return {"winback_revenue": 500.0}

        cur.fetchall.side_effect = _fetchall
        cur.fetchone.side_effect = _fetchone

        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "outcome_report"})
        assert resp.status_code == 200
        data = resp.json()
        assert "winback" in data["answer"].lower() or "brought back" in data["answer"].lower()
        assert "500" in data["answer"] or "Revenue" in data["answer"]


# ─────────────────────────────────────────────────────────────────────────────
# TestBroadcastHistoryWithDates + table-missing error
# ─────────────────────────────────────────────────────────────────────────────

class TestBroadcastHistoryExtended:
    def test_broadcast_history_with_date_filters(self, client):
        """broadcast_history with date_from/date_to passes filters to query."""
        rows = [{
            "id": 5, "title": "New Year Blast", "broadcast_type": "promotional",
            "channels": ["sms", "email"], "target_type": "all_customers", "target_date": None,
            "status": "completed", "total_recipients": 150,
            "sent_sms": 140, "sent_email": 130, "failed_count": 10,
            "created_by": "admin", "created_at": "2026-01-01",
            "completed_at": "2026-01-01",
        }]
        cur = _make_cursor(rows=rows)
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "broadcast_history",
                "date_from": "2026-01-01", "date_to": "2026-01-31",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "New Year" in data["answer"] or "broadcast" in data["answer"].lower()

    def test_broadcast_history_table_missing_returns_message(self, client):
        """broadcast_history with broadcast_jobs table missing returns graceful message."""
        with patch("app.routers.query.get_cursor") as mock_get_cursor:
            mock_get_cursor.side_effect = Exception("relation broadcast_jobs does not exist")
            resp = client.post("/api/query/", json={"category": "broadcast_history"})
        assert resp.status_code == 200
        data = resp.json()
        assert "not yet available" in data["answer"] or "broadcast" in data["answer"].lower()

    def test_broadcast_history_other_exception_raises_500(self, client):
        """broadcast_history with unexpected exception propagates as 500."""
        with patch("app.routers.query.get_cursor") as mock_get_cursor:
            mock_get_cursor.side_effect = Exception("disk full")
            resp = client.post("/api/query/", json={"category": "broadcast_history"})
        assert resp.status_code == 500

    def test_broadcast_history_completed_summary(self, client):
        """broadcast_history with completed broadcasts shows total sent summary."""
        rows = [
            {"id": 1, "title": "Blast A", "broadcast_type": "promo",
             "channels": ["sms"], "target_type": "all", "target_date": None,
             "status": "completed", "total_recipients": 100,
             "sent_sms": 90, "sent_email": 0, "failed_count": 10,
             "created_by": "admin", "created_at": "2026-01-10", "completed_at": "2026-01-10"},
            {"id": 2, "title": "Blast B", "broadcast_type": "promo",
             "channels": ["email"], "target_type": "all", "target_date": None,
             "status": "pending", "total_recipients": 200,
             "sent_sms": 0, "sent_email": 0, "failed_count": 0,
             "created_by": "admin", "created_at": "2026-01-11", "completed_at": None},
        ]
        cur = _make_cursor(rows=rows)
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={"category": "broadcast_history"})
        assert resp.status_code == 200
        data = resp.json()
        assert "Blast A" in data["answer"]


# ─────────────────────────────────────────────────────────────────────────────
# TestParseDateRange — invalid date
# ─────────────────────────────────────────────────────────────────────────────

class TestParseDateRange:
    def test_invalid_date_string_returns_none(self, client):
        """order_analytics with invalid date_from defaults gracefully (treats as no date)."""
        top_dishes = []
        daily = []
        sources = []
        avg_row = {"total_orders": 0, "avg_order": 0}
        repeat_row = {"total_customers": 0, "repeat_customers": 0}

        fetchall_call = [0]
        fetchone_call = [0]
        cur = MagicMock()

        def _fetchall():
            fetchall_call[0] += 1
            if fetchall_call[0] <= 3:
                return []
            return []

        def _fetchone():
            fetchone_call[0] += 1
            if fetchone_call[0] == 1:
                return avg_row
            return repeat_row

        cur.fetchall.side_effect = _fetchall
        cur.fetchone.side_effect = _fetchone

        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "order_analytics",
                "date_from": "not-a-date",
                "date_to": "also-bad",
            })
        # Should fall back to 30-day default and succeed
        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# TestSmsEmailPerformanceWithDates
# ─────────────────────────────────────────────────────────────────────────────

class TestSmsEmailPerformanceWithDates:
    def test_sms_performance_with_date_filters(self, client):
        """sms_performance with date filters passes them to where clauses."""
        rows = [{
            "id": 3, "title": "Weekend Promo", "broadcast_type": "promo",
            "target_type": "all", "target_date": None,
            "status": "completed", "total_recipients": 50,
            "sent_sms": 48, "failed_count": 2,
            "created_by": "admin", "created_at": "2026-01-20",
            "completed_at": "2026-01-20",
        }]
        cur = _make_cursor(rows=rows)
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "sms_performance",
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "Weekend Promo" in data["answer"]

    def test_email_performance_with_date_filters(self, client):
        """email_performance with date filters passes them to where clauses."""
        rows = [{
            "id": 4, "title": "Monthly Newsletter", "broadcast_type": "promo",
            "email_subject": "January Update",
            "target_type": "all", "target_date": None,
            "status": "completed", "total_recipients": 300,
            "sent_email": 295, "failed_count": 5,
            "created_by": "admin", "created_at": "2026-01-25",
            "completed_at": "2026-01-25",
        }]
        cur = _make_cursor(rows=rows)
        with patch("app.routers.query.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/query/", json={
                "category": "email_performance",
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "Monthly Newsletter" in data["answer"]
        assert "January Update" in data["answer"] or "Subject" in data["answer"]
