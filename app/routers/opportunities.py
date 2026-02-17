from fastapi import APIRouter, HTTPException

from app.db import get_cursor
from app.models import IdResponse, OpportunityCreate, OpportunityDispatched, OpportunityOutcome

router = APIRouter()


@router.post("", response_model=IdResponse)
def create_opportunity(payload: OpportunityCreate):
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO opportunities
                (contact_id, action, priority, reason, suggested_message, confidence_score)
            VALUES (%s, %s::opportunity_action, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                payload.contact_id,
                payload.action,
                payload.priority,
                payload.reason,
                payload.suggested_message,
                payload.confidence_score,
            ),
        )
        row = cur.fetchone()
        return IdResponse(id=row["id"])


@router.get("/pending")
def get_pending_opportunities():
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT o.id, o.contact_id, o.action, o.priority, o.reason,
                   o.suggested_message, o.confidence_score,
                   c.email, c.phone, c.first_name, c.last_name,
                   c.lifecycle_segment, c.total_orders, c.last_order_at
            FROM opportunities o
            JOIN contacts c ON c.id = o.contact_id
            WHERE o.status = 'pending'
            ORDER BY
                CASE o.priority WHEN 'hot' THEN 1 WHEN 'warm' THEN 2 ELSE 3 END,
                o.created_at
            """
        )
        return [dict(r) for r in cur.fetchall()]


@router.post("/{opportunity_id}/dispatched")
def mark_dispatched(opportunity_id: int, payload: OpportunityDispatched):
    with get_cursor() as cur:
        cur.execute(
            """
            UPDATE opportunities
            SET status = 'dispatched', airtable_record_id = %s, dispatched_at = now()
            WHERE id = %s AND status = 'pending'
            """,
            (payload.airtable_record_id, opportunity_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Opportunity not found or not pending")
        return {"status": "dispatched"}


@router.post("/{opportunity_id}/outcome")
def update_outcome(opportunity_id: int, payload: OpportunityOutcome):
    with get_cursor() as cur:
        cur.execute(
            """
            UPDATE opportunities
            SET status = %s::opportunity_status, outcome = %s, completed_at = now()
            WHERE id = %s
            """,
            (payload.status, payload.outcome, opportunity_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Opportunity not found")
        return {"status": payload.status}
