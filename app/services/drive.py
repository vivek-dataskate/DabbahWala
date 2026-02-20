"""Google Drive upload helper.

Requires env vars:
  GOOGLE_SERVICE_ACCOUNT_JSON  — full JSON of the service account key file
  GOOGLE_DRIVE_FOLDER_ID       — Drive folder ID to upload files into

The service account email must be granted Editor access to that folder.
"""
import io
import json
import logging
import os

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/drive"]


def upload_csv(csv_content: str, filename: str) -> str:
    """Upload a CSV string to Google Drive. Returns webViewLink or empty string."""
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip()

    if not sa_json or not folder_id:
        logger.debug("Drive upload skipped — env vars not configured")
        return ""

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseUpload

        creds = service_account.Credentials.from_service_account_info(
            json.loads(sa_json), scopes=_SCOPES
        )
        service = build("drive", "v3", credentials=creds, cache_discovery=False)

        media = MediaIoBaseUpload(
            io.BytesIO(csv_content.encode("utf-8")),
            mimetype="text/csv",
            resumable=False,
        )
        uploaded = service.files().create(
            body={"name": filename, "parents": [folder_id]},
            media_body=media,
            fields="id,webViewLink",
            supportsAllDrives=True,
        ).execute()

        link = uploaded.get("webViewLink", "")
        logger.info("Uploaded to Drive: %s → %s", filename, link)
        return link
    except Exception as e:
        logger.error("Drive upload failed for %s: %s", filename, e, exc_info=True)
        return ""
