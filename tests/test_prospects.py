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


# ─────────────────────────────────────────────────────────────────────────────
# UpsertContact — phone-first lookup, ValueError branches
# ─────────────────────────────────────────────────────────────────────────────

class TestUpsertContactDirectly:
    """Call _upsert_contact directly to hit ValueError guard lines."""

    def test_raises_when_first_name_empty(self):
        """_upsert_contact raises ValueError when first_name is blank."""
        from app.routers.prospects import _upsert_contact
        cur = MagicMock()
        with pytest.raises(ValueError, match="first_name"):
            _upsert_contact(cur, "", "Smith", "", "a@b.com", "")

    def test_raises_when_no_phone_or_email(self):
        """_upsert_contact raises ValueError when both phone and email are absent."""
        from app.routers.prospects import _upsert_contact
        cur = MagicMock()
        with pytest.raises(ValueError, match="phone or email"):
            _upsert_contact(cur, "Sara", "", "", "", "")

    def test_add_value_error_returns_400(self, client):
        """POST /api/prospects/add with blank first_name that passes Pydantic returns 400."""
        # Pydantic model only has str — no strip validation — so a space passes Pydantic
        # but the endpoint checks p.first_name.strip() which would catch it.
        # The ValueError path (line 395) would require _upsert_contact to raise.
        # We can trigger this by patching _upsert_contact to raise ValueError.
        with patch("app.routers.prospects._upsert_contact",
                   side_effect=ValueError("first_name is required")):
            resp = client.post(
                "/api/prospects/add",
                json={"first_name": "X", "email": "x@test.com"},
            )
        assert resp.status_code == 400


class TestUpsertContactPhoneLookup:
    def test_upsert_finds_by_phone_when_no_email(self, client):
        """upload-csv upsert matches by phone when email is absent."""
        # no email column → phone lookup → found → update
        csv_content = b"FirstName,Phone\nAli,+14041234567\n"

        call_count = [0]
        cur = MagicMock()

        def _fetchone():
            call_count[0] += 1
            if call_count[0] == 1:
                return {"id": 20}  # phone found
            return {"contacts_updated": 1, "campaigns_queued": 1}

        cur.fetchone.side_effect = _fetchone
        cur.fetchall.return_value = []

        with patch("app.routers.prospects.get_cursor",
                   side_effect=lambda commit=True: _cursor_ctx(cur)):
            resp = client.post(
                "/api/prospects/upload-csv",
                files={"file": ("upload.csv", csv_content, "text/csv")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] == 1

    def test_upsert_row_exception_recorded_in_errors(self, client):
        """upload-csv records error when upsert raises unexpected exception."""
        csv_content = b"FirstName,Email\nBob,bob@test.com\n"

        cur = MagicMock()
        cur.fetchone.return_value = None
        cur.execute.side_effect = Exception("FK violation")
        cur.fetchall.return_value = []

        with patch("app.routers.prospects.get_cursor",
                   side_effect=lambda commit=True: _cursor_ctx(cur)):
            resp = client.post(
                "/api/prospects/upload-csv",
                files={"file": ("upload.csv", csv_content, "text/csv")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["skipped"] == 1
        assert data["errors"]

    def test_upload_lifecycle_fails_gracefully(self, client):
        """upload-csv with lifecycle failure still returns ok with error in lifecycle."""
        # Include Phone so phone lookup is also done (3 fetchone calls before INSERT)
        csv_content = b"FirstName,Email,Phone\nSara,sara@test.com,+14041234567\n"

        call_count = [0]
        cur = MagicMock()

        def _fetchone():
            call_count[0] += 1
            if call_count[0] == 1:
                return None   # no email match
            if call_count[0] == 2:
                return None   # no phone match
            if call_count[0] == 3:
                return {"id": 30}  # INSERT

        def _execute(sql, params=None):
            # When lifecycle SELECT is called, raise
            if "run_lifecycle_cycle" in sql:
                raise Exception("lifecycle failed")

        cur.fetchone.side_effect = _fetchone
        cur.execute.side_effect = _execute
        cur.fetchall.return_value = []

        with patch("app.routers.prospects.get_cursor",
                   side_effect=lambda commit=True: _cursor_ctx(cur)):
            resp = client.post(
                "/api/prospects/upload-csv",
                files={"file": ("upload.csv", csv_content, "text/csv")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["added"] == 1
        assert "error" in data["lifecycle"]

    def test_upload_csv_latin1_encoding(self, client):
        """upload-csv with latin-1 encoded file (has non-UTF-8 bytes) falls back to latin-1."""
        # Create a latin-1 encoded CSV with a special character
        raw = "FirstName,Email\nJosé,jose@test.com\n".encode("latin-1")

        call_count = [0]
        cur = MagicMock()

        def _fetchone():
            call_count[0] += 1
            if call_count[0] == 1:
                return None   # no email match
            if call_count[0] == 2:
                return None   # no phone match
            if call_count[0] == 3:
                return {"id": 99}
            return {"contacts_updated": 0, "campaigns_queued": 0}

        cur.fetchone.side_effect = _fetchone
        cur.fetchall.return_value = []

        with patch("app.routers.prospects.get_cursor",
                   side_effect=lambda commit=True: _cursor_ctx(cur)):
            resp = client.post(
                "/api/prospects/upload-csv",
                files={"file": ("upload.csv", raw, "text/csv")},
            )

        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# UpdateTemplate with Google Drive env — enqueues action
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdateTemplateWithDriveFolder:
    def test_update_template_with_folder_id_enqueues_drive_upload(self, client, monkeypatch):
        """GET /api/prospects/update-template with GOOGLE_DRIVE_FOLDER_ID set enqueues upload."""
        monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "test-folder-id-123")
        cur = _make_cursor()

        with patch("app.routers.prospects.get_cursor",
                   side_effect=lambda commit=True: _cursor_ctx(cur)):
            resp = client.get("/api/prospects/update-template")

        assert resp.status_code == 200
        # DB write was attempted (INSERT INTO action_queue)
        cur.execute.assert_called_once()

    def test_update_template_drive_enqueue_exception_ignored(self, client, monkeypatch):
        """GET /api/prospects/update-template still returns CSV even if Drive enqueue fails."""
        monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "test-folder-id")
        with patch("app.routers.prospects.get_cursor",
                   side_effect=Exception("DB error")):
            resp = client.get("/api/prospects/update-template")

        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")


# ─────────────────────────────────────────────────────────────────────────────
# UpdateCSV — phone fallback lookup + addr/prio/notes set fields
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdateCsvExtended:
    def test_update_csv_phone_fallback_when_email_not_found(self, client):
        """update-csv tries phone lookup when email returns nothing."""
        csv_content = b"EmailId,Phone,FirstName\nunknown@test.com,+14041234567,Alice\n"

        call_count = [0]
        cur = MagicMock()

        def _fetchone():
            call_count[0] += 1
            if call_count[0] == 1:
                return None   # email not found
            return {"id": 50}  # phone found

        cur.fetchone.side_effect = _fetchone
        cur.fetchall.return_value = []

        with patch("app.routers.prospects.get_cursor",
                   side_effect=lambda commit=True: _cursor_ctx(cur)):
            resp = client.post(
                "/api/prospects/update-csv",
                files={"file": ("update.csv", csv_content, "text/csv")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] == 1

    def test_update_csv_with_addr_prio_notes(self, client):
        """update-csv with Address, PriorityOverride, SalesNotes updates all fields."""
        csv_content = (
            b"EmailId,Address,PriorityOverride,SalesNotes\n"
            b"ali@test.com,123 Main St,high,Very hot lead\n"
        )

        cur = MagicMock()
        cur.fetchone.return_value = {"id": 55}
        cur.fetchall.return_value = []

        with patch("app.routers.prospects.get_cursor",
                   side_effect=lambda commit=True: _cursor_ctx(cur)):
            resp = client.post(
                "/api/prospects/update-csv",
                files={"file": ("update.csv", csv_content, "text/csv")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] == 1

    def test_update_csv_latin1_fallback(self, client):
        """update-csv with latin-1 encoded file falls back from UTF-8."""
        raw = "EmailId,FirstName\nmari@test.com,Märia\n".encode("latin-1")
        cur = MagicMock()
        cur.fetchone.return_value = {"id": 60}
        cur.fetchall.return_value = []

        with patch("app.routers.prospects.get_cursor",
                   side_effect=lambda commit=True: _cursor_ctx(cur)):
            resp = client.post(
                "/api/prospects/update-csv",
                files={"file": ("update.csv", raw, "text/csv")},
            )
        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# AddProspect — validation and exception branches
# ─────────────────────────────────────────────────────────────────────────────

class TestAddProspectExtended:
    def test_add_empty_first_name_returns_400(self, client):
        """POST /api/prospects/add with blank first_name returns 400."""
        resp = client.post(
            "/api/prospects/add",
            json={"first_name": "   ", "phone": "+14041234567"},
        )
        assert resp.status_code == 400

    def test_add_no_phone_or_email_returns_400(self, client):
        """POST /api/prospects/add with no phone or email returns 400."""
        resp = client.post(
            "/api/prospects/add",
            json={"first_name": "Sara"},
        )
        assert resp.status_code == 400

    def test_add_db_exception_returns_500(self, client):
        """POST /api/prospects/add DB error returns 500."""
        cur = MagicMock()
        cur.execute.side_effect = Exception("DB connection lost")
        cur.fetchone.return_value = None

        with patch("app.routers.prospects.get_cursor",
                   side_effect=lambda commit=True: _cursor_ctx(cur)):
            resp = client.post(
                "/api/prospects/add",
                json={"first_name": "Ahmed", "email": "ahmed@test.com"},
            )

        assert resp.status_code == 500

    def test_add_lifecycle_failure_still_returns_ok(self, client):
        """POST /api/prospects/add with lifecycle cycle failure returns ok with error."""
        # Include phone so we have email + phone lookup (3 fetchone calls before INSERT)
        call_count = [0]
        cur = MagicMock()

        def _fetchone():
            call_count[0] += 1
            if call_count[0] == 1:
                return None   # no email match
            if call_count[0] == 2:
                return None   # no phone match
            if call_count[0] == 3:
                return {"id": 70}   # INSERT new
            # lifecycle raises

        def _execute(sql, params=None):
            if "run_lifecycle_cycle" in sql:
                raise Exception("lifecycle DB error")

        cur.fetchone.side_effect = _fetchone
        cur.execute.side_effect = _execute
        cur.fetchall.return_value = []

        with patch("app.routers.prospects.get_cursor",
                   side_effect=lambda commit=True: _cursor_ctx(cur)):
            resp = client.post(
                "/api/prospects/add",
                json={"first_name": "Omar", "email": "omar@test.com",
                      "phone": "+14041234567"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "added"
        assert "error" in data["lifecycle"]
