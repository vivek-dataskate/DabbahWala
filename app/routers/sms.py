from fastapi import APIRouter

from app.db import get_cursor
from app.models import SmsPending

router = APIRouter()


@router.get("/pending", response_model=list[SmsPending])
def get_pending_sms():
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT * FROM get_pending_sms()")
        rows = cur.fetchall()
        return [
            SmsPending(
                contact_id=r["contact_id"],
                contact_email=r["contact_email"],
                phone=r["phone"],
                sms_level=r["sms_level"],
                lifecycle=r["lifecycle"],
            )
            for r in rows
        ]


@router.post("/{contact_id}/sent")
def mark_sms_sent(contact_id: int):
    with get_cursor() as cur:
        cur.execute("SELECT mark_sms_sent(%s)", (contact_id,))
        return {"status": "ok"}
