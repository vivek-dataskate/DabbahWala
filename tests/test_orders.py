"""
Tests for app/routers/orders.py
=================================

Covers:
  Endpoint tests (TestClient with mocked DB)
    - POST /api/shipday/ingest-orders              — empty batch, single order, skipped, errors
    - GET  /api/shipday/sync-status                — db stats, db error path
    - GET  /api/shipday/top-calls                  — empty, with results, limit param
    - GET  /api/shipday/import-pipeline-status     — pipeline state returned
    - POST /api/shipday/import-all-and-run-agents  — starts, already running, blocked
    - POST /api/shipday/sync-feedback              — starts background sync, already running

Run with:
    pytest tests/test_orders.py -v
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
# POST /api/shipday/ingest-orders
# ─────────────────────────────────────────────────────────────────────────────

class TestIngestOrders:
    def test_ingest_empty_list(self, client):
        """POST ingest-orders with empty list — 200 {received:0, synced:0, errors:0}."""
        resp = client.post("/api/shipday/ingest-orders", json={"orders": []})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["received"] == 0
        assert data["synced"] == 0
        assert data["errors"] == 0

    def test_ingest_one_created_order(self, client):
        """Single new order is synced — received:1, synced:1, errors:0."""
        order = {
            "orderId": "SD-001",
            "customerName": "John Doe",
            "deliveryAddress": "123 Main St, Atlanta GA",
            "orderNumber": "ORD-001",
            "orderItems": [{"name": "Dal Makhani", "quantity": 1, "unitPrice": 8.99}],
            "totalOrderCost": 8.99,
            "paymentMethod": "card",
            "orderStatus": "DELIVERED",
        }
        sync_result = {"status": "created", "matched": True, "contact_created": False}

        with patch("app.routers.orders._sync_one_order", return_value=sync_result):
            resp = client.post("/api/shipday/ingest-orders", json={"orders": [order]})

        assert resp.status_code == 200
        data = resp.json()
        assert data["received"] == 1
        assert data["synced"] == 1
        assert data["matched"] == 1
        assert data["errors"] == 0

    def test_ingest_skipped_order_not_counted_as_synced(self, client):
        """Order with status='skipped' is not counted in synced."""
        sync_result = {"status": "skipped", "matched": False, "contact_created": False}

        with patch("app.routers.orders._sync_one_order", return_value=sync_result):
            resp = client.post("/api/shipday/ingest-orders", json={
                "orders": [{"orderId": "SD-SKIP"}]
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["received"] == 1
        assert data["synced"] == 0

    def test_ingest_multiple_orders_counts_correctly(self, client):
        """Batch of 3 orders — 2 created, 1 updated — all counted in synced."""
        results = [
            {"status": "created", "matched": True, "contact_created": False},
            {"status": "updated", "matched": True, "contact_created": False},
            {"status": "created", "matched": False, "contact_created": True},
        ]
        side_effects = iter(results)

        with patch("app.routers.orders._sync_one_order",
                   side_effect=lambda o: next(side_effects)):
            resp = client.post("/api/shipday/ingest-orders", json={
                "orders": [
                    {"orderId": "SD-001"},
                    {"orderId": "SD-002"},
                    {"orderId": "SD-003"},
                ]
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["received"] == 3
        assert data["synced"] == 3
        assert data["errors"] == 0

    def test_ingest_order_error_increments_errors(self, client):
        """When _sync_one_order raises, errors is incremented and last_error is set."""
        with patch("app.routers.orders._sync_one_order",
                   side_effect=Exception("DB stored proc failed")):
            resp = client.post("/api/shipday/ingest-orders", json={
                "orders": [{"orderId": "SD-ERR"}]
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["received"] == 1
        assert data["errors"] == 1
        assert data["last_error"] is not None
        assert "SD-ERR" in data["last_error"]

    def test_ingest_returns_ok_status_field(self, client):
        """Response always includes status='ok'."""
        resp = client.post("/api/shipday/ingest-orders", json={"orders": []})

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/shipday/sync-status
# ─────────────────────────────────────────────────────────────────────────────

class TestSyncStatus:
    def test_sync_status_returns_db_stats(self, client):
        """GET /api/shipday/sync-status returns sync_mode and db_stats."""
        db_row = {
            "total": 100,
            "matched": 80,
            "completed": 75,
            "oldest_order": None,
            "newest_order": None,
        }
        cur = _make_cursor(fetchone_val=db_row)

        with patch("app.routers.orders.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/shipday/sync-status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["sync_mode"] == "n8n_push"
        assert "db_stats" in data
        assert data["db_stats"]["total"] == 100
        assert data["db_stats"]["matched"] == 80

    def test_sync_status_db_error_returns_error_in_db_stats(self, client):
        """DB exception populates db_stats.error (does not raise 500)."""
        with patch("app.routers.orders.get_cursor",
                   side_effect=Exception("table does not exist")):
            resp = client.get("/api/shipday/sync-status")

        assert resp.status_code == 200
        data = resp.json()
        assert "db_stats" in data
        assert "error" in data["db_stats"]

    def test_sync_status_includes_note_field(self, client):
        """Response includes a note field explaining the n8n push architecture."""
        cur = _make_cursor(fetchone_val={"total": 0, "matched": 0, "completed": 0,
                                         "oldest_order": None, "newest_order": None})

        with patch("app.routers.orders.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/shipday/sync-status")

        assert resp.status_code == 200
        assert "note" in resp.json()


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/shipday/top-calls
# ─────────────────────────────────────────────────────────────────────────────

class TestTopCalls:
    def test_top_calls_returns_empty_when_no_candidates(self, client):
        """Both stored proc and fallback return empty — count:0, candidates:[]."""
        cur = _make_cursor(rows=[])

        with patch("app.routers.orders.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/shipday/top-calls")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["candidates"] == []

    def test_top_calls_returns_candidates_from_stored_proc(self, client):
        """Stored proc returns rows — they appear in candidates list."""
        candidates = [
            {
                "rank": 1,
                "contact_id": 10,
                "full_name": "Ahmed Khan",
                "phone": "+971501234567",
                "email": "ahmed@example.com",
                "lifecycle_segment": "loyal",
                "total_orders": 12,
                "last_order_at": None,
                "days_since_last_order": 21,
                "opens_7d": 3,
                "opens_30d": 8,
                "orders_90d": 4,
                "urgency_score": 0.78,
                "call_reason": "High engagement, ripe for reorder",
                "suggested_script": "Hi Ahmed! We miss you at DabbahWala...",
            }
        ]
        cur = _make_cursor(rows=candidates)

        with patch("app.routers.orders.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/shipday/top-calls")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["candidates"][0]["contact_id"] == 10
        assert data["candidates"][0]["urgency_score"] == 0.78

    def test_top_calls_respects_limit_param(self, client):
        """limit query param is passed to get_top_reorder_candidates."""
        cur = _make_cursor(rows=[])

        with patch("app.routers.orders.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/shipday/top-calls?limit=5")

        assert resp.status_code == 200
        # Confirm limit was forwarded to the stored proc
        call_args = cur.execute.call_args[0][1]
        assert 5 in call_args

    def test_top_calls_response_includes_metadata_fields(self, client):
        """Response includes generated_at and model_notes fields."""
        cur = _make_cursor(rows=[])

        with patch("app.routers.orders.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/shipday/top-calls")

        assert resp.status_code == 200
        data = resp.json()
        assert "generated_at" in data
        assert "model_notes" in data


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/shipday/import-pipeline-status
# ─────────────────────────────────────────────────────────────────────────────

class TestImportPipelineStatus:
    def test_returns_pipeline_state_dict(self, client):
        """GET /api/shipday/import-pipeline-status returns pipeline_state dict."""
        resp = client.get("/api/shipday/import-pipeline-status")

        assert resp.status_code == 200
        data = resp.json()
        assert "pipeline_state" in data
        state = data["pipeline_state"]
        # Verify standard state keys are present
        assert "running" in state
        assert "phase" in state
        assert "orders_synced" in state

    def test_returns_running_false_when_idle(self, client):
        """When pipeline is idle, running=False."""
        import app.routers.orders as orders_mod
        orders_mod._pipeline_state["running"] = False

        resp = client.get("/api/shipday/import-pipeline-status")

        assert resp.status_code == 200
        assert resp.json()["pipeline_state"]["running"] is False


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/shipday/import-all-and-run-agents
# ─────────────────────────────────────────────────────────────────────────────

class TestImportAllAndRunAgents:
    def test_import_all_starts_background_task(self, client, monkeypatch):
        """POST import-all-and-run-agents starts background pipeline — status='started'."""
        monkeypatch.setenv("SHIPDAY_API_KEY", "test-key-abc")

        import app.routers.orders as orders_mod
        orders_mod._pipeline_state["running"] = False
        orders_mod._sync_state["running"] = False

        with patch("app.routers.orders._run_import_pipeline"):
            resp = client.post(
                "/api/shipday/import-all-and-run-agents",
                json={"days_back": 7, "max_pages": 10},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert data["days_back"] == 7

    def test_import_all_already_running_returns_already_running(self, client, monkeypatch):
        """If pipeline is already running, returns status='already_running'."""
        monkeypatch.setenv("SHIPDAY_API_KEY", "test-key-abc")

        import app.routers.orders as orders_mod
        orders_mod._pipeline_state["running"] = True

        try:
            resp = client.post(
                "/api/shipday/import-all-and-run-agents",
                json={"days_back": 7, "max_pages": 10},
            )

            assert resp.status_code == 200
            assert resp.json()["status"] == "already_running"
        finally:
            orders_mod._pipeline_state["running"] = False

    def test_import_all_blocked_when_sync_running(self, client, monkeypatch):
        """If historical sync is running, pipeline start is blocked."""
        monkeypatch.setenv("SHIPDAY_API_KEY", "test-key-abc")

        import app.routers.orders as orders_mod
        orders_mod._pipeline_state["running"] = False
        orders_mod._sync_state["running"] = True

        try:
            resp = client.post(
                "/api/shipday/import-all-and-run-agents",
                json={"days_back": 7, "max_pages": 10},
            )

            assert resp.status_code == 200
            assert resp.json()["status"] == "blocked"
        finally:
            orders_mod._sync_state["running"] = False


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/shipday/sync-feedback
# ─────────────────────────────────────────────────────────────────────────────

class TestSyncFeedback:
    def test_starts_background_sync_by_default(self, client):
        """Default run_in_background=True returns status='started' immediately."""
        import app.routers.orders as orders_mod
        orders_mod._feedback_sync_state["running"] = False

        with patch("app.routers.orders._run_feedback_sync"):
            resp = client.post("/api/shipday/sync-feedback", json={"days_back": 3})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert data["days_back"] == 3

    def test_returns_already_running_when_sync_in_progress(self, client):
        """While a sync is running, subsequent calls return 'already_running'."""
        import app.routers.orders as orders_mod
        original = orders_mod._feedback_sync_state.copy()
        orders_mod._feedback_sync_state["running"] = True

        try:
            resp = client.post("/api/shipday/sync-feedback", json={"days_back": 3})

            assert resp.status_code == 200
            assert resp.json()["status"] == "already_running"
        finally:
            orders_mod._feedback_sync_state.update(original)
            orders_mod._feedback_sync_state["running"] = False

    def test_sync_feedback_passes_days_back_param(self, client):
        """days_back is echoed back in the started response."""
        import app.routers.orders as orders_mod
        orders_mod._feedback_sync_state["running"] = False

        with patch("app.routers.orders._run_feedback_sync"):
            resp = client.post("/api/shipday/sync-feedback", json={"days_back": 14})

        assert resp.status_code == 200
        data = resp.json()
        assert data["days_back"] == 14

    def test_sync_feedback_all_historical_flag(self, client):
        """all_historical=True is echoed back in the response."""
        import app.routers.orders as orders_mod
        orders_mod._feedback_sync_state["running"] = False

        with patch("app.routers.orders._run_feedback_sync"):
            resp = client.post("/api/shipday/sync-feedback", json={
                "days_back": 30,
                "all_historical": True,
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["all_historical"] is True
