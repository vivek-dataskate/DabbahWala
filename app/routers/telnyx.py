import json
import logging
import threading

from fastapi import APIRouter, HTTPException

from app.db import get_cursor
from app.models import FieldAgentSmsIn, IdResponse, TelnyxCallIn, TelnyxMessageIn

logger = logging.getLogger(__name__)
router = APIRouter()


def _fire_agent_cycle(contact_email: str, trigger: str) -> None:
    """Look up contact_id by email and run the full agent cycle — non-blocking.

    Called after every SMS, call, or field agent message so the agent stack
    immediately reasons from the new communication evidence.
    """
    try:
        from app.routers.agents import _run_full_cycle  # lazy import avoids circular deps
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT id FROM contacts WHERE email = %s LIMIT 1", (contact_email,))
            row = cur.fetchone()
        if not row:
            logger.warning("Agent cycle skip: no contact found for email=%s trigger=%s", contact_email, trigger)
            return
        contact_id = row["id"]
        logger.info("Agent cycle triggered by %s for contact_id=%s email=%s", trigger, contact_id, contact_email)
        _run_full_cycle(contact_id)
    except Exception as e:
        logger.error(
            "Background agent cycle failed email=%s trigger=%s: %s",
            contact_email, trigger, e, exc_info=True,
        )


def _resolve_email(phone: str | None, email: str | None) -> str:
    """Resolve contact email from phone or email. Field agents know phone, not email."""
    if email:
        return email.strip().lower()
    if not phone:
        raise HTTPException(status_code=400, detail="contact_phone or contact_email is required")
    normalized = "".join(c for c in phone if c.isdigit() or c == "+")
    logger.debug("Resolving contact email from phone=%s", phone)
    with get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT email FROM contacts WHERE phone = %s OR phone = %s LIMIT 1",
            (phone, normalized),
        )
        row = cur.fetchone()
    if not row:
        logger.warning("Contact not found for phone=%s", phone)
        raise HTTPException(status_code=404, detail=f"Contact not found for phone: {phone}")
    return row["email"]


@router.post("/message", response_model=IdResponse)
def store_message(payload: TelnyxMessageIn):
    # For inbound SMS, we have from_number (customer phone) but not email — resolve it
    inbound_phone = payload.from_number if payload.direction == "inbound" else None
    contact_email = _resolve_email(payload.contact_phone or inbound_phone, payload.contact_email)
    logger.info(
        "store_message: dir=%s from=%s to=%s email=%s",
        payload.direction, payload.from_number, payload.to_number, contact_email,
    )
    with get_cursor() as cur:
        try:
            cur.execute(
                "SELECT store_telnyx_message(%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
                (
                    contact_email,
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
            msg_id = row["store_telnyx_message"]
            logger.info("store_message: stored id=%s email=%s", msg_id, contact_email)
        except Exception as e:
            if "Contact not found" in str(e):
                logger.warning("store_message: contact not found email=%s", contact_email)
                raise HTTPException(status_code=404, detail=f"Contact not found: {contact_email}")
            logger.error("store_message: unexpected error: %s", e, exc_info=True)
            raise

    # For inbound SMS only, fire agent immediately — customer replied, context is live
    # Outbound SMS is evidence stored; nightly cycle reads it with full context
    if payload.direction == "inbound":
        threading.Thread(
            target=_fire_agent_cycle,
            args=(contact_email, "sms_inbound"),
            daemon=True,
        ).start()
    return IdResponse(id=msg_id)


@router.post("/call", response_model=IdResponse)
def store_call(payload: TelnyxCallIn):
    # For inbound calls, from_number is the customer's phone — resolve to email
    inbound_phone = payload.from_number if payload.direction == "inbound" else None
    contact_email = _resolve_email(payload.contact_phone or inbound_phone, payload.contact_email)
    logger.info(
        "store_call: dir=%s from=%s to=%s email=%s dur=%s",
        payload.direction, payload.from_number, payload.to_number,
        contact_email, payload.duration_sec,
    )
    with get_cursor() as cur:
        try:
            cur.execute(
                "SELECT store_telnyx_call(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)",
                (
                    contact_email,
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
            call_id = row["store_telnyx_call"]
            logger.info("store_call: stored id=%s email=%s", call_id, contact_email)
        except Exception as e:
            if "Contact not found" in str(e):
                logger.warning("store_call: contact not found email=%s", contact_email)
                raise HTTPException(status_code=404, detail=f"Contact not found: {contact_email}")
            logger.error("store_call: unexpected error: %s", e, exc_info=True)
            raise

    # Call transcript stored — nightly cycle reads it with full evidence context
    return IdResponse(id=call_id)


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
            msg_id = row["store_telnyx_message"]
        except Exception as e:
            if "Contact not found" in str(e):
                raise HTTPException(status_code=404, detail=f"Contact not found: {email}")
            raise

    # Field agent SMS stored — nightly cycle reads it with full evidence context
    return IdResponse(id=msg_id)
