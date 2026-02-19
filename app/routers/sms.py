import logging

from fastapi import APIRouter

from app.db import get_cursor
from app.models import SmsPending

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/pending", response_model=list[SmsPending])
def get_pending_sms():
    logger.debug("GET /sms/pending — fetching pending SMS contacts")
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT * FROM get_pending_sms()")
        rows = cur.fetchall()
        result = [
            SmsPending(
                contact_id=r["contact_id"],
                contact_email=r["contact_email"],
                phone=r["phone"],
                sms_level=r["sms_level"],
                lifecycle=r["lifecycle"],
            )
            for r in rows
        ]
        logger.info("GET /sms/pending — returned %d contacts", len(result))
        return result


@router.post("/{contact_id}/sent")
def mark_sms_sent(contact_id: int):
    logger.info("POST /sms/%d/sent — marking SMS sent", contact_id)
    with get_cursor() as cur:
        cur.execute("SELECT mark_sms_sent(%s)", (contact_id,))
        logger.debug("mark_sms_sent OK contact_id=%d", contact_id)
        return {"status": "ok"}
