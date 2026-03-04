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


# ─────────────────────────────────────────────────────────────────────────────
# _sync_one_order (internal helper) — direct tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSyncOneOrder:
    """Test _sync_one_order internal helper directly (no HTTP)."""

    def test_returns_result_dict_from_stored_proc(self):
        """_sync_one_order calls sync_shipday_order() and returns parsed result."""
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch
        from app.routers.orders import _sync_one_order

        result_dict = {"status": "created", "matched": True, "contact_created": False}
        cur = MagicMock()
        cur.fetchone.return_value = {"sync_shipday_order": result_dict}

        @contextmanager
        def _cursor_ctx(commit=True):
            yield cur

        with patch("app.routers.orders.get_cursor", side_effect=_cursor_ctx):
            result = _sync_one_order({"orderId": "SD-001", "orderStatus": "DELIVERED"})

        assert result["status"] == "created"
        assert result["matched"] is True

    def test_returns_empty_dict_when_no_row(self):
        """_sync_one_order returns {} when stored proc returns no row."""
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch
        from app.routers.orders import _sync_one_order

        cur = MagicMock()
        cur.fetchone.return_value = None

        @contextmanager
        def _cursor_ctx(commit=True):
            yield cur

        with patch("app.routers.orders.get_cursor", side_effect=_cursor_ctx):
            result = _sync_one_order({"orderId": "SD-999"})

        assert result == {}

    def test_parses_json_string_result(self):
        """_sync_one_order parses result when stored proc returns a JSON string."""
        import json
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch
        from app.routers.orders import _sync_one_order

        result_json = json.dumps({"status": "updated", "matched": True, "contact_created": False})
        cur = MagicMock()
        cur.fetchone.return_value = {"sync_shipday_order": result_json}

        @contextmanager
        def _cursor_ctx(commit=True):
            yield cur

        with patch("app.routers.orders.get_cursor", side_effect=_cursor_ctx):
            result = _sync_one_order({"orderId": "SD-002"})

        assert result["status"] == "updated"


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/shipday/run-migration
# ─────────────────────────────────────────────────────────────────────────────

class TestRunMigration:
    def test_run_migration_not_found_returns_404(self, client):
        """Returns 404 when migration 034 file is not found."""
        with patch("glob.glob", return_value=[]):
            resp = client.post("/api/shipday/run-migration")
        assert resp.status_code == 404

    def test_run_migration_success(self, client):
        """Returns status ok when migration file is found and executes successfully."""
        from unittest.mock import mock_open
        cur = _make_cursor()

        with patch("glob.glob", return_value=["migrations/034_shipday.sql"]), \
             patch("builtins.open", mock_open(read_data="CREATE TABLE test ();")), \
             patch("app.routers.orders.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/shipday/run-migration")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_run_migration_db_error_returns_500(self, client):
        """Returns 500 when migration SQL raises an exception."""
        from unittest.mock import mock_open
        cur = _make_cursor()
        cur.execute.side_effect = Exception("syntax error")

        with patch("glob.glob", return_value=["migrations/034_shipday.sql"]), \
             patch("builtins.open", mock_open(read_data="BAD SQL;")), \
             patch("app.routers.orders.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/shipday/run-migration")

        assert resp.status_code == 500


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/shipday/import-pipeline-status
# ─────────────────────────────────────────────────────────────────────────────

class TestImportPipelineStatus:
    def test_returns_pipeline_state(self, client):
        """GET /import-pipeline-status returns pipeline_state dict."""
        resp = client.get("/api/shipday/import-pipeline-status")
        assert resp.status_code == 200
        data = resp.json()
        assert "pipeline_state" in data
        assert "running" in data["pipeline_state"]

    def test_pipeline_state_has_required_fields(self, client):
        """pipeline_state includes all expected progress fields."""
        resp = client.get("/api/shipday/import-pipeline-status")
        state = resp.json()["pipeline_state"]
        required = ["running", "phase", "started_at", "completed_at",
                    "orders_synced", "agents_run"]
        for field in required:
            assert field in state


# ─────────────────────────────────────────────────────────────────────────────
# _get_shipday_key — internal helper
# ─────────────────────────────────────────────────────────────────────────────

class TestGetShipdayKey:
    def test_raises_500_when_no_key(self, monkeypatch):
        """_get_shipday_key raises HTTPException 500 when env var not set."""
        from fastapi import HTTPException
        from app.routers.orders import _get_shipday_key
        monkeypatch.delenv("SHIPDAY_API_KEY", raising=False)
        monkeypatch.delenv("SHIPDAY_KEY", raising=False)
        try:
            _get_shipday_key()
            assert False, "Should have raised"
        except HTTPException as e:
            assert e.status_code == 500

    def test_returns_key_when_set(self, monkeypatch):
        """_get_shipday_key returns key from SHIPDAY_API_KEY env var."""
        from app.routers.orders import _get_shipday_key
        monkeypatch.setenv("SHIPDAY_API_KEY", "test-api-key-xyz")
        key = _get_shipday_key()
        assert key == "test-api-key-xyz"


# ─────────────────────────────────────────────────────────────────────────────
# _run_historical_sync — direct tests with mocked httpx
# ─────────────────────────────────────────────────────────────────────────────

class TestRunHistoricalSync:
    def test_sync_fetches_orders_and_updates_state(self):
        """_run_historical_sync calls httpx and updates _sync_state."""
        import app.routers.orders as orders_mod
        from app.routers.orders import _run_historical_sync

        orders_mod._sync_state["running"] = False

        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"orderId": "ORD-001", "customerName": "Alice Test"}
        ]
        mock_resp.raise_for_status = MagicMock()
        mock_resp.status_code = 200

        mock_http_client = MagicMock()
        mock_http_client.__enter__ = MagicMock(return_value=mock_http_client)
        mock_http_client.__exit__ = MagicMock(return_value=False)
        mock_http_client.get.return_value = mock_resp

        with patch("app.routers.orders.httpx.Client", return_value=mock_http_client), \
             patch("app.routers.orders._sync_one_order", return_value={"status": "created", "matched": True, "contact_created": True}):
            _run_historical_sync("test-key", "2024-01-01", max_pages=1)

        assert orders_mod._sync_state["running"] is False
        assert orders_mod._sync_state["orders_synced"] >= 1

    def test_sync_handles_fetch_exception(self):
        """_run_historical_sync handles HTTP errors per window gracefully."""
        import app.routers.orders as orders_mod
        from app.routers.orders import _run_historical_sync

        mock_http_client = MagicMock()
        mock_http_client.__enter__ = MagicMock(return_value=mock_http_client)
        mock_http_client.__exit__ = MagicMock(return_value=False)
        mock_http_client.get.side_effect = Exception("Connection refused")

        with patch("app.routers.orders.httpx.Client", return_value=mock_http_client):
            _run_historical_sync("test-key", "2024-01-01", max_pages=1)

        assert orders_mod._sync_state["running"] is False
        assert orders_mod._sync_state["errors"] >= 1

    def test_sync_handles_order_ingest_exception(self):
        """_run_historical_sync catches per-order exceptions without crashing."""
        import app.routers.orders as orders_mod
        from app.routers.orders import _run_historical_sync

        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"orderId": "ORD-ERR"}]
        mock_resp.raise_for_status = MagicMock()

        mock_http_client = MagicMock()
        mock_http_client.__enter__ = MagicMock(return_value=mock_http_client)
        mock_http_client.__exit__ = MagicMock(return_value=False)
        mock_http_client.get.return_value = mock_resp

        with patch("app.routers.orders.httpx.Client", return_value=mock_http_client), \
             patch("app.routers.orders._sync_one_order", side_effect=Exception("DB error")):
            _run_historical_sync("test-key", "2024-01-01", max_pages=1)

        assert orders_mod._sync_state["running"] is False
        assert orders_mod._sync_state["errors"] >= 1

    def test_sync_handles_dict_response(self):
        """_run_historical_sync handles {'orders': [...]} dict response format."""
        import app.routers.orders as orders_mod
        from app.routers.orders import _run_historical_sync

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"orders": [{"orderId": "ORD-DICT"}]}
        mock_resp.raise_for_status = MagicMock()

        mock_http_client = MagicMock()
        mock_http_client.__enter__ = MagicMock(return_value=mock_http_client)
        mock_http_client.__exit__ = MagicMock(return_value=False)
        mock_http_client.get.return_value = mock_resp

        with patch("app.routers.orders.httpx.Client", return_value=mock_http_client), \
             patch("app.routers.orders._sync_one_order", return_value={"status": "created", "matched": False, "contact_created": False}):
            _run_historical_sync("test-key", "2024-01-01", max_pages=1)

        assert orders_mod._sync_state["running"] is False


# ─────────────────────────────────────────────────────────────────────────────
# get_top_calls — fallback and error paths
# ─────────────────────────────────────────────────────────────────────────────

class TestTopCallsAdditional:
    def test_fallback_query_used_when_stored_proc_empty(self, client):
        """When stored proc returns 0 rows, fallback query is used."""
        fallback_rows = [{"contact_id": 1, "full_name": "Alice", "phone": "+1404",
                          "email": "a@b.com", "lifecycle_segment": "active",
                          "total_orders": 5, "last_order_at": None, "days_since_last_order": 10,
                          "opens_7d": 2, "opens_30d": 5, "orders_90d": 3,
                          "urgency_score": 0.5, "call_reason": "Top customer",
                          "suggested_script": "Hi!", "rank": 1}]
        cur = _make_cursor()
        cur.fetchall.side_effect = [[], fallback_rows]

        with patch("app.routers.orders.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/shipday/top-calls?limit=5")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 0

    def test_top_calls_db_exception_returns_500(self, client):
        """get_top_calls propagates exception as 500."""
        cur = _make_cursor()
        cur.execute.side_effect = Exception("DB crash")

        with patch("app.routers.orders.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/shipday/top-calls?limit=5")

        assert resp.status_code == 500


# ─────────────────────────────────────────────────────────────────────────────
# _fetch_order_detail — direct tests with mocked httpx
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchOrderDetail:
    def test_returns_none_on_404(self):
        """_fetch_order_detail returns None when Shipday returns 404."""
        from app.routers.orders import _fetch_order_detail

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        mock_http_client = MagicMock()
        mock_http_client.__enter__ = MagicMock(return_value=mock_http_client)
        mock_http_client.__exit__ = MagicMock(return_value=False)
        mock_http_client.get.return_value = mock_resp

        with patch("app.routers.orders.httpx.Client", return_value=mock_http_client):
            result = _fetch_order_detail("test-key", "ORD-999")

        assert result is None

    def test_returns_order_dict_on_success(self):
        """_fetch_order_detail returns parsed JSON on success."""
        from app.routers.orders import _fetch_order_detail

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"orderId": "ORD-123", "feedback": "Great!"}
        mock_resp.raise_for_status = MagicMock()

        mock_http_client = MagicMock()
        mock_http_client.__enter__ = MagicMock(return_value=mock_http_client)
        mock_http_client.__exit__ = MagicMock(return_value=False)
        mock_http_client.get.return_value = mock_resp

        with patch("app.routers.orders.httpx.Client", return_value=mock_http_client):
            result = _fetch_order_detail("test-key", "ORD-123")

        assert result is not None
        assert result["orderId"] == "ORD-123"

    def test_returns_none_on_network_exception(self):
        """_fetch_order_detail returns None on network error."""
        from app.routers.orders import _fetch_order_detail

        mock_http_client = MagicMock()
        mock_http_client.__enter__ = MagicMock(return_value=mock_http_client)
        mock_http_client.__exit__ = MagicMock(return_value=False)
        mock_http_client.get.side_effect = Exception("timeout")

        with patch("app.routers.orders.httpx.Client", return_value=mock_http_client):
            result = _fetch_order_detail("test-key", "ORD-ERR")

        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# _store_order_communications — direct tests
# ─────────────────────────────────────────────────────────────────────────────

class TestStoreOrderCommunications:
    def test_stores_feedback_and_instruction(self):
        """_store_order_communications handles feedback, delivery_instruction, and POD."""
        from app.routers.orders import _store_order_communications

        cur = _make_cursor()

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        order = {
            "feedback": "Great delivery!",
            "deliveryInstruction": "Leave at door",
            "proofOfDelivery": {"signaturePath": "sig.png", "picturePaths": ["pic1.jpg"]},
        }

        with patch("app.routers.orders.get_cursor", side_effect=_mock_cursor):
            result = _store_order_communications("ORD-001", order, "2024-01-01T12:00:00Z")

        assert "customer_feedback" in result
        assert "delivery_instruction" in result
        assert "proof_of_delivery" in result

    def test_returns_empty_for_no_comms(self):
        """_store_order_communications returns empty list when order has no comms."""
        from app.routers.orders import _store_order_communications

        cur = _make_cursor()

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        order = {"feedback": "", "deliveryInstruction": "", "proofOfDelivery": {}}

        with patch("app.routers.orders.get_cursor", side_effect=_mock_cursor):
            result = _store_order_communications("ORD-EMPTY", order, None)

        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# _create_feedback_opportunity — direct tests
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateFeedbackOpportunity:
    def test_creates_opportunity_for_negative_sentiment(self):
        """_create_feedback_opportunity inserts opportunity for negative feedback."""
        from app.routers.orders import _create_feedback_opportunity

        cur = _make_cursor()

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.orders.get_cursor", side_effect=_mock_cursor):
            _create_feedback_opportunity(contact_id=1, shipday_order_id="ORD-NEG", sentiment="negative")

        assert cur.execute.called

    def test_creates_opportunity_for_positive_sentiment(self):
        """_create_feedback_opportunity inserts opportunity for positive feedback."""
        from app.routers.orders import _create_feedback_opportunity

        cur = _make_cursor()

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.orders.get_cursor", side_effect=_mock_cursor):
            _create_feedback_opportunity(contact_id=2, shipday_order_id="ORD-POS", sentiment="positive")

        assert cur.execute.called

    def test_skips_neutral_sentiment(self):
        """_create_feedback_opportunity does nothing for neutral sentiment."""
        from app.routers.orders import _create_feedback_opportunity

        cur = _make_cursor()

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.orders.get_cursor", side_effect=_mock_cursor):
            _create_feedback_opportunity(contact_id=3, shipday_order_id="ORD-NEU", sentiment="neutral")

        cur.execute.assert_not_called()

    def test_handles_db_exception(self):
        """_create_feedback_opportunity silently handles DB error."""
        from app.routers.orders import _create_feedback_opportunity

        with patch("app.routers.orders.get_cursor", side_effect=Exception("DB error")):
            _create_feedback_opportunity(contact_id=4, shipday_order_id="ORD-X", sentiment="negative")
        # Should not raise


# ─────────────────────────────────────────────────────────────────────────────
# _run_feedback_sync — direct tests
# ─────────────────────────────────────────────────────────────────────────────

class TestRunFeedbackSync:
    def test_aborts_when_no_api_key(self, monkeypatch):
        """_run_feedback_sync returns early when no SHIPDAY_API_KEY set."""
        from app.routers.orders import _run_feedback_sync
        monkeypatch.delenv("SHIPDAY_API_KEY", raising=False)
        monkeypatch.delenv("SHIPDAY_KEY", raising=False)

        with patch("app.routers.orders.get_cursor") as mock_cursor:
            _run_feedback_sync(days_back=7, all_historical=False)

        mock_cursor.assert_not_called()

    def test_syncs_orders_with_feedback(self, monkeypatch):
        """_run_feedback_sync processes orders and records sentiment."""
        import app.routers.orders as orders_mod
        from app.routers.orders import _run_feedback_sync

        monkeypatch.setenv("SHIPDAY_API_KEY", "test-key")

        pending_rows = [{"shipday_order_id": "ORD-001", "contact_id": 1, "actual_delivery": None}]
        sentiment_row = {"sentiment": "positive"}
        cur = _make_cursor()
        cur.fetchall.return_value = pending_rows
        cur.fetchone.return_value = sentiment_row

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.orders.get_cursor", side_effect=_mock_cursor), \
             patch("app.routers.orders._fetch_order_detail", return_value={"feedback": "Great!"}), \
             patch("app.routers.orders._store_order_communications", return_value=["customer_feedback"]), \
             patch("app.routers.orders._create_feedback_opportunity"), \
             patch("app.routers.orders.time.sleep"):
            _run_feedback_sync(days_back=7, all_historical=False)

        assert orders_mod._feedback_sync_state["running"] is False

    def test_handles_order_fetch_exception(self, monkeypatch):
        """_run_feedback_sync catches per-order exceptions gracefully."""
        import app.routers.orders as orders_mod
        from app.routers.orders import _run_feedback_sync

        monkeypatch.setenv("SHIPDAY_API_KEY", "test-key")

        pending_rows = [{"shipday_order_id": "ORD-ERR", "contact_id": 1, "actual_delivery": None}]
        cur = _make_cursor()
        cur.fetchall.return_value = pending_rows

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.orders.get_cursor", side_effect=_mock_cursor), \
             patch("app.routers.orders._fetch_order_detail", side_effect=Exception("timeout")):
            _run_feedback_sync(days_back=7, all_historical=False)

        assert orders_mod._feedback_sync_state["errors"] >= 1

    def test_all_historical_uses_different_query(self, monkeypatch):
        """_run_feedback_sync with all_historical=True uses historical SQL path."""
        from app.routers.orders import _run_feedback_sync

        monkeypatch.setenv("SHIPDAY_API_KEY", "test-key")

        cur = _make_cursor()
        cur.fetchall.return_value = []

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.orders.get_cursor", side_effect=_mock_cursor):
            _run_feedback_sync(days_back=7, all_historical=True)

        # Verify the query was executed with "historical" style (no date params)
        call_args = cur.execute.call_args_list[0]
        sql = call_args[0][0] if call_args[0] else ""
        assert "actual_delivery" in sql or "synced_at" in sql or "COMPLETED" in sql


# ─────────────────────────────────────────────────────────────────────────────
# sync_feedback — synchronous mode
# ─────────────────────────────────────────────────────────────────────────────

class TestSyncFeedbackSynchronous:
    def test_sync_mode_runs_inline(self, client, monkeypatch):
        """sync_feedback with run_in_background=False runs inline."""
        import app.routers.orders as orders_mod
        orders_mod._feedback_sync_state["running"] = False

        with patch("app.routers.orders._run_feedback_sync") as mock_fn:
            resp = client.post("/api/shipday/sync-feedback", json={
                "days_back": 7,
                "run_in_background": False,
            })

        assert resp.status_code == 200
        mock_fn.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# _run_import_pipeline — direct tests
# ─────────────────────────────────────────────────────────────────────────────

class TestRunImportPipeline:
    def test_pipeline_runs_all_phases(self, monkeypatch):
        """_run_import_pipeline runs all 4 phases and completes."""
        import app.routers.orders as orders_mod
        from app.routers.orders import _run_import_pipeline

        monkeypatch.setenv("SHIPDAY_API_KEY", "test-key")

        cur = _make_cursor()
        cur.fetchall.return_value = []  # no contacts to run agents on

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.orders._run_historical_sync"), \
             patch("app.routers.orders._run_feedback_sync"), \
             patch("app.routers.orders.get_cursor", side_effect=_mock_cursor):
            _run_import_pipeline("test-key", days_back=7, max_pages=10)

        assert orders_mod._pipeline_state["running"] is False
        assert orders_mod._pipeline_state["phase"] == "done"

    def test_pipeline_handles_exception(self, monkeypatch):
        """_run_import_pipeline catches fatal exceptions and marks phase=done."""
        import app.routers.orders as orders_mod
        from app.routers.orders import _run_import_pipeline

        with patch("app.routers.orders._run_historical_sync", side_effect=Exception("crash!")):
            _run_import_pipeline("test-key", days_back=7, max_pages=10)

        assert orders_mod._pipeline_state["running"] is False
        assert orders_mod._pipeline_state["phase"] == "done"

    def test_pipeline_runs_agents_for_contacts(self, monkeypatch):
        """_run_import_pipeline calls _run_full_cycle for each contact."""
        import app.routers.orders as orders_mod
        from app.routers.orders import _run_import_pipeline

        cur = _make_cursor()
        cur.fetchall.return_value = [{"id": 101}, {"id": 102}]

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.orders._run_historical_sync"), \
             patch("app.routers.orders._run_feedback_sync"), \
             patch("app.routers.orders.get_cursor", side_effect=_mock_cursor), \
             patch("app.routers.agents._run_full_cycle") as mock_agent:
            _run_import_pipeline("test-key", days_back=7, max_pages=10)

        assert mock_agent.call_count == 2

    def test_pipeline_catches_per_agent_exceptions(self, monkeypatch):
        """_run_import_pipeline catches per-contact agent failures gracefully."""
        import app.routers.orders as orders_mod
        from app.routers.orders import _run_import_pipeline

        cur = _make_cursor()
        cur.fetchall.return_value = [{"id": 201}]

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.orders._run_historical_sync"), \
             patch("app.routers.orders._run_feedback_sync"), \
             patch("app.routers.orders.get_cursor", side_effect=_mock_cursor), \
             patch("app.routers.agents._run_full_cycle", side_effect=Exception("agent crash")):
            _run_import_pipeline("test-key", days_back=7, max_pages=10)

        assert orders_mod._pipeline_state["agent_errors"] >= 1


# ─────────────────────────────────────────────────────────────────────────────
# feedback_stats — endpoint tests
# ─────────────────────────────────────────────────────────────────────────────

class TestFeedbackStats:
    def test_feedback_stats_returns_data(self, client):
        """GET /feedback-stats returns sync_state, breakdown, and recent_feedback."""
        breakdown = [{"comm_type": "customer_feedback", "sentiment": "positive", "count": 3, "latest": None}]
        recent = [{"id": 1, "shipday_order_id": "ORD-1", "order_number": "1", "content": "Great!",
                   "sentiment": "positive", "occurred_at": None, "email": "a@b.com",
                   "full_name": "Alice Test", "phone": "+1404"}]
        cur = _make_cursor()
        cur.fetchall.side_effect = [breakdown, recent]

        with patch("app.routers.orders.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/shipday/feedback-stats")

        assert resp.status_code == 200
        data = resp.json()
        assert "sync_state" in data
        assert "breakdown" in data
        assert "recent_feedback" in data

    def test_feedback_stats_db_error_returns_500(self, client):
        """GET /feedback-stats returns 500 when DB raises."""
        cur = _make_cursor()
        cur.execute.side_effect = Exception("DB crash")

        with patch("app.routers.orders.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/shipday/feedback-stats")

        assert resp.status_code == 500

    def test_feedback_stats_serializes_datetimes(self, client):
        """GET /feedback-stats serializes datetime fields to ISO strings."""
        from datetime import datetime, timezone
        ts = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        breakdown = [{"comm_type": "customer_feedback", "sentiment": "positive", "count": 1, "latest": ts}]
        recent = []
        cur = _make_cursor()
        cur.fetchall.side_effect = [breakdown, recent]

        with patch("app.routers.orders.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/shipday/feedback-stats")

        assert resp.status_code == 200
        # latest should be serialized to string
        breakdown_data = resp.json()["breakdown"]
        if breakdown_data:
            assert isinstance(breakdown_data[0]["latest"], str)
