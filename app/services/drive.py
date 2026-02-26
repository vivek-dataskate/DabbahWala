"""Google Drive / Google Docs helpers.

Requires env vars:
  GOOGLE_SERVICE_ACCOUNT_JSON  — full JSON of the service account key file
  GOOGLE_DRIVE_FOLDER_ID       — Drive folder ID to upload files into

The service account email must be granted Editor access to that folder
and Viewer access to any Google Docs you want to read.
"""
import io
import json
import logging
import os

logger = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents.readonly",
]

# Default chatbot docs folder
_CHATBOT_FOLDER_ID = "1O0ES9uiDL6AWf9QMMYiyRUWGtymDjPF5"


def _get_drive_service():
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not sa_json:
        return None
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_info(
        json.loads(sa_json), scopes=_SCOPES
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _get_docs_service():
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not sa_json:
        return None
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_info(
        json.loads(sa_json), scopes=_SCOPES
    )
    return build("docs", "v1", credentials=creds, cache_discovery=False)


def upload_csv(csv_content: str, filename: str, folder_id: str | None = None) -> str:
    """Upload a CSV string to Google Drive. Returns webViewLink or empty string."""
    target_folder = folder_id or os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    service = _get_drive_service()

    if not service or not target_folder:
        logger.debug("Drive upload skipped — env vars not configured")
        return ""

    try:
        from googleapiclient.http import MediaIoBaseUpload
        media = MediaIoBaseUpload(
            io.BytesIO(csv_content.encode("utf-8")),
            mimetype="text/csv",
            resumable=False,
        )
        uploaded = service.files().create(
            body={"name": filename, "parents": [target_folder]},
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


def list_folder_files(folder_id: str | None = None) -> dict:
    """List files in a Drive folder. Returns {"files": [...]} with id/name/mimeType/modifiedTime."""
    target_folder = folder_id or os.environ.get("GOOGLE_DRIVE_FOLDER_ID", _CHATBOT_FOLDER_ID).strip()
    service = _get_drive_service()

    if not service:
        logger.debug("Drive list skipped — GOOGLE_SERVICE_ACCOUNT_JSON not configured")
        return {"files": []}

    try:
        query = f"'{target_folder}' in parents and trashed = false"
        fields = "files(id,name,mimeType,modifiedTime,lastModifyingUser)"
        result = service.files().list(q=query, fields=fields, pageSize=100).execute()
        files = result.get("files", [])
        logger.info("Drive list: %d files in folder %s", len(files), target_folder)
        return {"files": files}
    except Exception as e:
        logger.error("Drive list failed for folder %s: %s", target_folder, e, exc_info=True)
        return {"files": []}


def read_doc(doc_id: str) -> dict:
    """Read a Google Doc and return its plain-text content."""
    service = _get_docs_service()

    if not service:
        logger.debug("Docs read skipped — GOOGLE_SERVICE_ACCOUNT_JSON not configured")
        return {"id": doc_id, "title": "", "text": ""}

    try:
        doc = service.documents().get(documentId=doc_id).execute()
        title = doc.get("title", "")

        # Extract plain text from document body
        text_parts = []
        for block in doc.get("body", {}).get("content", []):
            if "paragraph" in block:
                for el in block["paragraph"].get("elements", []):
                    text_run = el.get("textRun", {})
                    if text_run.get("content"):
                        text_parts.append(text_run["content"])

        text = "".join(text_parts).strip()
        logger.info("Read Google Doc %s (%d chars)", doc_id, len(text))
        return {"id": doc_id, "title": title, "text": text}
    except Exception as e:
        logger.error("Docs read failed for %s: %s", doc_id, e, exc_info=True)
        return {"id": doc_id, "title": "", "text": ""}
