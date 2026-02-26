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
