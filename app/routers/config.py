"""
Centralized Credential Service — GET /api/credentials
Email Proxy              — POST /api/internal/send-email
Drive Upload Proxy       — POST /api/internal/drive/upload

All endpoints require the X-Admin-Secret header to match the ADMIN_SECRET
env var. n8n workflows fetch credentials at runtime instead of storing
them in the n8n credential store. This means n8n credential store holds
exactly ONE entry: the "DW Admin Secret" HTTP Header Auth credential that
unlocks this endpoint.

Usage from n8n:
  GET https://dabbahwala-latest.onrender.com/api/credentials
  Header: X-Admin-Secret: <value of ADMIN_SECRET env var>

  Response: {
    TELNYX_API_KEY, TELNYX_FROM_NUMBER, TELNYX_MESSAGING_PROFILE_ID,
    AIRTABLE_API_KEY, AIRTABLE_BASE_ID,
    INSTANTLY_BEARER,
    SHIPDAY_API_KEY,
    REPORT_EMAIL_TO
  }

Email proxy:
  POST /api/internal/send-email
  Header: X-Admin-Secret: <value>
  Body: {"to": "...", "subject": "...", "body_html": "...", "cc": "..."}
  Uses SMTP_USER / SMTP_PASSWORD / SMTP_HOST / SMTP_PORT env vars.

Drive upload proxy:
  POST /api/internal/drive/upload
  Header: X-Admin-Secret: <value>
  Body: {"filename": "...", "content": "...", "mimetype": "text/csv"}
"""
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


def _check_admin_secret(x_admin_secret: str | None) -> None:
    """Raise 403 if the provided secret doesn't match ADMIN_SECRET env var."""
    expected = os.environ.get("ADMIN_SECRET", "").strip()
    if not expected:
        logger.error("ADMIN_SECRET env var is not set — /api/credentials endpoint is disabled")
        raise HTTPException(status_code=503, detail="Credential service not configured (ADMIN_SECRET missing)")
    if x_admin_secret != expected:
        logger.warning("GET /api/credentials — invalid or missing X-Admin-Secret")
        raise HTTPException(status_code=403, detail="Forbidden")


# ---------------------------------------------------------------------------
# GET /api/credentials
# ---------------------------------------------------------------------------
@router.get("")
def get_credentials(x_admin_secret: str | None = Header(default=None)):
    """
    Return all runtime credentials as JSON.
    Called by n8n workflows at the start of each execution.

    Protected by X-Admin-Secret header. Returns only downstream service
    credentials — never the ADMIN_SECRET itself.
    """
    _check_admin_secret(x_admin_secret)
    logger.debug("GET /api/credentials — serving credentials to n8n")

    creds = {
        # Telnyx SMS
        "TELNYX_API_KEY":                os.environ.get("TELNYX_API_KEY", ""),
        "TELNYX_FROM_NUMBER":             os.environ.get("TELNYX_FROM_NUMBER", "+18444322224"),
        "TELNYX_MESSAGING_PROFILE_ID":    os.environ.get("TELNYX_MESSAGING_PROFILE_ID", "400191f9-0057-41f5-9f10-375fb3fe1a70"),
        "SMS_TEST_NUMBER":               os.environ.get("SMS_TEST_NUMBER", "+12144996143"),
        # Airtable
        "AIRTABLE_API_KEY":              os.environ.get("AIRTABLE_API_KEY", ""),
        "AIRTABLE_BASE_ID":              os.environ.get("AIRTABLE_BASE_ID", "appuy2VTIao6XVpIW"),
        # Instantly email campaigns
        "INSTANTLY_BEARER":              os.environ.get("INSTANTLY_API_KEY", ""),
        # Shipday delivery
        "SHIPDAY_API_KEY":               os.environ.get("SHIPDAY_API_KEY", ""),
        # Reports
        "REPORT_EMAIL_TO":               os.environ.get("REPORT_EMAIL_TO", "core@dabbahwala.com"),
        # FastAPI base URL (for n8n to call back)
        "API_BASE_URL":                  os.environ.get("BASE_URL", "https://dabbahwala-latest.onrender.com"),
    }

    # Warn about any missing critical credentials so Render logs show it
    missing = [k for k, v in creds.items() if not v and k not in ("SHIPDAY_API_KEY", "SMS_TEST_NUMBER")]
    if missing:
        logger.warning("GET /api/credentials — missing env vars: %s", missing)

    return creds


# ---------------------------------------------------------------------------
# POST /api/internal/send-email
# ---------------------------------------------------------------------------
class EmailRequest(BaseModel):
    to: str
    subject: str
    body_html: str
    cc: str | None = None
    reply_to: str | None = None


@router.post("/send-email")
def send_email(payload: EmailRequest, x_admin_secret: str | None = Header(default=None)):
    """
    Email proxy — sends via SMTP using SMTP_USER / SMTP_PASSWORD / SMTP_HOST / SMTP_PORT env vars.
    n8n calls this instead of using the Gmail-SMTP n8n credential.

    Required env vars:
      SMTP_USER     — sender address (e.g. vivek@dabbahwala.com)
      SMTP_PASSWORD — app password
      SMTP_HOST     — defaults to smtp.gmail.com
      SMTP_PORT     — defaults to 587 (STARTTLS); use 465 for SSL
    """
    _check_admin_secret(x_admin_secret)
    logger.info("POST /api/internal/send-email — to=%s subject=%s", payload.to, payload.subject)

    smtp_user = os.environ.get("SMTP_USER", "").strip()
    smtp_pass = os.environ.get("SMTP_PASSWORD", "").strip()
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com").strip()
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    from_addr = os.environ.get("REPORT_EMAIL_FROM", smtp_user).strip() or smtp_user

    if not smtp_user or not smtp_pass:
        logger.error("send-email: SMTP_USER or SMTP_PASSWORD not set")
        raise HTTPException(status_code=503, detail="Email service not configured")

    msg = MIMEMultipart("alternative")
    msg["From"] = from_addr
    msg["To"] = payload.to
    msg["Subject"] = payload.subject
    if payload.cc:
        msg["Cc"] = payload.cc
    if payload.reply_to:
        msg["Reply-To"] = payload.reply_to
    msg.attach(MIMEText(payload.body_html, "html"))

    recipients = [payload.to]
    if payload.cc:
        recipients.extend(payload.cc.split(","))

    try:
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
                server.login(smtp_user, smtp_pass)
                server.sendmail(from_addr, recipients, msg.as_string())
        else:
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.ehlo()
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.sendmail(from_addr, recipients, msg.as_string())
        logger.info("send-email: sent successfully to=%s subject=%s", payload.to, payload.subject)
        return {"status": "sent", "to": payload.to, "subject": payload.subject}
    except smtplib.SMTPException as e:
        logger.error("send-email: SMTP error to=%s: %s", payload.to, e, exc_info=True)
        raise HTTPException(status_code=502, detail=f"SMTP error: {e}")


# ---------------------------------------------------------------------------
# POST /api/internal/drive/upload
# ---------------------------------------------------------------------------
class DriveUploadRequest(BaseModel):
    filename: str
    content: str          # file content as a string (CSV, HTML, etc.)
    mimetype: str = "text/csv"
    folder_id: str | None = None  # optional override; uses GOOGLE_DRIVE_FOLDER_ID env var if omitted


@router.post("/drive/upload")
def drive_upload(payload: DriveUploadRequest, x_admin_secret: str | None = Header(default=None)):
    """
    Google Drive upload proxy — uses the service account in GOOGLE_SERVICE_ACCOUNT_JSON.
    n8n calls this instead of using the Google Drive OAuth2 n8n credential.

    Required env vars:
      GOOGLE_SERVICE_ACCOUNT_JSON — full JSON string of the service account key
      GOOGLE_DRIVE_FOLDER_ID      — Drive folder ID to upload into
    """
    _check_admin_secret(x_admin_secret)
    logger.info("POST /api/internal/drive/upload — filename=%s mimetype=%s", payload.filename, payload.mimetype)

    from app.services.drive import upload_csv
    link = upload_csv(payload.content, payload.filename, folder_id=payload.folder_id)

    if not link:
        raise HTTPException(status_code=502, detail="Drive upload failed — check GOOGLE_SERVICE_ACCOUNT_JSON env var")

    return {"status": "uploaded", "filename": payload.filename, "link": link}


# ---------------------------------------------------------------------------
# GET /api/internal/drive/files
# ---------------------------------------------------------------------------
@router.get("/drive/files")
def list_drive_files(
    folder_id: str | None = None,
    x_admin_secret: str | None = Header(default=None),
):
    """
    List files in a Google Drive folder.
    Uses GOOGLE_DRIVE_FOLDER_ID env var if folder_id query param is not provided.
    Returns {"files": [{id, name, mimeType, modifiedTime, lastModifyingUser}, ...]}.
    Used by [Chatbot] Docs Sync workflow to replace googleDrive list node.
    """
    _check_admin_secret(x_admin_secret)
    from app.services.drive import list_folder_files
    return list_folder_files(folder_id)


# ---------------------------------------------------------------------------
# GET /api/internal/docs/{doc_id}
# ---------------------------------------------------------------------------
@router.get("/docs/{doc_id}")
def read_google_doc(
    doc_id: str,
    x_admin_secret: str | None = Header(default=None),
):
    """
    Read a Google Doc and return its plain-text content.
    Returns {"id": ..., "title": ..., "text": ...}.
    Used by [Chatbot] Docs Sync workflow to replace googleDocs read node.
    """
    _check_admin_secret(x_admin_secret)
    from app.services.drive import read_doc
    return read_doc(doc_id)
