"""
Tests for app/routers/orders.py
=================================

Covers:
  Endpoint tests (TestClient with mocked DB)
    - POST /api/shipday/ingest-orders        — empty batch, one order
    - GET  /api/shipday/sync-status          — db stats returned
    - GET  /api/shipday/top-calls            — empty candidates
    - GET  /api/shipday/import-pipeline-status  — pipeline state returned
    - POST /api/shipday/import-all-and-run-agents — starts background task

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
    def test_ingest_empty(self, client):
        """POST ingest-orders with empty list — 200 {received:0, synced:0}."""
        resp = client.post("/api/shipday/ingest-orders", json={"orders": []})

        assert resp.status_code == 200
        data = resp.json()
        assert data["received"] == 0
        assert data["synced"] == 0

    def test_ingest_one_order(self, client):
        """POST ingest-orders with one order — _sync_one_order called, received:1."""
        order = {
            "orderId": "SD-001",
            "customerName": "John Doe",
            "deliveryAddress": "123 Main St, Atlanta GA",
            "orderNumber": "ORD-001",
            "orderItems": [{"name": "Dal", "quantity": 1, "unitPrice": 8.99}],
            "totalOrderCost": 8.99,
            "paymentMethod": "card",
            "orderStatus": "delivered",
        }
        sync_result = {"status": "created", "matched": True, "contact_created": False}

        with patch("app.routers.orders._sync_one_order", return_value=sync_result):
            resp = client.post("/api/shipday/ingest-orders", json={"orders": [order]})

        assert resp.status_code == 200
        data = resp.json()
        assert data["received"] == 1
        assert data["synced"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/shipday/sync-status
# ─────────────────────────────────────────────────────────────────────────────

class TestSyncStatus:
    def test_sync_status(self, client):
        """GET /api/shipday/sync-status — returns sync_mode and db_stats."""
        db_row = {
            "total": 100,
            "matched": 80,
            "completed": 75,
            "oldest_order": None,
            "newest_order": None,
        }
        cur = _make_cursor(fetchone_val=db_row)

        with patch("app.routers.orders.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/shipday/sync-status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["sync_mode"] == "n8n_push"
        assert "db_stats" in data


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/shipday/top-calls
# ─────────────────────────────────────────────────────────────────────────────

class TestTopCalls:
    def test_top_calls_empty(self, client):
        """GET /api/shipday/top-calls with no candidates — 200 {count:0}."""
        cur = _make_cursor(rows=[])

        with patch("app.routers.orders.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/shipday/top-calls")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["candidates"] == []


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/shipday/import-pipeline-status
# POST /api/shipday/import-all-and-run-agents
# ─────────────────────────────────────────────────────────────────────────────

class TestImportPipeline:
    def test_import_pipeline_status(self, client):
        """GET /api/shipday/import-pipeline-status — returns pipeline_state dict."""
        resp = client.get("/api/shipday/import-pipeline-status")

        assert resp.status_code == 200
        data = resp.json()
        assert "pipeline_state" in data

    def test_import_all_starts(self, client, monkeypatch):
        """POST /api/shipday/import-all-and-run-agents — returns status='started'."""
        monkeypatch.setenv("SHIPDAY_API_KEY", "test-key-abc")

        # Ensure pipeline is not already running
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
