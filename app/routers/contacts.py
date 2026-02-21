"""
Contacts management endpoints — ground team overrides and notes.
"""
import logging

from fastapi import APIRouter, HTTPException

from app.db import get_cursor
from app.models import ContactNotes, ContactPriority

logger = logging.getLogger(__name__)
router = APIRouter()

VALID_PRIORITIES = {"none", "high", "do_not_contact"}


@router.patch("/{contact_id}/priority")
def set_priority_override(contact_id: int, payload: ContactPriority):
    """Set a per-customer priority override for the agent pipeline.

    - none         — default, agents decide normally
    - high         — treat as hot priority, prefer escalation/sms over none
    - do_not_contact — skip entire agent cycle, no automated outreach
    """
    if payload.priority_override not in VALID_PRIORITIES:
        raise HTTPException(
            status_code=422,
            detail=f"priority_override must be one of: {sorted(VALID_PRIORITIES)}",
        )
    with get_cursor() as cur:
        cur.execute(
            "UPDATE contacts SET priority_override = %s, updated_at = now() WHERE id = %s",
            (payload.priority_override, contact_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Contact not found")
    logger.info("priority_override set: contact_id=%s → %s", contact_id, payload.priority_override)
    return {"contact_id": contact_id, "priority_override": payload.priority_override}


@router.patch("/{contact_id}/notes")
def set_sales_notes(contact_id: int, payload: ContactNotes):
    """Set ground team sales notes for a customer — injected into all agent prompts."""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE contacts SET sales_notes = %s, updated_at = now() WHERE id = %s",
            (payload.sales_notes, contact_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Contact not found")
    logger.info("sales_notes updated: contact_id=%s (len=%d)", contact_id, len(payload.sales_notes))
    return {"contact_id": contact_id, "sales_notes": payload.sales_notes}
