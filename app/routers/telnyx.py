import json

from fastapi import APIRouter, HTTPException

from app.db import get_cursor
from app.models import IdResponse, TelnyxCallIn, TelnyxMessageIn

router = APIRouter()


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
