"""
Inbound webhooks — currently handles Instantly email events.

Configure in Instantly:
  Settings → Integrations → Webhooks → Add webhook URL:
  https://<your-domain>/api/webhooks/instantly

Campaign registry (instantly_campaigns table) is kept in sync by n8n calling:
  POST /api/webhooks/sync-campaigns   (schedule: every 6 h or as desired in n8n)

n8n fetches campaigns from Instantly directly and passes them in the request body.
Python never calls Instantly — all external API calls go through n8n.
"""
import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.db import get_cursor
from app.routers.campaigns import _CAMPAIGN_META
from app.routers.prospects import _upsert_contact

logger = logging.getLogger(__name__)


def _fire_agent_cycle(contact_id: int, trigger: str) -> None:
    """Run the full agent cycle in a background thread — non-blocking.

    Called after every Shipday or Instantly event so the agent stack always
    reasons from the freshest evidence without delaying the webhook response.
    """
    try:
        from app.routers.agents import _run_full_cycle  # lazy import avoids circular deps
        logger.info("Agent cycle triggered by %s for contact_id=%s", trigger, contact_id)
        _run_full_cycle(contact_id)
    except Exception as e:
        logger.error(
            "Background agent cycle failed contact_id=%s trigger=%s: %s",
            contact_id, trigger, e, exc_info=True,
        )

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

class CampaignSyncPayload(BaseModel):
    """n8n fetches campaigns from Instantly and passes them here.
    Python never calls Instantly directly.
    """
    campaigns: list[dict] = []


@router.post("/sync-campaigns")
def sync_campaigns(body: CampaignSyncPayload = CampaignSyncPayload()):
    """
    Upsert DabbahWala campaigns into instantly_campaigns.

    n8n fetches the campaign list from Instantly API and passes it in the
    request body as `campaigns`. Python writes to Postgres only — no outbound
    HTTP to Instantly.

    Call this from n8n on a schedule (e.g. every 6 hours) with the Instantly
    campaign list as the POST body.
    """

    # 1. Always seed hardcoded campaigns
    seeded = 0
    for name, meta in _CAMPAIGN_META.items():
        _upsert_campaign_db(
            campaign_id=meta["instantly_id"],
            name=meta["label"],
            source="hardcoded",
        )
        seeded += 1

    # 2. Upsert campaigns provided by n8n
    discovered = 0
    for c in body.campaigns:
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
                _upsert_campaign_db(
                    campaign_id=cid,
                    name=cname,
                    tags=tag_names,
                    status=status,
                    source="tag_discovered",
                )
                discovered += 1

    total = len(_load_db_campaign_ids())
    return {
        "status": "ok",
        "hardcoded_seeded": seeded,
        "tag_discovered": discovered,
        "total_in_db": total,
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

    except Exception as e:
        logger.error("Instantly webhook processing failed: %s", e, exc_info=True)
        return {"status": "error", "detail": str(e)[:300]}

    # Evidence stored — lifecycle and agent cycle run on nightly schedule with full evidence
    return {
        "status": "ok",
        "event_type": event_type,
        "contact_id": contact_id,
        "is_new": is_new,
    }


# ── Shipday webhook ───────────────────────────────────────────────────────────

_SHIPDAY_TO_STATUS = {
    "ACCEPTED":  "assigned",
    "ASSIGNED":  "assigned",
    "PICKED_UP": "picked_up",
    "IN_TRANSIT":"in_transit",
    "COMPLETED": "delivered",
    "DELIVERED": "delivered",
    "FAILED":    "failed",
    "RETURNED":  "failed",
}


@router.get("/shipday")
async def shipday_webhook_ping(request: Request):
    """Shipday verification ping — just needs a 200 OK."""
    logger.info("Shipday GET /api/webhooks/shipday ping — headers: %s", dict(request.headers))
    return {"status": "ok"}


@router.post("/shipday")
async def shipday_webhook(request: Request):
    """
    Inbound Shipday webhook — receives order status change callbacks.

    ONLY updates existing records (shipday_orders_raw + delivery_status).
    Never creates new orders, contacts, or events.
    """
    try:
        body = await request.body()
    except Exception as e:
        logger.error("Shipday webhook: failed to read body: %s", e)
        raise HTTPException(status_code=400, detail="Could not read request body")

    logger.info(
        "Shipday POST /api/webhooks/shipday — content_type=%s content_length=%s auth=%s body_bytes=%d body=%s",
        request.headers.get("content-type", "(none)"),
        request.headers.get("content-length", "(none)"),
        request.headers.get("authorization", "(none)"),
        len(body),
        body[:500].decode("utf-8", errors="replace"),
    )

    if not body or not body.strip():
        logger.info("Shipday webhook: empty body — verification ping, returning 200")
        return {"status": "ok"}

    expected = os.environ.get("SHIPDAY_WEBHOOK_TOKEN", "").strip()
    if expected:
        auth = request.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ").strip()
        if token != expected:
            logger.warning(
                "Shipday webhook rejected — token mismatch (got=%r expected_len=%d)",
                token[:8] + "..." if token else "(none)",
                len(expected),
            )
            raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        payload = json.loads(body)
    except Exception:
        logger.warning("Shipday webhook: non-JSON body — returning 200 (body=%s)", body[:200].decode("utf-8", errors="replace"))
        return {"status": "ok"}

    order_id = str(payload.get("orderId") or payload.get("id") or "").strip()
    if not order_id:
        logger.info("Shipday webhook: no orderId in payload (likely verification) — returning 200. payload=%s", str(payload)[:300])
        return {"status": "ok"}

    raw_status   = (payload.get("orderStatus") or payload.get("status") or "UNKNOWN").upper()
    our_status   = _SHIPDAY_TO_STATUS.get(raw_status)
    order_number = payload.get("orderNumber") or ""
    carrier      = payload.get("assignedCarrier") or {}
    driver_name  = carrier.get("name")
    driver_phone = carrier.get("phone")

    actual_delivery: Optional[str] = None
    try:
        actual_delivery = payload.get("actualDeliveryTime") or None
    except Exception:
        pass

    logger.info("Shipday webhook: order_id=%s status=%s → %s driver=%s", order_id, raw_status, our_status, driver_name)

    with get_cursor(commit=True) as cur:
        cur.execute(
            """SELECT contact_id, customer_phone, customer_email, order_number
               FROM shipday_orders_raw WHERE shipday_order_id = %s""",
            (order_id,)
        )
        existing = cur.fetchone()
        if not existing:
            logger.info("Shipday webhook: order_id=%s not found in shipday_orders_raw — ignoring", order_id)
            return {"status": "ignored", "reason": "order_not_found", "order_id": order_id}

        contact_id = existing["contact_id"]
        order_ref  = order_number or existing.get("order_number") or order_id

        cur.execute(
            """UPDATE shipday_orders_raw
               SET shipday_status  = %s,
                   actual_delivery = COALESCE(%s::timestamptz, actual_delivery),
                   driver_name     = COALESCE(%s, driver_name),
                   driver_phone    = COALESCE(%s, driver_phone),
                   synced_at       = NOW(),
                   raw_payload     = raw_payload || %s::jsonb
               WHERE shipday_order_id = %s""",
            (raw_status, actual_delivery, driver_name, driver_phone,
             json.dumps({"webhook_updated_at": datetime.now(timezone.utc).isoformat()}), order_id)
        )

        if contact_id and our_status == "delivered" and order_ref:
            cur.execute(
                """UPDATE orders
                   SET delivery_date = CURRENT_DATE,
                       metadata = metadata || %s::jsonb
                   WHERE contact_id = %s
                     AND (order_id_external = %s OR order_id_external = %s)
                     AND delivery_date IS NULL""",
                (json.dumps({"shipday_status": raw_status, "shipday_order_id": order_id}),
                 contact_id, order_ref, order_id)
            )

        if contact_id and our_status:
            cur.execute(
                """INSERT INTO delivery_status
                     (contact_id, order_ref, status, updated_by, occurred_at, metadata)
                   VALUES (%s, %s, %s, 'shipday_webhook', NOW(), %s)""",
                (contact_id, order_ref, our_status,
                 json.dumps({"shipday_order_id": order_id, "raw_status": raw_status,
                             "source": "webhook", "driver_name": driver_name}))
            )

        # Ingest delivery_update event for every status change (contact_id always present)
        if contact_id:
            try:
                cur.execute(
                    "SELECT ingest_event(%s, %s::event_type, %s)",
                    (contact_id, "delivery_update",
                     json.dumps({"shipday_order_id": order_id, "raw_status": raw_status,
                                 "mapped_status": our_status, "order_ref": order_ref,
                                 "source": "shipday_webhook"}))
                )
            except Exception as e:
                logger.warning("Shipday webhook: could not ingest delivery_update event (non-fatal): %s", e)

    # For DELIVERED and FAILED only, fire agent immediately — these have real time windows:
    # DELIVERED = prime reorder window (~1-2h), FAILED = same-day escalation needed
    agent_cycle = "skipped"
    if contact_id and our_status in {"delivered", "failed"}:
        threading.Thread(
            target=_fire_agent_cycle,
            args=(contact_id, f"shipday_{raw_status.lower()}"),
            daemon=True,
        ).start()
        agent_cycle = "triggered"

    return {
        "status":         "ok",
        "order_id":       order_id,
        "shipday_status": raw_status,
        "mapped_status":  our_status,
        "contact_found":  contact_id is not None,
        "agent_cycle":    agent_cycle,
    }
