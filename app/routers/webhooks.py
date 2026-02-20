"""
Inbound webhooks — currently handles Instantly email events.

Configure in Instantly:
  Settings → Integrations → Webhooks → Add webhook URL:
  https://<your-domain>/api/webhooks/instantly

Campaign registry (instantly_campaigns table) is kept in sync by n8n calling:
  POST /api/webhooks/sync-campaigns   (schedule: every 6 h or as desired in n8n)

The webhook handler itself does only a fast DB lookup — no external API calls.
"""
import logging

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.config import INSTANTLY_API_KEY
from app.db import get_cursor
from app.routers.campaigns import _CAMPAIGN_META
from app.routers.prospects import _upsert_contact
from app.routers.shipday_historical import (
    shipday_webhook_ping as _shipday_ping,
    shipday_webhook as _shipday_webhook,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Hardcoded DabbahWala campaign IDs (always trusted) ──────────────────────
_HARDCODED_IDS: set[str] = {meta["instantly_id"] for meta in _CAMPAIGN_META.values()}


# ── DB helpers ───────────────────────────────────────────────────────────────

def _upsert_campaign_db(campaign_id: str, name: str = "", tags: list = None,
                        status: str = "", source: str = "tag_discovered") -> None:
    """Persist / refresh a campaign record in instantly_campaigns."""
    tags = tags or []
    try:
        with get_cursor(commit=True) as cur:
            cur.execute(
                """INSERT INTO instantly_campaigns
                       (campaign_id, campaign_name, tags, status, source, first_seen_at, last_seen_at)
                   VALUES (%s, %s, %s, %s, %s, now(), now())
                   ON CONFLICT (campaign_id) DO UPDATE SET
                       campaign_name = COALESCE(NULLIF(EXCLUDED.campaign_name,''), instantly_campaigns.campaign_name),
                       tags          = CASE WHEN array_length(EXCLUDED.tags,1) > 0 THEN EXCLUDED.tags
                                            ELSE instantly_campaigns.tags END,
                       status        = COALESCE(NULLIF(EXCLUDED.status,''),    instantly_campaigns.status),
                       last_seen_at  = now()""",
                (campaign_id, name or None, tags or None, status or None, source),
            )
    except Exception as e:
        logger.warning("Could not upsert campaign to DB (non-fatal): %s", e)


def _load_db_campaign_ids() -> set[str]:
    """Load all campaign IDs previously saved to instantly_campaigns."""
    try:
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT campaign_id FROM instantly_campaigns")
            return {r["campaign_id"] for r in cur.fetchall()}
    except Exception as e:
        logger.warning("Could not load campaigns from DB (non-fatal): %s", e)
        return set()


def _dabbahwala_campaign_ids() -> set[str]:
    """
    Fast DB lookup — union of hardcoded IDs + whatever n8n has synced into
    instantly_campaigns via the sync-campaigns endpoint.
    """
    return _HARDCODED_IDS | _load_db_campaign_ids()


# ── n8n-callable sync endpoint ───────────────────────────────────────────────

class SyncCampaignsBody(BaseModel):
    """
    Optional body sent by n8n after it fetches and filters campaigns from Instantly.
    Each item: {campaign_id, name, tags: [...], status}
    If omitted, the endpoint falls back to calling Instantly directly (manual/emergency use).
    """
    campaigns: list[dict] = []


@router.post("/sync-campaigns")
def sync_campaigns(body: SyncCampaignsBody = None):
    """
    Upsert DabbahWala campaigns into instantly_campaigns.

    Primary flow (n8n-driven):
      n8n fetches campaigns from Instantly, filters by 'dabbahwala' tag, and
      POSTs them here as {"campaigns": [{campaign_id, name, tags, status}, ...]}.
      This endpoint just does the DB upsert — no outbound Instantly call needed.

    Fallback (manual / emergency):
      If called with no body, falls back to fetching from Instantly directly.
      Always seeds the 5 hardcoded campaigns regardless.
    """
    body = body or SyncCampaignsBody()

    # 1. Always seed hardcoded campaigns
    seeded = 0
    for name, meta in _CAMPAIGN_META.items():
        _upsert_campaign_db(
            campaign_id=meta["instantly_id"],
            name=meta["label"],
            source="hardcoded",
        )
        seeded += 1

    # 2a. n8n passed pre-filtered campaigns — just upsert them
    discovered = 0
    api_error = None
    if body.campaigns:
        for c in body.campaigns:
            cid    = str(c.get("campaign_id") or c.get("id") or "").strip()
            cname  = c.get("name") or c.get("campaign_name") or ""
            tags   = c.get("tags") or []
            status = str(c.get("status") or "")
            if cid:
                _upsert_campaign_db(
                    campaign_id=cid,
                    name=cname,
                    tags=tags if isinstance(tags, list) else [tags],
                    status=status,
                    source="tag_discovered",
                )
                discovered += 1
        logger.info("sync-campaigns (n8n payload): seeded=%d upserted=%d", seeded, discovered)

    # 2b. Fallback — call Instantly ourselves
    else:
        logger.info("sync-campaigns: no n8n payload, falling back to Instantly API call")
        if not INSTANTLY_API_KEY:
            api_error = "INSTANTLY_API_KEY not configured"
        else:
            try:
                resp = httpx.get(
                    "https://api.instantly.ai/api/v2/campaigns",
                    headers={"Authorization": f"Bearer {INSTANTLY_API_KEY}"},
                    params={"limit": 100},
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                campaigns = data if isinstance(data, list) else (
                    data.get("items") or data.get("campaigns") or []
                )
                for c in campaigns:
                    raw_tags = c.get("tags") or []
                    tag_names: list[str] = []
                    for t in raw_tags:
                        if isinstance(t, str):
                            tag_names.append(t)
                        elif isinstance(t, dict):
                            tag_names.append(t.get("name") or t.get("label") or "")
                    if any(tag.strip().lower() == "dabbahwala" for tag in tag_names):
                        cid    = str(c.get("id") or c.get("campaign_id") or "").strip()
                        cname  = c.get("name") or c.get("campaign_name") or ""
                        status = str(c.get("status") or c.get("campaign_status") or "")
                        if cid:
                            _upsert_campaign_db(campaign_id=cid, name=cname,
                                                tags=tag_names, status=status,
                                                source="tag_discovered")
                            discovered += 1
            except Exception as e:
                api_error = str(e)[:300]
                logger.warning("Instantly fallback sync failed: %s", e)

    total = len(_load_db_campaign_ids())
    return {
        "status": "ok",
        "hardcoded_seeded": seeded,
        "tag_discovered": discovered,
        "total_in_db": total,
        "api_error": api_error,
    }


# ── Campaign list endpoint (used by n8n performance tracker) ─────────────────

@router.get("/campaigns")
def list_campaigns():
    """
    Return all DabbahWala campaigns tracked in instantly_campaigns,
    including latest performance stats. Used by n8n to know which
    campaign IDs to fetch analytics for from Instantly.
    """
    try:
        with get_cursor(commit=False) as cur:
            cur.execute("""
                SELECT campaign_id, campaign_name, status, source,
                       leads_count, emails_sent, unique_opens, opens,
                       replies, clicks, bounces, open_rate, reply_rate,
                       stats_synced_at, last_seen_at
                FROM instantly_campaigns
                ORDER BY last_seen_at DESC
            """)
            rows = cur.fetchall()
            # Also include hardcoded IDs not yet in DB
            known = {r["campaign_id"] for r in rows}
            extra = []
            for name, meta in _CAMPAIGN_META.items():
                if meta["instantly_id"] not in known:
                    extra.append({"campaign_id": meta["instantly_id"],
                                  "campaign_name": meta["label"],
                                  "source": "hardcoded"})
            campaigns = [dict(r) for r in rows] + extra
            return {"campaigns": campaigns, "total": len(campaigns)}
    except Exception as e:
        logger.error("list_campaigns failed: %s", e)
        return {"campaigns": [c for c in [
            {"campaign_id": meta["instantly_id"], "campaign_name": meta["label"], "source": "hardcoded"}
            for meta in _CAMPAIGN_META.values()
        ]], "total": len(_CAMPAIGN_META), "fallback": True}


# ── Campaign stats endpoint (n8n posts Instantly analytics here) ──────────────

class CampaignStatsBody(BaseModel):
    campaign_id: str
    leads_count:  int | None = None
    emails_sent:  int | None = None
    unique_opens: int | None = None
    opens:        int | None = None
    replies:      int | None = None
    clicks:       int | None = None
    bounces:      int | None = None
    unsubscribes: int | None = None
    open_rate:    float | None = None
    reply_rate:   float | None = None


@router.post("/campaign-stats")
def update_campaign_stats(body: CampaignStatsBody):
    """
    Receive campaign performance stats from n8n (sourced from Instantly analytics API)
    and update the instantly_campaigns row. Called once per campaign per polling cycle.
    """
    try:
        with get_cursor(commit=True) as cur:
            cur.execute("""
                UPDATE instantly_campaigns SET
                    leads_count    = COALESCE(%s, leads_count),
                    emails_sent    = COALESCE(%s, emails_sent),
                    unique_opens   = COALESCE(%s, unique_opens),
                    opens          = COALESCE(%s, opens),
                    replies        = COALESCE(%s, replies),
                    clicks         = COALESCE(%s, clicks),
                    bounces        = COALESCE(%s, bounces),
                    unsubscribes   = COALESCE(%s, unsubscribes),
                    open_rate      = COALESCE(%s, open_rate),
                    reply_rate     = COALESCE(%s, reply_rate),
                    stats_synced_at = now()
                WHERE campaign_id = %s
            """, (
                body.leads_count, body.emails_sent, body.unique_opens,
                body.opens, body.replies, body.clicks, body.bounces,
                body.unsubscribes, body.open_rate, body.reply_rate,
                body.campaign_id,
            ))
            updated = cur.rowcount
        logger.info("campaign-stats: updated campaign_id=%s rows=%d", body.campaign_id, updated)
        return {"status": "ok", "campaign_id": body.campaign_id, "updated": updated}
    except Exception as e:
        logger.error("campaign-stats update failed: %s", e)
        return {"status": "error", "detail": str(e)[:300]}


# ── Payload helpers ──────────────────────────────────────────────────────────

def _extract_lead(payload: dict) -> dict:
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

    Filters to DabbahWala campaigns only via fast DB lookup (hardcoded IDs +
    anything n8n has synced via /api/webhooks/sync-campaigns).

    On every meaningful engagement (open, reply, click):
      1. Upsert the lead as a contact (noop if already exists)
      2. Record the engagement event
      3. Run lifecycle so the contact gets campaign-assigned immediately
      4. Update last_seen_at on the campaign record in DB
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

    campaign_id     = str(payload.get("campaign_id")     or payload.get("campaignId")     or "").strip()
    campaign_name   =    (payload.get("campaign_name")   or payload.get("campaignName")   or "")
    campaign_status =    (payload.get("campaign_status") or payload.get("status")         or "")

    logger.info("Instantly webhook received: event=%s campaign_id=%s campaign=%s",
                event_type, campaign_id, campaign_name)

    # ── Filter: only process DabbahWala campaigns ────────────────────────────
    if campaign_id:
        known_ids = _dabbahwala_campaign_ids()
        if campaign_id not in known_ids:
            logger.info(
                "Instantly webhook: campaign_id=%s not in DabbahWala set (%d known) — ignored",
                campaign_id, len(known_ids),
            )
            return {"status": "ignored", "reason": "not a DabbahWala campaign",
                    "campaign_id": campaign_id}
        # Touch last_seen_at and capture any name/status carried in this webhook event
        _upsert_campaign_db(campaign_id=campaign_id, name=campaign_name,
                            status=campaign_status, source="webhook")
    else:
        logger.warning("Instantly webhook: no campaign_id in payload — processing anyway")

    # ── Filter: only actionable event types ─────────────────────────────────
    actionable_events = {"email_opened", "email_replied", "link_clicked", "email_clicked",
                         "email_open", "email_reply", "email_click"}
    if event_type not in actionable_events:
        logger.debug("Instantly webhook: unhandled event_type=%s — ignored", event_type)
        return {"status": "ignored", "event_type": event_type}

    lead = _extract_lead(payload)
    if not lead["email"] and not lead["phone"]:
        logger.warning("Instantly webhook %s: no email or phone — skipped", event_type)
        return {"status": "ignored", "reason": "no lead contact info"}

    # ── Upsert contact + record event + run lifecycle ────────────────────────
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
            logger.info("Instantly webhook %s: contact_id=%s is_new=%s email=%s",
                        event_type, contact_id, is_new, lead["email"])

            db_event = "email_click" if ("click" in event_type or "replied" in event_type) else "email_open"
            try:
                cur.execute(
                    "SELECT ingest_event(%s, %s::event_type, %s)",
                    (contact_id, db_event,
                     f"Instantly: {event_type} — campaign: {campaign_name or campaign_id}")
                )
            except Exception as e:
                logger.warning("Could not record engagement event (non-fatal): %s", e)

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


# ── Shipday webhook aliases ───────────────────────────────────────────────────
# Shipday is configured to POST to /api/webhooks/shipday, so we alias
# the handlers from shipday_historical here.

@router.get("/shipday")
async def shipday_ping():
    logger.info("Shipday webhook verification ping (GET /api/webhooks/shipday)")
    return await _shipday_ping()


@router.post("/shipday")
async def shipday_webhook(request: Request):
    body_bytes = await request.body()
    logger.info(
        "Shipday webhook incoming: method=POST path=/api/webhooks/shipday "
        "content_type=%s content_length=%d body_preview=%s",
        request.headers.get("content-type", ""),
        len(body_bytes),
        body_bytes[:200].decode("utf-8", errors="replace"),
    )
    return await _shipday_webhook(request)
