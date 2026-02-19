import json
import logging

from fastapi import APIRouter, HTTPException

from app.db import get_cursor
from app.models import EventIngest, EventResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _resolve_email(contact_email: str | None, contact_phone: str | None) -> str:
    """Return the contact's email, resolving from phone if email is absent."""
    if contact_email:
        return contact_email
    if not contact_phone:
        raise HTTPException(status_code=400, detail="contact_email or contact_phone is required")
    normalized = "".join(c for c in contact_phone if c.isdigit() or c == "+")
    with get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT email FROM contacts WHERE phone = %s OR phone = %s LIMIT 1",
            (contact_phone, normalized),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Contact not found for phone: {contact_phone}")
    return row["email"]


@router.post("/ingest", response_model=EventResponse)
def ingest_event(payload: EventIngest):
    email = _resolve_email(payload.contact_email, payload.contact_phone)
    logger.info("ingest_event: type=%s email=%s", payload.event_type, email)
    with get_cursor() as cur:
        try:
            cur.execute(
                "SELECT ingest_event(%s, %s::event_type, %s::jsonb)",
                (email, payload.event_type, str(payload.metadata).replace("'", '"')),
            )
            row = cur.fetchone()
            event_id = row["ingest_event"]
            logger.debug("ingest_event: stored id=%s type=%s email=%s", event_id, payload.event_type, email)
            return EventResponse(event_id=event_id)
        except Exception as e:
            error_msg = str(e)
            if "Contact not found" in error_msg:
                logger.warning("ingest_event: contact not found email=%s", email)
                raise HTTPException(status_code=404, detail=error_msg)
            logger.error("ingest_event: error type=%s email=%s: %s", payload.event_type, email, e, exc_info=True)
            raise HTTPException(status_code=400, detail=error_msg)
