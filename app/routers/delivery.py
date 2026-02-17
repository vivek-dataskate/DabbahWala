import json

from fastapi import APIRouter, HTTPException

from app.db import get_cursor
from app.models import DeliveryStatusIn, IdResponse

router = APIRouter()


@router.post("/status", response_model=IdResponse)
def update_delivery_status(payload: DeliveryStatusIn):
    with get_cursor() as cur:
        cur.execute("SELECT id FROM contacts WHERE email = %s", (payload.contact_email,))
        contact = cur.fetchone()
        if not contact:
            raise HTTPException(status_code=404, detail=f"Contact not found: {payload.contact_email}")

        cur.execute(
            """
            INSERT INTO delivery_status
                (contact_id, order_ref, status, updated_by, notes, location, metadata)
            VALUES (%s, %s, %s::delivery_status_type, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                contact["id"],
                payload.order_ref,
                payload.status,
                payload.updated_by,
                payload.notes,
                payload.location,
                json.dumps(payload.metadata),
            ),
        )
        row = cur.fetchone()

        # Insert delivery_update event
        cur.execute(
            "INSERT INTO events (contact_id, event_type, metadata) VALUES (%s, 'delivery_update'::event_type, %s::jsonb)",
            (contact["id"], json.dumps({"delivery_status": payload.status, "order_ref": payload.order_ref})),
        )

        return IdResponse(id=row["id"])
