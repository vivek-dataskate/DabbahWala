"""
Tests for app/routers/prospects.py
=====================================

Covers:
  Endpoint tests (TestClient with mocked DB)
    - GET  /api/prospects/template        — returns CSV file
    - GET  /api/prospects/update-template — returns CSV file
    - POST /api/prospects/add             — add new, duplicate, missing first_name → 422

Run with:
    pytest tests/test_prospects.py -v
"""

import io
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
# GET /api/prospects/template
# ─────────────────────────────────────────────────────────────────────────────

class TestProspectTemplate:
    def test_get_template(self, client):
        """GET /api/prospects/template — 200 with text/csv content type."""
        resp = client.get("/api/prospects/template")

        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")

    def test_get_update_template(self, client):
        """GET /api/prospects/update-template — 200 with text/csv content type."""
        cur = _make_cursor()

        with patch("app.routers.prospects.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/prospects/update-template")

        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/prospects/add
# ─────────────────────────────────────────────────────────────────────────────

class TestAddProspect:
    def test_add_prospect(self, client):
        """POST /api/prospects/add — new contact created, lifecycle run, status='added'."""
        # _upsert_contact: SELECT by email → None, INSERT → {id:10}
        # lifecycle SELECT → {contacts_updated:1, campaigns_queued:1}
        call_count = [0]

        def _fetchone():
            call_count[0] += 1
            if call_count[0] == 1:
                return None   # no existing contact by email
            if call_count[0] == 2:
                return None   # no existing contact by phone
            if call_count[0] == 3:
                return {"id": 10}  # RETURNING id from INSERT
            # lifecycle result
            return {"contacts_updated": 1, "campaigns_queued": 1}

        cur = MagicMock()
        cur.fetchone.side_effect = _fetchone
        cur.fetchall.return_value = []

        with patch("app.routers.prospects.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/prospects/add",
                json={
                    "first_name": "John",
                    "last_name": "Doe",
                    "phone": "+12145550001",
                    "email": "john@example.com",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "added"
        assert data["contact_id"] == 10

    def test_add_duplicate_prospect(self, client):
        """POST /api/prospects/add — contact already exists — status='updated'."""
        # SELECT by email → existing contact
        cur = _make_cursor(fetchone_val={"id": 5})

        with patch("app.routers.prospects.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/prospects/add",
                json={
                    "first_name": "Jane",
                    "last_name": "Smith",
                    "email": "jane@example.com",
                    "phone": "+12145550002",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"

    def test_add_missing_name(self, client):
        """POST /api/prospects/add without first_name — 422 validation error."""
        resp = client.post(
            "/api/prospects/add",
            json={"last_name": "Doe", "phone": "+12145550003"},
        )
        # FastAPI will reject missing required field
        assert resp.status_code == 422
