import csv
import io
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, File, UploadFile
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


# Column name aliases accepted from uploaded CSVs
_COL_ALIASES = {
    "email":            ["email", "contact_email", "customer_email"],
    "phone":            ["phone", "contact_phone", "customer_phone", "mobile"],
    "order_id_external":["order_id", "order_id_external", "external_id", "shipday_id", "id"],
    "order_date":       ["order_date", "date", "order_date_str"],
    "source":           ["source", "order_source", "platform"],
    "total_amount":     ["total_amount", "total", "amount", "order_total", "price"],
    "order_type":       ["order_type", "type", "subscription_type"],
    "delivery_slot":    ["delivery_slot", "slot", "time_slot"],
    "notes":            ["notes", "note", "comments"],
}


def _find_col(row_keys: list, field: str) -> Optional[str]:
    lower = {k.strip().lower(): k for k in row_keys}
    for alias in _COL_ALIASES[field]:
        if alias.lower() in lower:
            return lower[alias.lower()]
    return None


@router.post("/upload-csv")
async def upload_csv(file: UploadFile = File(...)):
    """
    Upload a CSV of daily orders. Columns are matched flexibly (see _COL_ALIASES).
    Each row is converted to a DailyOrder and processed via process_daily_orders.
    """
    content = await file.read()
    text = content.decode("utf-8-sig")  # strip BOM if present
    reader = csv.DictReader(io.StringIO(text))

    orders: list[DailyOrder] = []
    parse_errors: list[dict] = []

    for i, row in enumerate(reader, start=2):  # row 1 = header
        keys = list(row.keys())

        def get(field: str) -> str:
            col = _find_col(keys, field)
            return row.get(col, "").strip() if col else ""

        total_str = get("total_amount")
        try:
            total = float(total_str) if total_str else 0.0
        except ValueError:
            total = 0.0
            parse_errors.append({"row": i, "issue": f"invalid total_amount '{total_str}', defaulted to 0"})

        orders.append(DailyOrder(
            contact_email=get("email") or None,
            contact_phone=get("phone") or None,
            order_id_external=get("order_id_external") or None,
            order_date=get("order_date") or None,
            source=get("source") or "Website",
            total_amount=total,
            order_type=get("order_type") or None,
            delivery_slot=get("delivery_slot") or None,
            notes=get("notes") or None,
        ))

    if not orders:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        return {"ingested": 0, "skipped": 0, "skipped_detail": [], "date": today,
                "message": "No data rows found in CSV"}

    result = process_daily_orders(DailyOrderBatch(orders=orders))
    result["parse_errors"] = parse_errors
    return result
