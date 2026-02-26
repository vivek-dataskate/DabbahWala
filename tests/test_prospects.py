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


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/prospects/upload-csv
# ─────────────────────────────────────────────────────────────────────────────

class TestUploadProspectsCsv:
    def test_upload_csv_adds_new_contact(self, client):
        """POST /api/prospects/upload-csv with a valid CSV row adds a new contact."""
        csv_content = b"FirstName,LastName,Phone,EmailId,Address\nJohn,Doe,2485550100,john@example.com,123 Main St\n"

        # Sequence: SELECT by email → None, SELECT by phone → None, INSERT → {id:10}, lifecycle → result
        call_count = [0]
        cur = MagicMock()

        def _fetchone():
            call_count[0] += 1
            if call_count[0] == 1:
                return None  # no existing by email
            if call_count[0] == 2:
                return None  # no existing by phone
            if call_count[0] == 3:
                return {"id": 10}  # INSERT RETURNING
            return {"contacts_updated": 1, "campaigns_queued": 1}  # lifecycle

        cur.fetchone.side_effect = _fetchone
        cur.fetchall.return_value = []

        with patch("app.routers.prospects.get_cursor", side_effect=lambda commit=True: _cursor_ctx(cur)):
            resp = client.post(
                "/api/prospects/upload-csv",
                files={"file": ("prospects.csv", csv_content, "text/csv")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["added"] == 1
        assert data["status"] == "ok"

    def test_upload_csv_updates_existing_contact(self, client):
        """POST /api/prospects/upload-csv with existing email updates the contact."""
        csv_content = b"FirstName,LastName,Phone,EmailId\nJane,Smith,2485550101,jane@example.com\n"

        cur = MagicMock()
        cur.fetchone.return_value = {"id": 5}  # existing contact found by email
        cur.fetchall.return_value = []

        with patch("app.routers.prospects.get_cursor", side_effect=lambda commit=True: _cursor_ctx(cur)):
            resp = client.post(
                "/api/prospects/upload-csv",
                files={"file": ("prospects.csv", csv_content, "text/csv")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] == 1
        assert data["added"] == 0

    def test_upload_csv_skips_row_missing_name(self, client):
        """Rows without FirstName are skipped and counted in skipped."""
        csv_content = b"FirstName,LastName,Phone,EmailId\n,,2485550102,noname@example.com\n"
        cur = MagicMock()
        cur.fetchall.return_value = []

        with patch("app.routers.prospects.get_cursor", side_effect=lambda commit=True: _cursor_ctx(cur)):
            resp = client.post(
                "/api/prospects/upload-csv",
                files={"file": ("prospects.csv", csv_content, "text/csv")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["skipped"] == 1
        assert data["added"] == 0

    def test_upload_csv_empty_file_returns_400(self, client):
        """Empty CSV file returns 400 with 'CSV is empty' detail."""
        csv_content = b"FirstName,LastName,Phone,EmailId\n"

        with patch("app.routers.prospects.get_cursor", side_effect=Exception("should not be called")):
            resp = client.post(
                "/api/prospects/upload-csv",
                files={"file": ("empty.csv", csv_content, "text/csv")},
            )

        assert resp.status_code == 400

    def test_upload_csv_skips_row_no_phone_or_email(self, client):
        """Row with FirstName but no Phone or EmailId is skipped."""
        csv_content = b"FirstName,LastName,Phone,EmailId\nBob,NoPE,,\n"
        cur = MagicMock()
        cur.fetchall.return_value = []

        with patch("app.routers.prospects.get_cursor", side_effect=lambda commit=True: _cursor_ctx(cur)):
            resp = client.post(
                "/api/prospects/upload-csv",
                files={"file": ("prospects.csv", csv_content, "text/csv")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["skipped"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/prospects/update-csv
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdateProspectsCsv:
    def test_update_csv_updates_known_contact(self, client):
        """POST /api/prospects/update-csv with known email updates the contact."""
        csv_content = b"EmailId,Phone,FirstName,LastName\njohn@example.com,2485550100,Johnny,Doe\n"
        cur = MagicMock()
        cur.fetchone.return_value = {"id": 10}  # contact found
        cur.fetchall.return_value = []

        with patch("app.routers.prospects.get_cursor", side_effect=lambda commit=True: _cursor_ctx(cur)):
            resp = client.post(
                "/api/prospects/update-csv",
                files={"file": ("update.csv", csv_content, "text/csv")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] == 1
        assert data["skipped"] == 0

    def test_update_csv_skips_unknown_contact(self, client):
        """POST /api/prospects/update-csv with email not in DB skips and logs error."""
        csv_content = b"EmailId,Phone,FirstName\nunknown@example.com,,Alice\n"
        cur = MagicMock()
        cur.fetchone.return_value = None  # contact not found by email
        cur.fetchall.return_value = []

        with patch("app.routers.prospects.get_cursor", side_effect=lambda commit=True: _cursor_ctx(cur)):
            resp = client.post(
                "/api/prospects/update-csv",
                files={"file": ("update.csv", csv_content, "text/csv")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["skipped"] == 1
        assert data["updated"] == 0

    def test_update_csv_invalid_priority_skips_row(self, client):
        """Row with invalid PriorityOverride is skipped."""
        csv_content = b"EmailId,PriorityOverride\njohn@example.com,vip_platinum\n"
        cur = MagicMock()
        cur.fetchall.return_value = []

        with patch("app.routers.prospects.get_cursor", side_effect=lambda commit=True: _cursor_ctx(cur)):
            resp = client.post(
                "/api/prospects/update-csv",
                files={"file": ("update.csv", csv_content, "text/csv")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["skipped"] == 1
        assert len(data["errors"]) == 1

    def test_update_csv_empty_returns_400(self, client):
        """Empty CSV file returns 400."""
        csv_content = b"EmailId,Phone\n"
        resp = client.post(
            "/api/prospects/update-csv",
            files={"file": ("empty.csv", csv_content, "text/csv")},
        )
        assert resp.status_code == 400

    def test_update_csv_skips_row_no_identifier(self, client):
        """Row with no EmailId or Phone is skipped."""
        csv_content = b"EmailId,Phone,FirstName\n,,Alice\n"
        cur = MagicMock()
        cur.fetchall.return_value = []

        with patch("app.routers.prospects.get_cursor", side_effect=lambda commit=True: _cursor_ctx(cur)):
            resp = client.post(
                "/api/prospects/update-csv",
                files={"file": ("update.csv", csv_content, "text/csv")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["skipped"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/prospects/update-template-file
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdateTemplateFile:
    def test_get_update_template_file(self, client):
        """GET /api/prospects/update-template-file returns CSV."""
        resp = client.get("/api/prospects/update-template-file")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")
