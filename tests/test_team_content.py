"""
Tests for app/routers/team_content.py
=======================================

Covers:
  Endpoint tests (TestClient with mocked DB)
    - POST /api/team-content/sync   — new doc, existing doc, empty list, missing body
    - POST /api/team-content/submit — observation, with author
    - GET  /api/team-content/browse — empty, with content_type filter
    - POST /api/team-content/search — keyword search

Run with:
    pytest tests/test_team_content.py -v
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
# POST /api/team-content/sync
# ─────────────────────────────────────────────────────────────────────────────

class TestSyncDocs:
    def test_sync_new_doc(self, client):
        """POST sync with a new google doc — created:1 returned."""
        cur = _make_cursor(fetchone_val=None)  # no existing doc

        with patch("app.routers.team_content.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/team-content/sync",
                json={
                    "documents": [
                        {
                            "google_doc_id": "doc1",
                            "content_type": "ground_note",
                            "title": "Field Report",
                            "body": "Delivery was smooth today.",
                        }
                    ]
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 1
        assert data["updated"] == 0

    def test_sync_existing_doc(self, client):
        """POST sync with existing doc — updated:1 returned."""
        existing = {"id": 5, "google_last_modified": "2026-01-01T00:00:00Z"}
        cur = _make_cursor(fetchone_val=existing)

        with patch("app.routers.team_content.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/team-content/sync",
                json={
                    "documents": [
                        {
                            "google_doc_id": "doc1",
                            "content_type": "ground_note",
                            "title": "Field Report Updated",
                            "body": "Updated content here.",
                            "google_last_modified": "2026-02-01T00:00:00Z",
                        }
                    ]
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] == 1

    def test_sync_empty(self, client):
        """POST sync with empty documents list — synced:0."""
        cur = _make_cursor()

        with patch("app.routers.team_content.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/team-content/sync", json={"documents": []})

        assert resp.status_code == 200
        data = resp.json()
        assert data["synced"] == 0
        assert data["created"] == 0

    def test_sync_missing_body(self, client):
        """POST sync with doc missing body — skipped."""
        cur = _make_cursor()

        with patch("app.routers.team_content.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/team-content/sync",
                json={
                    "documents": [
                        {
                            "google_doc_id": "doc2",
                            "content_type": "ground_note",
                            "title": "Empty Doc",
                            "body": "",  # empty body — should be skipped
                        }
                    ]
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 0
        assert data["skipped"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/team-content/submit
# ─────────────────────────────────────────────────────────────────────────────

class TestSubmitContent:
    def test_submit_observation(self, client):
        """POST submit with content_type=observation — stored with id returned."""
        cur = _make_cursor(fetchone_val={"id": 1})

        with patch("app.routers.team_content.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/team-content/submit",
                json={"content_type": "observation", "content": "Noticed high demand today."},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "stored"
        assert data["id"] == 1
        assert data["content_type"] == "observation"

    def test_submit_with_author(self, client):
        """POST submit with author field — 200."""
        cur = _make_cursor(fetchone_val={"id": 7})

        with patch("app.routers.team_content.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/team-content/submit",
                json={
                    "content_type": "ground_note",
                    "content": "Driver reported delay due to traffic.",
                    "author": "Raj Kumar",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "stored"
        assert data["id"] == 7


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/team-content/browse
# ─────────────────────────────────────────────────────────────────────────────

class TestBrowseContent:
    def test_browse_empty(self, client):
        """GET /api/team-content/browse with no rows — 200 {count:0}."""
        cur = _make_cursor(rows=[])

        with patch("app.routers.team_content.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/team-content/browse")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["content"] == []

    def test_browse_with_type(self, client):
        """GET /api/team-content/browse?content_type=observation — filters and returns."""
        rows = [
            {
                "id": 3,
                "content_type": "observation",
                "title": "Test Note",
                "body": "Some observation.",
                "author": "Jane",
                "created_at": None,
            }
        ]
        cur = _make_cursor(rows=rows)

        with patch("app.routers.team_content.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/team-content/browse?content_type=observation&limit=5")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/team-content/search
# ─────────────────────────────────────────────────────────────────────────────

class TestSearchContent:
    def test_search(self, client):
        """POST search with keyword — returns matching results."""
        rows = [
            {
                "id": 4,
                "content_type": "ground_note",
                "title": "Delivery Issue",
                "body": "Customer reported late delivery.",
                "author": "Field Agent",
                "created_at": None,
            }
        ]
        cur = _make_cursor(rows=rows)

        with patch("app.routers.team_content.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/team-content/search",
                json={"search_query": "delivery"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert isinstance(data["results"], list)
