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


# ---------------------------------------------------------------------------
# Internal helper tests (direct)
# ---------------------------------------------------------------------------

class TestAirtableHelpers:
    """Test _to_airtable_fields and _from_airtable_record helpers."""

    def test_to_airtable_fields_minimal(self):
        """_to_airtable_fields with only item_name returns just Item Name."""
        from app.routers.menu import _to_airtable_fields
        item = {"item_name": "Dal Makhani"}
        fields = _to_airtable_fields(item)
        assert fields["Item Name"] == "Dal Makhani"
        assert len(fields) == 1

    def test_to_airtable_fields_full(self):
        """_to_airtable_fields with all fields maps everything correctly."""
        from app.routers.menu import _to_airtable_fields
        item = {
            "item_name": "Butter Chicken",
            "category": "Mains",
            "is_veg": False,
            "description": "Creamy tomato sauce",
            "image_url": "https://img.example.com/butter.jpg",
            "price": 12.99,
            "added_date": "2026-01-15",
        }
        fields = _to_airtable_fields(item)
        assert fields["Item Name"] == "Butter Chicken"
        assert fields["Category"] == "Mains"
        assert fields["Is Veg"] is False
        assert fields["Price"] == 12.99

    def test_from_airtable_record_full(self):
        """_from_airtable_record correctly maps all Airtable fields."""
        from app.routers.menu import _from_airtable_record
        record = {
            "id": "rec_abc123",
            "fields": {
                "Item Name": "Biryani",
                "Category": "Rice",
                "Is Veg": False,
                "Description": "Fragrant basmati rice",
                "Image URL": "https://img.example.com/biryani.jpg",
                "Price": 15.99,
                "Added Date": "2026-01-10",
            }
        }
        item = _from_airtable_record(record)
        assert item["airtable_record_id"] == "rec_abc123"
        assert item["item_name"] == "Biryani"
        assert item["category"] == "Rice"
        assert item["price"] == 15.99

    def test_from_airtable_record_missing_fields(self):
        """_from_airtable_record handles missing optional fields gracefully."""
        from app.routers.menu import _from_airtable_record
        record = {"id": "rec_xyz", "fields": {"Item Name": "Naan"}}
        item = _from_airtable_record(record)
        assert item["item_name"] == "Naan"
        assert item["category"] is None
        assert item["price"] is None


class TestMenuSyncWithExistingItems:
    """Test POST /api/menu/sync with existing items (update path)."""

    def test_sync_updates_existing_item(self, client):
        """When item already exists, it's updated (not duplicated)."""
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch

        at_record = {
            "id": "rec_001",
            "fields": {
                "Item Name": "Dal Makhani",
                "Category": "Lentils",
                "Price": 10.99,
            }
        }
        existing_row = {
            "id": 5, "item_name": "Dal Makhani", "price": 9.99,
            "category": "Lentils", "is_veg": True,
            "description": None, "image_url": None,
        }

        call_count = [0]

        @contextmanager
        def _cursor_ctx(commit=True):
            call_count[0] += 1
            cur = MagicMock()
            if call_count[0] == 1:
                cur.fetchone.return_value = existing_row  # existing item found
            else:
                cur.fetchall.return_value = []  # no active rows for deletion check
            yield cur

        with patch("app.routers.menu._airtable_list_all", return_value=[at_record]), \
             patch("app.routers.menu.get_cursor", side_effect=_cursor_ctx):
            resp = client.post("/api/menu/sync")

        assert resp.status_code == 200
        data = resp.json()
        assert data["upserted"] == 1

    def test_sync_discards_deleted_items(self, client):
        """Items in DB but not in Airtable are marked inactive."""
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch

        # Empty Airtable but DB has one active item
        db_item = {
            "id": 10, "item_name": "Old Item",
            "airtable_record_id": "rec_OLD123",
        }

        call_count = [0]

        @contextmanager
        def _cursor_ctx(commit=True):
            call_count[0] += 1
            cur = MagicMock()
            cur.fetchall.return_value = [db_item]
            yield cur

        with patch("app.routers.menu._airtable_list_all", return_value=[]), \
             patch("app.routers.menu.get_cursor", side_effect=_cursor_ctx):
            resp = client.post("/api/menu/sync")

        assert resp.status_code == 200
        data = resp.json()
        assert data["discarded"] == 1

    def test_sync_airtable_error_returns_502(self, client):
        """When Airtable raises an exception, returns 502."""
        import httpx
        with patch("app.routers.menu._airtable_list_all",
                   side_effect=httpx.ConnectError("timeout")):
            resp = client.post("/api/menu/sync")
        assert resp.status_code == 502


class TestMenuItemHistory:
    def test_get_history_not_found_returns_404(self, client):
        """GET /items/{id}/history for unknown item returns 404."""
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch

        cur = MagicMock()
        cur.fetchone.return_value = None  # item not found

        @contextmanager
        def _cursor_ctx(commit=False):
            yield cur

        with patch("app.routers.menu.get_cursor", side_effect=_cursor_ctx):
            resp = client.get("/api/menu/items/9999/history")

        assert resp.status_code == 404

    def test_get_history_empty(self, client):
        """GET /items/{id}/history for known item with no history returns empty list."""
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch

        # The endpoint uses ONE cursor context with two DB calls:
        # 1. fetchone() to check item exists
        # 2. fetchall() to get history
        cur = MagicMock()
        cur.fetchone.return_value = {"id": 5}  # item exists check passes
        cur.fetchall.return_value = []          # no history rows

        @contextmanager
        def _cursor_ctx(commit=False):
            yield cur

        with patch("app.routers.menu.get_cursor", side_effect=_cursor_ctx):
            resp = client.get("/api/menu/items/5/history")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["history"] == []
