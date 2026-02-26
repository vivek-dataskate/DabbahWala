"""
Tests for app/routers/menu.py
==============================

Covers:
  Endpoint tests (TestClient with mocked DB)
    - GET  /api/menu/items          — active items list, empty list
    - GET  /api/menu/items/inactive — inactive items
    - GET  /api/menu/items/{id}/history — item history, 404 when item not found
    - POST /api/menu/sync           — sync from Airtable (mocked httpx)

Run with:
    pytest tests/test_menu.py -v
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
# GET /api/menu/items
# ─────────────────────────────────────────────────────────────────────────────

class TestMenuItems:
    def test_get_active_items(self, client):
        """GET /api/menu/items — returns active items with expected fields."""
        rows = [
            {
                "id": 1,
                "item_name": "Dal Tadka",
                "category": "Lentils",
                "is_veg": True,
                "price": 8.99,
                "active": True,
                "added_date": None,
                "description": None,
                "image_url": None,
                "discarded_date": None,
                "airtable_record_id": None,
                "updated_at": None,
                "created_at": None,
            }
        ]
        cur = _make_cursor(rows=rows)

        with patch("app.routers.menu.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/menu/items")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["items"][0]["item_name"] == "Dal Tadka"

    def test_get_active_empty(self, client):
        """GET /api/menu/items with no items — 200 empty list."""
        cur = _make_cursor(rows=[])

        with patch("app.routers.menu.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/menu/items")

        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["count"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/menu/items/inactive
# ─────────────────────────────────────────────────────────────────────────────

class TestInactiveItems:
    def test_get_inactive(self, client):
        """GET /api/menu/items/inactive — returns inactive items."""
        rows = [
            {
                "id": 2,
                "item_name": "OldItem",
                "category": "Snacks",
                "is_veg": True,
                "price": 4.99,
                "added_date": None,
                "discarded_date": None,
                "airtable_record_id": None,
                "updated_at": None,
            }
        ]
        cur = _make_cursor(rows=rows)

        with patch("app.routers.menu.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/menu/items/inactive")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["items"][0]["item_name"] == "OldItem"


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/menu/items/{item_id}/history
# ─────────────────────────────────────────────────────────────────────────────

class TestItemHistory:
    def test_get_history(self, client):
        """GET /api/menu/items/1/history — item exists, returns history rows."""
        history_rows = [
            {
                "id": 1,
                "change_type": "added",
                "field_changed": None,
                "old_value": None,
                "new_value": "Dal Tadka",
                "changed_at": None,
                "source": "airtable_sync",
            }
        ]
        # fetchone for existence check, then fetchall for history
        call_count = [0]
        cur = MagicMock()

        def _fetchone():
            call_count[0] += 1
            if call_count[0] == 1:
                return {"id": 1}  # item exists
            return None

        cur.fetchone.side_effect = _fetchone
        cur.fetchall.return_value = history_rows

        with patch("app.routers.menu.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/menu/items/1/history")

        assert resp.status_code == 200
        data = resp.json()
        assert data["item_id"] == 1
        assert data["count"] == 1

    def test_history_not_found(self, client):
        """GET /api/menu/items/999/history — item doesn't exist — 404."""
        cur = _make_cursor(fetchone_val=None)

        with patch("app.routers.menu.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/menu/items/999/history")

        assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/menu/sync
# ─────────────────────────────────────────────────────────────────────────────

class TestMenuSync:
    def _make_airtable_records(self):
        return [
            {
                "id": "recABC123",
                "fields": {
                    "Item Name": "Dal Tadka",
                    "Category": "Lentils",
                    "Is Veg": True,
                    "Price": 8.99,
                },
            }
        ]

    def test_sync_items(self, client):
        """POST /api/menu/sync — new item created, returns upserted count."""
        airtable_records = self._make_airtable_records()

        # First fetchone: None (new item) — then fetchone for new id
        call_count = [0]
        cur = MagicMock()

        def _fetchone():
            call_count[0] += 1
            if call_count[0] == 1:
                return None  # no existing row
            return {"id": 10}  # RETURNING id after INSERT

        cur.fetchone.side_effect = _fetchone
        cur.fetchall.return_value = []  # no active rows to discard

        with patch("app.routers.menu._airtable_list_all", return_value=airtable_records), \
             patch("app.routers.menu.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/menu/sync")

        assert resp.status_code == 200
        data = resp.json()
        assert "upserted" in data
        assert data["upserted"] >= 1

    def test_sync_empty_list(self, client):
        """POST /api/menu/sync with no Airtable records — returns synced=0."""
        cur = _make_cursor(rows=[])

        with patch("app.routers.menu._airtable_list_all", return_value=[]), \
             patch("app.routers.menu.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/menu/sync")

        assert resp.status_code == 200
        data = resp.json()
        assert data["upserted"] == 0
        assert data["total"] == 0
