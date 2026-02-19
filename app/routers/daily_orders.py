import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.db import get_cursor

router = APIRouter()


class DailyOrder(BaseModel):
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    order_id_external: Optional[str] = None
    order_date: Optional[str] = None  # YYYY-MM-DD; defaults to today
    source: str = "Website"
    total_amount: float = 0.0
    order_type: Optional[str] = None
    delivery_slot: Optional[str] = None
    notes: Optional[str] = None


class DailyOrderBatch(BaseModel):
    orders: list[DailyOrder]


def _resolve_email(phone: Optional[str], email: Optional[str]) -> Optional[str]:
    if email:
        return email.strip().lower()
    if not phone:
        return None
    normalized = "".join(c for c in phone if c.isdigit() or c == "+")
    with get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT email FROM contacts WHERE phone = %s OR phone = %s LIMIT 1",
            (phone, normalized),
        )
        row = cur.fetchone()
    return row["email"] if row else None


@router.post("/process")
def process_daily_orders(payload: DailyOrderBatch):
    """
    Batch-ingest daily order data as order_placed events.
    Called by the Daily Order Upload n8n workflow every day at 1 PM.
    After ingestion the agent cycle (POST /api/agents/cycle/run-all) should be
    triggered separately so the inference/decision agents can process new orders.
    """
    today = datetime.utcnow().strftime("%Y-%m-%d")
    ingested = 0
    skipped = []

    for order in payload.orders:
        email = _resolve_email(order.contact_phone, order.contact_email)
        if not email:
            skipped.append({
                "phone": order.contact_phone,
                "reason": "contact not found",
            })
            continue

        metadata = {
            "source": order.source,
            "total_amount": order.total_amount,
            "order_id_external": order.order_id_external or "",
            "order_date": order.order_date or today,
            "order_type": order.order_type or "",
            "delivery_slot": order.delivery_slot or "",
            "notes": order.notes or "",
        }

        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT ingest_event(%s, %s::event_type, %s::jsonb)",
                    (email, "order_placed", json.dumps(metadata)),
                )
            ingested += 1
        except Exception as e:
            skipped.append({"email": email, "reason": str(e)[:120]})

    return {
        "ingested": ingested,
        "skipped": len(skipped),
        "skipped_detail": skipped,
        "date": today,
    }
