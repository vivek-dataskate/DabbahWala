from fastapi import APIRouter, HTTPException

from app.db import get_cursor
from app.models import IdResponse, OpportunityCreate, OpportunityDispatched, OpportunityOutcome

router = APIRouter()


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
