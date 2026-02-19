import json

from fastapi import APIRouter, HTTPException

from app.db import get_cursor
from app.models import EventIngest, EventResponse

router = APIRouter()


@router.post("/ingest", response_model=EventResponse)
def ingest_event(payload: EventIngest):
    with get_cursor() as cur:
        try:
            cur.execute(
                "SELECT ingest_event(%s, %s::event_type, %s::jsonb)",
                (payload.contact_email, payload.event_type, json.dumps(payload.metadata)),
            )
            row = cur.fetchone()
            return EventResponse(event_id=row["ingest_event"])
        except Exception as e:
            error_msg = str(e)
            if "Contact not found" in error_msg:
                raise HTTPException(status_code=404, detail=error_msg)
            raise HTTPException(status_code=400, detail=error_msg)
