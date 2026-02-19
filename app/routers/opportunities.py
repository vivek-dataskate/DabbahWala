import logging

from fastapi import APIRouter, HTTPException

from app.db import get_cursor
from app.models import IdResponse, OpportunityCreate, OpportunityDispatched, OpportunityOutcome

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Detection endpoints — called by n8n opportunity_detection_cron workflow
# ---------------------------------------------------------------------------
@router.get("/detect")
def detect_engaged_no_order():
    """Detect contacts with engagement (opens/clicks) but no recent order."""
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT * FROM detect_engaged_no_order()")
        candidates = [dict(r) for r in cur.fetchall()]
        for c in candidates:
            c['signal'] = 'engaged_no_order'
            c['suggested_action'] = 'send_email'
            c['suggested_priority'] = 'warm'
        return {"count": len(candidates), "candidates": candidates}


@router.get("/detect/new-customer-no-repeat")
def detect_new_customer_no_repeat():
    """Detect new customers who haven't reordered after 5+ days."""
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT * FROM detect_new_customer_no_repeat()")
        candidates = [dict(r) for r in cur.fetchall()]
        for c in candidates:
            c['signal'] = 'new_customer_no_repeat'
            c['suggested_action'] = 'send_sms'
            c['suggested_priority'] = 'warm'
        return {"count": len(candidates), "candidates": candidates}


@router.get("/detect/lapsed-reengaged")
def detect_lapsed_reengaged():
    """Detect lapsed customers who recently re-engaged."""
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT * FROM detect_lapsed_reengaged()")
        candidates = [dict(r) for r in cur.fetchall()]
        for c in candidates:
            c['signal'] = 'lapsed_reengaged'
            c['suggested_action'] = 'field_sales_call'
            c['suggested_priority'] = 'hot'
        return {"count": len(candidates), "candidates": candidates}


@router.get("/detect/reorder-intent")
def detect_reorder_intent():
    """Detect reorder intent from call transcripts."""
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT * FROM detect_reorder_intent()")
        candidates = [dict(r) for r in cur.fetchall()]
        for c in candidates:
            c['signal'] = 'reorder_intent'
            c['suggested_action'] = 'send_sms'
            c['suggested_priority'] = 'hot'
        return {"count": len(candidates), "candidates": candidates}


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------
@router.post("", response_model=IdResponse)
def create_opportunity(payload: OpportunityCreate):
    with get_cursor() as cur:
        cur.execute(
            "SELECT create_opportunity(%s, %s::opportunity_action, %s, %s, %s, %s)",
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
        return IdResponse(id=row["create_opportunity"])


@router.get("/pending")
def get_pending_opportunities():
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT * FROM get_pending_opportunities()")
        return [dict(r) for r in cur.fetchall()]


@router.post("/{opportunity_id}/dispatched")
def mark_dispatched(opportunity_id: int, payload: OpportunityDispatched):
    with get_cursor() as cur:
        cur.execute(
            "SELECT mark_opportunity_dispatched(%s, %s)",
            (opportunity_id, payload.airtable_record_id),
        )
        found = cur.fetchone()["mark_opportunity_dispatched"]
        if not found:
            raise HTTPException(status_code=404, detail="Opportunity not found or not pending")
        return {"status": "dispatched"}


@router.post("/{opportunity_id}/outcome")
def update_outcome(opportunity_id: int, payload: OpportunityOutcome):
    with get_cursor() as cur:
        cur.execute(
            "SELECT update_opportunity_outcome(%s, %s::opportunity_status, %s)",
            (opportunity_id, payload.status, payload.outcome),
        )
        found = cur.fetchone()["update_opportunity_outcome"]
        if not found:
            raise HTTPException(status_code=404, detail="Opportunity not found")
        return {"status": payload.status}
