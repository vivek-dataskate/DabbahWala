import json

from fastapi import APIRouter, HTTPException

from app.db import get_cursor
from app.models import IdResponse, TelnyxCallIn, TelnyxMessageIn

router = APIRouter()


@router.post("/message", response_model=IdResponse)
def store_message(payload: TelnyxMessageIn):
    with get_cursor() as cur:
        # Resolve contact_id from email
        cur.execute("SELECT id FROM contacts WHERE email = %s", (payload.contact_email,))
        contact = cur.fetchone()
        if not contact:
            raise HTTPException(status_code=404, detail=f"Contact not found: {payload.contact_email}")

        cur.execute(
            """
            INSERT INTO telnyx_messages
                (contact_id, direction, from_number, to_number, body,
                 telnyx_msg_id, status, is_delivery_staff, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                contact["id"],
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

        # Also insert as event for rollup tracking
        event_type = "sms_received" if payload.direction == "inbound" else "sms_sent"
        cur.execute(
            "INSERT INTO events (contact_id, event_type, metadata) VALUES (%s, %s::event_type, %s::jsonb)",
            (contact["id"], event_type, json.dumps({"telnyx_msg_id": payload.telnyx_msg_id})),
        )

        return IdResponse(id=row["id"])


@router.post("/call", response_model=IdResponse)
def store_call(payload: TelnyxCallIn):
    with get_cursor() as cur:
        cur.execute("SELECT id FROM contacts WHERE email = %s", (payload.contact_email,))
        contact = cur.fetchone()
        if not contact:
            raise HTTPException(status_code=404, detail=f"Contact not found: {payload.contact_email}")

        cur.execute(
            """
            INSERT INTO telnyx_calls
                (contact_id, direction, from_number, to_number, duration_sec,
                 recording_url, transcript, summary, is_delivery_staff, metadata,
                 started_at, ended_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                contact["id"],
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

        # Insert call_completed event
        cur.execute(
            "INSERT INTO events (contact_id, event_type, metadata) VALUES (%s, 'call_completed'::event_type, %s::jsonb)",
            (contact["id"], json.dumps({"call_id": row["id"], "duration_sec": payload.duration_sec})),
        )

        return IdResponse(id=row["id"])
