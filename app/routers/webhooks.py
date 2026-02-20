"""
Inbound webhooks — currently handles Instantly email events.

Configure in Instantly:
  Settings → Integrations → Webhooks → Add webhook URL:
  https://<your-domain>/api/webhooks/instantly

Supported events: email_opened, email_replied, email_bounced, email_unsubscribed
"""
import logging

from fastapi import APIRouter, Request

from app.db import get_cursor
from app.routers.prospects import _upsert_contact

logger = logging.getLogger(__name__)

router = APIRouter()


def _extract_lead(payload: dict) -> dict:
    """
    Normalise Instantly webhook payload into a simple lead dict.
    Instantly may nest lead fields directly or under a 'lead' key.
    """
    lead = payload.get("lead") or {}
    return {
        "email":      (lead.get("email")      or payload.get("lead_email")      or "").strip().lower(),
        "first_name": (lead.get("first_name") or payload.get("lead_first_name") or "").strip(),
        "last_name":  (lead.get("last_name")  or payload.get("lead_last_name")  or "").strip(),
        "phone":      (lead.get("phone")      or payload.get("lead_phone")      or "").strip(),
    }


@router.post("/instantly")
async def instantly_webhook(request: Request):
    """
    Receive Instantly webhook events.

    On every meaningful engagement (open, reply, click):
      1. Upsert the lead as a contact (noop if already exists)
      2. Record the engagement event
      3. Run lifecycle so the contact gets campaign-assigned immediately
    """
    try:
        payload = await request.json()
    except Exception:
        return {"status": "ignored", "reason": "non-JSON body"}

    event_type = (
        payload.get("event_type")
        or payload.get("eventType")
        or payload.get("type")
        or ""
    ).lower()

    campaign_id   = payload.get("campaign_id")   or payload.get("campaignId")   or ""
    campaign_name = payload.get("campaign_name") or payload.get("campaignName") or ""

    logger.info("Instantly webhook received: event=%s campaign=%s", event_type, campaign_name or campaign_id)

    # ── Ignore events we don't act on ───────────────────────────────────────
    actionable_events = {"email_opened", "email_replied", "link_clicked", "email_clicked",
                         "email_open", "email_reply", "email_click"}
    if event_type not in actionable_events:
        logger.debug("Instantly webhook: unhandled event_type=%s — ignored", event_type)
        return {"status": "ignored", "event_type": event_type}

    lead = _extract_lead(payload)
    if not lead["email"] and not lead["phone"]:
        logger.warning("Instantly webhook %s: no email or phone in payload — skipped", event_type)
        return {"status": "ignored", "reason": "no lead contact info"}

    # ── Upsert contact + run lifecycle ──────────────────────────────────────
    first_name = lead["first_name"] or "Unknown"
    try:
        with get_cursor(commit=True) as cur:
            contact_id, is_new = _upsert_contact(
                cur,
                first_name=first_name,
                last_name=lead["last_name"],
                phone=lead["phone"],
                email=lead["email"],
                address="",
            )
            logger.info(
                "Instantly webhook %s: contact_id=%s is_new=%s email=%s",
                event_type, contact_id, is_new, lead["email"],
            )

            # Record engagement event so lifecycle can react
            db_event = "email_click" if "click" in event_type or "replied" in event_type else "email_open"
            try:
                cur.execute(
                    "SELECT ingest_event(%s, %s::event_type, %s)",
                    (contact_id, db_event, f"Instantly: {event_type} — campaign: {campaign_name or campaign_id}")
                )
            except Exception as e:
                logger.warning("Could not record engagement event (non-fatal): %s", e)

            # Run lifecycle to assign/update campaign immediately
            lifecycle_result = {}
            try:
                cur.execute("SELECT * FROM run_lifecycle_cycle()")
                lc = cur.fetchone()
                lifecycle_result = {
                    "contacts_updated": lc["contacts_updated"] if lc else 0,
                    "campaigns_queued": lc["campaigns_queued"] if lc else 0,
                }
            except Exception as e:
                logger.warning("Lifecycle cycle after Instantly webhook failed: %s", e)
                lifecycle_result = {"error": str(e)}

    except Exception as e:
        logger.error("Instantly webhook processing failed: %s", e, exc_info=True)
        return {"status": "error", "detail": str(e)[:300]}

    return {
        "status": "ok",
        "event_type": event_type,
        "contact_id": contact_id,
        "is_new": is_new,
        "lifecycle": lifecycle_result,
    }
