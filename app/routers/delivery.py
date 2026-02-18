import json

from fastapi import APIRouter, HTTPException

from app.db import get_cursor
from app.models import DeliveryStatusIn, IdResponse

router = APIRouter()


@router.post("/status", response_model=IdResponse)
def update_delivery_status(payload: DeliveryStatusIn):
    with get_cursor() as cur:
        try:
            cur.execute(
                "SELECT update_delivery_status(%s, %s, %s::delivery_status_type, %s, %s, %s, %s::jsonb)",
                (
                    payload.contact_email,
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
                raise HTTPException(status_code=404, detail=f"Contact not found: {payload.contact_email}")
            raise
