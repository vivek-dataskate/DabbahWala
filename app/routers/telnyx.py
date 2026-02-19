import json

from fastapi import APIRouter, HTTPException

from app.db import get_cursor
from app.models import FieldAgentSmsIn, IdResponse, TelnyxCallIn, TelnyxMessageIn

router = APIRouter()


def _resolve_email(phone: str | None, email: str | None) -> str:
    """Resolve contact email from phone or email. Field agents know phone, not email."""
    if email:
        return email.strip().lower()
    if not phone:
        raise HTTPException(status_code=400, detail="contact_phone or contact_email is required")
    normalized = "".join(c for c in phone if c.isdigit() or c == "+")
    with get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT email FROM contacts WHERE phone = %s OR phone = %s LIMIT 1",
            (phone, normalized),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Contact not found for phone: {phone}")
    return row["email"]


@router.post("/message", response_model=IdResponse)
def store_message(payload: TelnyxMessageIn):
    with get_cursor() as cur:
        try:
            cur.execute(
                "SELECT store_telnyx_message(%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
                (
                    payload.contact_email,
                    payload.direction,
                    payload.from_number,
                    payload.to_number,
                    payload.body,
                    payload.telnyx_msg_id,
                    payload.status,
                    payload.is_delivery_staff,
                    json.dumps(payload.metadata),
                ),
            )
            row = cur.fetchone()
            return IdResponse(id=row["store_telnyx_message"])
        except Exception as e:
            if "Contact not found" in str(e):
                raise HTTPException(status_code=404, detail=f"Contact not found: {payload.contact_email}")
            raise


@router.post("/call", response_model=IdResponse)
def store_call(payload: TelnyxCallIn):
    with get_cursor() as cur:
        try:
            cur.execute(
                "SELECT store_telnyx_call(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)",
                (
                    payload.contact_email,
                    payload.direction,
                    payload.from_number,
                    payload.to_number,
                    payload.duration_sec,
                    payload.recording_url,
                    payload.transcript,
                    payload.summary,
                    payload.is_delivery_staff,
                    json.dumps(payload.metadata),
                    payload.started_at,
                    payload.ended_at,
                ),
            )
            row = cur.fetchone()
            return IdResponse(id=row["store_telnyx_call"])
        except Exception as e:
            if "Contact not found" in str(e):
                raise HTTPException(status_code=404, detail=f"Contact not found: {payload.contact_email}")
            raise


@router.post("/field-agent-message", response_model=IdResponse)
def log_field_agent_sms(payload: FieldAgentSmsIn):
    """
    Log an SMS sent by a field agent from their personal phone.
    Stored in telnyx_messages with source='field_agent' so the inference
    agents see it in communication history alongside Telnyx-automated messages.
    Also fires an sms_sent event for the lifecycle engine.
    """
    email = _resolve_email(payload.contact_phone, payload.contact_email)
    metadata = {"notes": payload.notes or ""}
    with get_cursor() as cur:
        try:
            cur.execute(
                """SELECT store_telnyx_message(
                    %s, 'outbound', %s, %s, %s,
                    NULL, 'sent', false, %s::jsonb,
                    'field_agent', %s, %s
                )""",
                (
                    email,
                    payload.agent_name,       # from_number = agent identifier
                    payload.contact_phone or "",
                    payload.body,
                    json.dumps(metadata),
                    payload.agent_name,
                    payload.sent_at,
                ),
            )
            row = cur.fetchone()
            return IdResponse(id=row["store_telnyx_message"])
        except Exception as e:
            if "Contact not found" in str(e):
                raise HTTPException(status_code=404, detail=f"Contact not found: {email}")
            raise
