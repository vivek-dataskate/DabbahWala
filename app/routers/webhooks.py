"""
Inbound webhooks — currently handles Instantly email events.

Configure in Instantly:
  Settings → Integrations → Webhooks → Add webhook URL:
  https://<your-domain>/api/webhooks/instantly

Supported events: email_opened, email_replied, email_bounced, email_unsubscribed

Only events from DabbahWala campaigns are processed. Campaign IDs are resolved by:
  1. The hardcoded set in _CAMPAIGN_META (campaigns.py) — always present.
  2. Instantly API tag search for "dabbahwala" (case-insensitive) — cached 6 hours.
"""
import logging
import time

import httpx
from fastapi import APIRouter, Request

from app.config import INSTANTLY_API_KEY
from app.db import get_cursor
from app.routers.campaigns import _CAMPAIGN_META
from app.routers.prospects import _upsert_contact

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Hardcoded DabbahWala campaign IDs (always trusted) ──────────────────────
_HARDCODED_IDS: set[str] = {meta["instantly_id"] for meta in _CAMPAIGN_META.values()}

# ── Tag-based campaign ID cache ──────────────────────────────────────────────
_TAG_CACHE: dict = {"ids": set(), "fetched_at": 0.0}
_TAG_CACHE_TTL = 6 * 3600  # 6 hours


def _fetch_campaign_ids_by_tag() -> set[str]:
    """
    Query Instantly API for all campaigns whose tags include 'dabbahwala'
    (case-insensitive). Returns a set of campaign IDs.
    Falls back to empty set on any error.
    """
    if not INSTANTLY_API_KEY:
        return set()
    try:
        resp = httpx.get(
            "https://api.instantly.ai/api/v2/campaigns",
            headers={"Authorization": f"Bearer {INSTANTLY_API_KEY}"},
            params={"limit": 100},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        campaigns = data if isinstance(data, list) else data.get("items") or data.get("campaigns") or []
        ids = set()
        for c in campaigns:
            tags = c.get("tags") or []
            # tags may be list of strings or list of dicts with a 'name' key
            tag_names = []
            for t in tags:
                if isinstance(t, str):
                    tag_names.append(t)
                elif isinstance(t, dict):
                    tag_names.append(t.get("name") or t.get("label") or "")
            if any(tag.strip().lower() == "dabbahwala" for tag in tag_names):
                cid = c.get("id") or c.get("campaign_id") or ""
                if cid:
                    ids.add(str(cid))
        logger.info("Instantly tag search found %d DabbahWala campaign(s)", len(ids))
        return ids
    except Exception as e:
        logger.warning("Instantly tag-based campaign fetch failed: %s", e)
        return set()


def _dabbahwala_campaign_ids() -> set[str]:
    """Return union of hardcoded IDs + tag-discovered IDs (cached 6 h)."""
    now = time.time()
    if now - _TAG_CACHE["fetched_at"] > _TAG_CACHE_TTL:
        _TAG_CACHE["ids"] = _fetch_campaign_ids_by_tag()
        _TAG_CACHE["fetched_at"] = now
    return _HARDCODED_IDS | _TAG_CACHE["ids"]


# ── Payload helpers ──────────────────────────────────────────────────────────

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


# ── Webhook endpoint ─────────────────────────────────────────────────────────

@router.post("/instantly")
async def instantly_webhook(request: Request):
    """
    Receive Instantly webhook events.

    Filters to DabbahWala campaigns only (by hardcoded IDs + 'dabbahwala' tag).
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

    campaign_id   = str(payload.get("campaign_id")   or payload.get("campaignId")   or "").strip()
    campaign_name =    (payload.get("campaign_name") or payload.get("campaignName") or "")

    logger.info("Instantly webhook received: event=%s campaign_id=%s campaign=%s",
                event_type, campaign_id, campaign_name)

    # ── Filter: only process DabbahWala campaigns ────────────────────────────
    if campaign_id:
        known_ids = _dabbahwala_campaign_ids()
        if campaign_id not in known_ids:
            logger.info(
                "Instantly webhook: campaign_id=%s not a DabbahWala campaign — ignored "
                "(known=%d hardcoded + tag-discovered)",
                campaign_id, len(known_ids),
            )
            return {"status": "ignored", "reason": "not a DabbahWala campaign", "campaign_id": campaign_id}
    else:
        # No campaign_id in payload — allow through (can't filter) but log it
        logger.warning("Instantly webhook: no campaign_id in payload — processing anyway")

    # ── Filter: only actionable event types ─────────────────────────────────
    actionable_events = {"email_opened", "email_replied", "link_clicked", "email_clicked",
                         "email_open", "email_reply", "email_click"}
    if event_type not in actionable_events:
        logger.debug("Instantly webhook: unhandled event_type=%s — ignored", event_type)
        return {"status": "ignored", "event_type": event_type}

    lead = _extract_lead(payload)
    if not lead["email"] and not lead["phone"]:
        logger.warning("Instantly webhook %s: no email or phone in payload — skipped", event_type)
        return {"status": "ignored", "reason": "no lead contact info"}

    # ── Upsert contact + run lifecycle ───────────────────────────────────────
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
