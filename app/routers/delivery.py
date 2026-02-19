import json
import logging

from fastapi import APIRouter, HTTPException

from app.db import get_cursor
from app.models import DeliveryStatusIn, IdResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def _resolve_email(contact_email: str | None, contact_phone: str | None) -> str:
    """Return the contact's email, resolving from phone if email is absent."""
    if contact_email:
        return contact_email
    if not contact_phone:
        logger.error("Delivery status update missing both contact_email and contact_phone")
        raise HTTPException(status_code=400, detail="contact_email or contact_phone is required")
    normalized = "".join(c for c in contact_phone if c.isdigit() or c == "+")
    logger.debug("Resolving email for phone=%s (normalized=%s)", contact_phone, normalized)
    with get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT email FROM contacts WHERE phone = %s OR phone = %s LIMIT 1",
            (contact_phone, normalized),
        )
        row = cur.fetchone()
    if not row:
        logger.warning("No contact found for phone=%s (normalized=%s)", contact_phone, normalized)
        raise HTTPException(status_code=404, detail=f"Contact not found for phone: {contact_phone}")
    logger.debug("Resolved phone=%s to email=%s", contact_phone, row["email"])
    return row["email"]


@router.post("/status", response_model=IdResponse)
def update_delivery_status(payload: DeliveryStatusIn):
    email = _resolve_email(payload.contact_email, payload.contact_phone)
    logger.info(
        "Delivery status update: email=%s order_ref=%s status=%s updated_by=%s",
        email,
        payload.order_ref,
        payload.status,
        payload.updated_by,
    )
    with get_cursor() as cur:
        try:
            cur.execute(
                "SELECT update_delivery_status(%s, %s, %s::delivery_status_type, %s, %s, %s, %s::jsonb)",
                (
                    email,
                    payload.order_ref,
                    payload.status,
                    payload.updated_by,
                    payload.notes,
                    payload.location,
                    json.dumps(payload.metadata),
                ),
            )
            row = cur.fetchone()
            delivery_id = row["update_delivery_status"]
            logger.info(
                "Delivery status stored id=%s email=%s status=%s",
                delivery_id,
                email,
                payload.status,
            )
            return IdResponse(id=delivery_id)
        except Exception as e:
            if "Contact not found" in str(e):
                logger.error("Contact not found in DB for email=%s during delivery update", email)
                raise HTTPException(status_code=404, detail=f"Contact not found: {email}")
            logger.error("Failed to update delivery status for email=%s: %s", email, e, exc_info=True)
            raise
