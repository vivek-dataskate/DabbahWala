"""
Tests for app/routers/config.py
================================

Covers:
  - GET  /api/credentials          — auth, missing secret, valid secret
  - POST /api/internal/send-email  — no SMTP creds, valid send (mocked SMTP)
  - POST /api/internal/drive/upload — drive upload (mocked service)
  - GET  /api/internal/drive/files  — list files (mocked service)
  - GET  /api/internal/docs/{id}    — read doc (mocked service)
  - _check_admin_secret             — direct tests

Run with:
    pytest tests/test_config.py -v
"""

from unittest.mock import MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/credentials
# ─────────────────────────────────────────────────────────────────────────────

class TestGetCredentials:
    def test_no_admin_secret_env_returns_503(self, client, monkeypatch):
        """When ADMIN_SECRET env var is not set, endpoint returns 503."""
        monkeypatch.delenv("ADMIN_SECRET", raising=False)
        resp = client.get("/api/credentials", headers={"X-Admin-Secret": "anything"})
        assert resp.status_code == 503

    def test_wrong_secret_returns_403(self, client, monkeypatch):
        """Wrong X-Admin-Secret header returns 403."""
        monkeypatch.setenv("ADMIN_SECRET", "correct-secret")
        resp = client.get("/api/credentials", headers={"X-Admin-Secret": "wrong-secret"})
        assert resp.status_code == 403

    def test_missing_secret_header_returns_403(self, client, monkeypatch):
        """Missing X-Admin-Secret header returns 403."""
        monkeypatch.setenv("ADMIN_SECRET", "correct-secret")
        resp = client.get("/api/credentials")
        assert resp.status_code == 403

    def test_valid_secret_returns_credentials(self, client, monkeypatch):
        """Valid X-Admin-Secret returns credential dict."""
        monkeypatch.setenv("ADMIN_SECRET", "secret123")
        monkeypatch.setenv("TELNYX_API_KEY", "telnyx-key")
        monkeypatch.setenv("AIRTABLE_API_KEY", "airtable-key")
        monkeypatch.setenv("INSTANTLY_API_KEY", "instantly-key")
        monkeypatch.setenv("SHIPDAY_API_KEY", "shipday-key")
        resp = client.get("/api/credentials", headers={"X-Admin-Secret": "secret123"})
        assert resp.status_code == 200
        data = resp.json()
        assert "TELNYX_API_KEY" in data
        assert "AIRTABLE_API_KEY" in data
        assert "INSTANTLY_BEARER" in data

    def test_credentials_never_include_admin_secret(self, client, monkeypatch):
        """Response must not contain ADMIN_SECRET itself."""
        monkeypatch.setenv("ADMIN_SECRET", "super-secret")
        resp = client.get("/api/credentials", headers={"X-Admin-Secret": "super-secret"})
        assert resp.status_code == 200
        data = resp.json()
        assert "ADMIN_SECRET" not in data


# ─────────────────────────────────────────────────────────────────────────────
# _check_admin_secret — direct tests
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckAdminSecret:
    def test_raises_503_when_no_env_var(self, monkeypatch):
        """_check_admin_secret raises 503 when ADMIN_SECRET not set."""
        from fastapi import HTTPException
        from app.routers.config import _check_admin_secret
        monkeypatch.delenv("ADMIN_SECRET", raising=False)
        with pytest.raises(HTTPException) as exc_info:
            _check_admin_secret("any-secret")
        assert exc_info.value.status_code == 503

    def test_raises_403_on_wrong_secret(self, monkeypatch):
        """_check_admin_secret raises 403 when secret doesn't match."""
        from fastapi import HTTPException
        from app.routers.config import _check_admin_secret
        monkeypatch.setenv("ADMIN_SECRET", "correct")
        with pytest.raises(HTTPException) as exc_info:
            _check_admin_secret("wrong")
        assert exc_info.value.status_code == 403

    def test_passes_when_correct_secret(self, monkeypatch):
        """_check_admin_secret does not raise when secret matches."""
        from app.routers.config import _check_admin_secret
        monkeypatch.setenv("ADMIN_SECRET", "my-secret")
        _check_admin_secret("my-secret")  # Should not raise


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/internal/send-email
# ─────────────────────────────────────────────────────────────────────────────

class TestSendEmail:
    def test_no_smtp_creds_returns_503(self, client, monkeypatch):
        """Without SMTP_USER/SMTP_PASSWORD, returns 503."""
        monkeypatch.setenv("ADMIN_SECRET", "admin")
        monkeypatch.delenv("SMTP_USER", raising=False)
        monkeypatch.delenv("SMTP_PASSWORD", raising=False)
        resp = client.post(
            "/api/internal/send-email",
            headers={"X-Admin-Secret": "admin"},
            json={"to": "test@example.com", "subject": "Test", "body_html": "<p>hi</p>"},
        )
        assert resp.status_code == 503

    def test_wrong_admin_secret_returns_403(self, client, monkeypatch):
        """Wrong admin secret returns 403."""
        monkeypatch.setenv("ADMIN_SECRET", "admin")
        resp = client.post(
            "/api/internal/send-email",
            headers={"X-Admin-Secret": "wrong"},
            json={"to": "test@example.com", "subject": "Test", "body_html": "<p>hi</p>"},
        )
        assert resp.status_code == 403

    def test_sends_email_with_starttls(self, client, monkeypatch):
        """Mocked SMTP sends email via STARTTLS (port 587) and returns status sent."""
        monkeypatch.setenv("ADMIN_SECRET", "admin")
        monkeypatch.setenv("SMTP_USER", "user@example.com")
        monkeypatch.setenv("SMTP_PASSWORD", "pass")
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_PORT", "587")

        mock_smtp = MagicMock()
        mock_smtp_instance = MagicMock()
        mock_smtp.return_value.__enter__ = lambda s: mock_smtp_instance
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)

        with patch("app.routers.config.smtplib.SMTP", return_value=mock_smtp.return_value):
            resp = client.post(
                "/api/internal/send-email",
                headers={"X-Admin-Secret": "admin"},
                json={
                    "to": "dest@example.com",
                    "subject": "Hello",
                    "body_html": "<p>World</p>",
                    "cc": "cc@example.com",
                    "reply_to": "reply@example.com",
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "sent"
        assert data["to"] == "dest@example.com"

    def test_sends_email_with_ssl(self, client, monkeypatch):
        """Mocked SMTP_SSL sends email via SSL (port 465) and returns status sent."""
        monkeypatch.setenv("ADMIN_SECRET", "admin")
        monkeypatch.setenv("SMTP_USER", "user@example.com")
        monkeypatch.setenv("SMTP_PASSWORD", "pass")
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_PORT", "465")

        mock_smtp_ssl = MagicMock()
        mock_smtp_ssl_instance = MagicMock()
        mock_smtp_ssl.return_value.__enter__ = lambda s: mock_smtp_ssl_instance
        mock_smtp_ssl.return_value.__exit__ = MagicMock(return_value=False)

        with patch("app.routers.config.smtplib.SMTP_SSL", return_value=mock_smtp_ssl.return_value):
            resp = client.post(
                "/api/internal/send-email",
                headers={"X-Admin-Secret": "admin"},
                json={"to": "dest@example.com", "subject": "SSL Test", "body_html": "<b>hi</b>"},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "sent"

    def test_smtp_error_returns_502(self, client, monkeypatch):
        """SMTP error propagates as 502."""
        import smtplib
        monkeypatch.setenv("ADMIN_SECRET", "admin")
        monkeypatch.setenv("SMTP_USER", "user@example.com")
        monkeypatch.setenv("SMTP_PASSWORD", "pass")
        monkeypatch.setenv("SMTP_PORT", "587")

        with patch("app.routers.config.smtplib.SMTP", side_effect=smtplib.SMTPException("Connection refused")):
            resp = client.post(
                "/api/internal/send-email",
                headers={"X-Admin-Secret": "admin"},
                json={"to": "dest@example.com", "subject": "Fail", "body_html": "<p>oops</p>"},
            )
        assert resp.status_code == 502


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/internal/drive/upload
# ─────────────────────────────────────────────────────────────────────────────

class TestDriveUpload:
    def test_wrong_secret_returns_403(self, client, monkeypatch):
        """Wrong admin secret returns 403."""
        monkeypatch.setenv("ADMIN_SECRET", "admin")
        resp = client.post(
            "/api/internal/drive/upload",
            headers={"X-Admin-Secret": "bad"},
            json={"filename": "test.csv", "content": "a,b\n1,2"},
        )
        assert resp.status_code == 403

    def test_upload_success(self, client, monkeypatch):
        """Mocked drive upload returns status uploaded and link."""
        monkeypatch.setenv("ADMIN_SECRET", "admin")
        with patch("app.services.drive.upload_csv", return_value="https://drive.google.com/file/123"):
            resp = client.post(
                "/api/internal/drive/upload",
                headers={"X-Admin-Secret": "admin"},
                json={"filename": "report.csv", "content": "col1,col2\n1,2"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "uploaded"
        assert "drive.google.com" in data["link"]

    def test_upload_failure_returns_502(self, client, monkeypatch):
        """When upload_csv returns None/falsy, endpoint returns 502."""
        monkeypatch.setenv("ADMIN_SECRET", "admin")
        with patch("app.services.drive.upload_csv", return_value=None):
            resp = client.post(
                "/api/internal/drive/upload",
                headers={"X-Admin-Secret": "admin"},
                json={"filename": "fail.csv", "content": "data"},
            )
        assert resp.status_code == 502


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/internal/drive/files
# ─────────────────────────────────────────────────────────────────────────────

class TestListDriveFiles:
    def test_wrong_secret_returns_403(self, client, monkeypatch):
        """Wrong admin secret returns 403."""
        monkeypatch.setenv("ADMIN_SECRET", "admin")
        resp = client.get("/api/internal/drive/files", headers={"X-Admin-Secret": "bad"})
        assert resp.status_code == 403

    def test_lists_files_successfully(self, client, monkeypatch):
        """Valid request with mocked drive returns file list."""
        monkeypatch.setenv("ADMIN_SECRET", "admin")
        mock_files = {
            "files": [{"id": "abc", "name": "menu.csv", "mimeType": "text/csv"}]
        }
        with patch("app.services.drive.list_folder_files", return_value=mock_files):
            resp = client.get("/api/internal/drive/files", headers={"X-Admin-Secret": "admin"})
        assert resp.status_code == 200
        data = resp.json()
        assert "files" in data
        assert data["files"][0]["name"] == "menu.csv"


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/internal/docs/{doc_id}
# ─────────────────────────────────────────────────────────────────────────────

class TestReadGoogleDoc:
    def test_wrong_secret_returns_403(self, client, monkeypatch):
        """Wrong admin secret returns 403."""
        monkeypatch.setenv("ADMIN_SECRET", "admin")
        resp = client.get("/api/internal/docs/doc123", headers={"X-Admin-Secret": "bad"})
        assert resp.status_code == 403

    def test_reads_doc_successfully(self, client, monkeypatch):
        """Valid request returns doc content."""
        monkeypatch.setenv("ADMIN_SECRET", "admin")
        mock_doc = {"id": "doc123", "title": "Menu Policy", "text": "All items are fresh."}
        with patch("app.services.drive.read_doc", return_value=mock_doc):
            resp = client.get("/api/internal/docs/doc123", headers={"X-Admin-Secret": "admin"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Menu Policy"
        assert "fresh" in data["text"]
