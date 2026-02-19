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
    Upload a CSV of daily orders. Columns are matched flexibly.
    After ingestion:
      1. Summarizes the data (revenue, by source, new vs returning).
      2. Runs the full inference→decision→orchestration cycle for each
         ingested contact (capped at 5 to stay within response time limits).
      3. Returns actionable field opportunities alongside the ingest stats.
    """
    from collections import Counter

    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    orders: list[DailyOrder] = []
    parse_errors: list[dict] = []

    for i, row in enumerate(reader, start=2):
        keys = list(row.keys())

        def get(field: str, _keys: list = keys, _row: dict = row) -> str:
            col = _find_col(_keys, field)
            return _row.get(col, "").strip() if col else ""

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

    today = datetime.utcnow().strftime("%Y-%m-%d")

    if not orders:
        return {"ingested": 0, "skipped": 0, "skipped_detail": [], "date": today,
                "message": "No data rows found in CSV"}

    # ── Step 1: Ingest & track ────────────────────────────────────────────────
    ingested = 0
    skipped: list[dict] = []
    ingested_emails: list[str] = []
    total_revenue = 0.0
    source_counter: Counter = Counter()

    for order in orders:
        email = _resolve_email(order.contact_phone, order.contact_email)
        if not email:
            skipped.append({"phone": order.contact_phone, "reason": "contact not found"})
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
            ingested_emails.append(email)
            total_revenue += order.total_amount or 0.0
            source_counter[order.source or "Website"] += 1
        except Exception as e:
            skipped.append({"email": email, "reason": str(e)[:120]})

    # ── Step 2: Identify new vs returning customers in this batch ─────────────
    new_customers = 0
    returning_customers = 0
    if ingested_emails:
        with get_cursor(commit=False) as cur:
            cur.execute(
                "SELECT email, total_orders FROM contacts WHERE email = ANY(%s)",
                (ingested_emails,),
            )
            for row in cur.fetchall():
                if row["total_orders"] <= 1:
                    new_customers += 1
                else:
                    returning_customers += 1

    upload_summary = {
        "total_revenue_aed": round(total_revenue, 2),
        "by_source": dict(source_counter),
        "new_customers": new_customers,
        "returning_customers": returning_customers,
    }

    # ── Step 3: Run inference cycle for up to 5 contacts ─────────────────────
    from app.routers.agents import _run_full_cycle  # local import avoids circular deps at module level

    contact_ids: list[int] = []
    if ingested_emails:
        with get_cursor(commit=False) as cur:
            cur.execute(
                "SELECT id, email FROM contacts WHERE email = ANY(%s) LIMIT 5",
                (ingested_emails[:5],),
            )
            contact_ids = [r["id"] for r in cur.fetchall()]

    agent_analysis: list[dict] = []
    for cid in contact_ids:
        try:
            result = _run_full_cycle(cid)
            agent_analysis.append(result)
        except Exception as e:
            agent_analysis.append({"contact_id": cid, "error": str(e)[:200]})

    # ── Step 4: Extract field opportunities (escalations + high-value SMS) ────
    field_opportunities = [
        {
            "contact_name": r.get("contact_name", "Unknown"),
            "action":       r.get("chosen_action", "none"),
            "channel":      r.get("chosen_channel", "none"),
            "reasoning":    (r.get("reasoning_snippet") or "")[:200],
        }
        for r in agent_analysis
        if isinstance(r, dict)
        and "error" not in r
        and r.get("chosen_action") in ("escalate_airtable", "send_sms")
    ]

    return {
        "ingested":          ingested,
        "skipped":           len(skipped),
        "skipped_detail":    skipped,
        "date":              today,
        "parse_errors":      parse_errors,
        "upload_summary":    upload_summary,
        "agent_analysis":    agent_analysis,
        "field_opportunities": field_opportunities,
        "note": (
            f"Inference ran for {len(contact_ids)} of {len(ingested_emails)} ingested contacts. "
            "Use 'Run Agent → cycle/run-all' to process remaining contacts."
            if len(ingested_emails) > 5 else ""
        ),
    }
