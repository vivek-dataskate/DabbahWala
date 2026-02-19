import json

from fastapi import APIRouter, HTTPException

from app.db import get_cursor
from app.models import DeliveryStatusIn, IdResponse

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


@router.post("/status", response_model=IdResponse)
def update_delivery_status(payload: DeliveryStatusIn):
    email = _resolve_email(payload.contact_email, payload.contact_phone)
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
            return IdResponse(id=row["update_delivery_status"])
        except Exception as e:
            if "Contact not found" in str(e):
                raise HTTPException(status_code=404, detail=f"Contact not found: {email}")
            raise
