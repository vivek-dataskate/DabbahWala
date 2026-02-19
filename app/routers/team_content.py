"""
Team Content — Google Docs ingestion and team knowledge management.

Content types:
  - ground_note:  Field team observations from delivery drivers, kitchen notes
  - ad_copy:      Social media daily ad copies (Facebook, Instagram posts)
  - observation:  General team observations submitted via query form
  - question:     Questions from team submitted via form

Sync flows:
  n8n (Google Drive poll) -> POST /api/team-content/sync -> Postgres
  n8n (Form submission)   -> POST /api/team-content/submit -> Postgres + Airtable
"""
from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.db import get_cursor

router = APIRouter()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class TeamContentSubmission(BaseModel):
    author: str | None = None
    content_type: str = "observation"  # ground_note, ad_copy, observation, question
    content: str
    title: str | None = None


class TeamContentSearchRequest(BaseModel):
    content_type: str | None = None
    search_query: str | None = None
    limit: int = 20


# ---------------------------------------------------------------------------
# Sync endpoint — called by n8n Google Docs workflow
# ---------------------------------------------------------------------------
@router.post("/sync")
async def sync_from_google_docs(request: Request):
    """
    Sync team content from Google Docs.
    n8n calls this with documents extracted from a Google Drive folder.

    Body: { "documents": [{ google_doc_id, content_type, title, body, author,
                             google_last_modified, doc_url, tags }] }
    """
    body = await request.json()
    documents = body.get("documents", [])

    created = 0
    updated = 0
    skipped = 0

    with get_cursor(commit=True) as cur:
        for doc in documents:
            gdoc_id = doc.get("google_doc_id")
            if not gdoc_id:
                skipped += 1
                continue

            title = (doc.get("title") or "").strip()
            content_body = (doc.get("body") or "").strip()
            if not content_body:
                skipped += 1
                continue

            content_type = doc.get("content_type", "ground_note")
            author = doc.get("author")
            google_last_modified = doc.get("google_last_modified")
            doc_url = doc.get("doc_url")
            tags = doc.get("tags", [])

            # Check if already synced
            cur.execute(
                "SELECT id, google_last_modified FROM team_content WHERE google_doc_id = %s",
                (gdoc_id,)
            )
            existing = cur.fetchone()

            if existing:
                # Only update if the doc has been modified since last sync
                if google_last_modified and existing.get("google_last_modified"):
                    existing_ts = str(existing["google_last_modified"])
                    if google_last_modified <= existing_ts:
                        skipped += 1
                        continue

                cur.execute(
                    """UPDATE team_content
                       SET title = %s, body = %s, author = %s,
                           google_last_modified = %s, doc_url = %s,
                           tags = %s, updated_at = now()
                       WHERE google_doc_id = %s""",
                    (title, content_body, author, google_last_modified,
                     doc_url, tags, gdoc_id)
                )
                updated += 1
            else:
                cur.execute(
                    """INSERT INTO team_content
                       (google_doc_id, content_type, title, body, author,
                        source, google_last_modified, doc_url, tags)
                       VALUES (%s, %s, %s, %s, %s, 'google_docs', %s, %s, %s)""",
                    (gdoc_id, content_type, title, content_body, author,
                     google_last_modified, doc_url, tags)
                )
                created += 1

    return {
        "synced": len(documents),
        "created": created,
        "updated": updated,
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# Form submission endpoint — user submits observations via query form
# ---------------------------------------------------------------------------
@router.post("/submit")
def submit_team_input(submission: TeamContentSubmission):
    """
    Store a team member's observation/note submitted via the query form.
    n8n will also forward this to Airtable "Team Inputs" table.
    """
    title = submission.title or (
        f"{submission.content_type.replace('_', ' ').title()} — "
        f"{datetime.utcnow().strftime('%b %d %Y')}"
    )

    with get_cursor(commit=True) as cur:
        cur.execute(
            """INSERT INTO team_content
               (content_type, title, body, author, source)
               VALUES (%s, %s, %s, %s, 'form_submission')
               RETURNING id""",
            (submission.content_type, title, submission.content,
             submission.author or "Anonymous")
        )
        row = cur.fetchone()

    return {
        "id": row["id"],
        "status": "stored",
        "content_type": submission.content_type,
        "title": title,
    }


# ---------------------------------------------------------------------------
# Browse / search endpoints
# ---------------------------------------------------------------------------
@router.get("/browse")
def browse_team_content(
    content_type: str | None = None,
    days: int = 30,
    limit: int = 25,
):
    """Browse recent team content by type."""
    with get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT * FROM get_recent_team_content(%s, %s, %s)",
            (content_type, days, limit)
        )
        results = [dict(r) for r in cur.fetchall()]

    return {"count": len(results), "content": results}


@router.post("/search")
def search_team_content(req: TeamContentSearchRequest):
    """Search team content with full-text search."""
    with get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT * FROM search_team_content(%s, %s, %s, 0)",
            (req.content_type, req.search_query, req.limit)
        )
        results = [dict(r) for r in cur.fetchall()]

    return {"count": len(results), "results": results}
