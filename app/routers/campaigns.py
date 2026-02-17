from fastapi import APIRouter

from app.db import get_cursor
from app.models import CampaignMove

router = APIRouter()


@router.get("/pending", response_model=list[CampaignMove])
def get_pending_campaigns():
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT * FROM get_pending_campaign_moves()")
        rows = cur.fetchall()
        return [
            CampaignMove(
                queue_id=r["queue_id"],
                contact_email=r["contact_email"],
                contact_phone=r["contact_phone"],
                from_campaign=r["from_campaign"],
                to_campaign=r["to_campaign"],
            )
            for r in rows
        ]


@router.post("/{queue_id}/executed")
def mark_executed(queue_id: int):
    with get_cursor() as cur:
        cur.execute("SELECT mark_campaign_executed(%s)", (queue_id,))
        return {"status": "ok"}
