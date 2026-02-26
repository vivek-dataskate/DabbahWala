"""
Inbound webhooks — handles Instantly email events, Telnyx inbound SMS, and Shipday delivery events.

Configure in Instantly:
  Settings → Integrations → Webhooks → Add webhook URL:
  https://<your-domain>/api/webhooks/instantly

Configure in Telnyx (Messaging → Messaging Profiles → <profile> → Webhooks):
  Inbound message webhook URL: https://dabbahwala-latest.onrender.com/api/webhooks/telnyx

Campaign filtering is done by querying campaign_routing (single source of truth).
n8n fetches campaigns from Instantly directly — Python never calls Instantly.
"""
import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.db import get_cursor
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


# ── Campaign ID helpers (single source of truth: campaign_routing) ────────────

def _dabbahwala_campaign_ids() -> set[str]:
    """Return all Instantly campaign IDs registered in campaign_routing."""
    try:
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT instantly_campaign_id FROM campaign_routing WHERE instantly_campaign_id IS NOT NULL")
            return {r["instantly_campaign_id"] for r in cur.fetchall()}
    except Exception as e:
        logger.warning("Could not load campaign IDs from campaign_routing (non-fatal): %s", e)
        return set()


# ── Campaign list endpoint (used by n8n performance tracker) ─────────────────

@router.get("/campaigns")
def list_campaigns():
    """
    Return all DabbahWala campaigns from campaign_routing, including latest stats.
    Used by n8n to know which campaign IDs to fetch analytics for from Instantly.
    """
    try:
        with get_cursor(commit=False) as cur:
            cur.execute("""
                SELECT default_campaign  AS campaign_name,
                       instantly_campaign_id   AS campaign_id,
                       instantly_campaign_name AS label,
                       leads_count, emails_sent, unique_opens, opens,
                       replies, clicks, bounces, open_rate, reply_rate,
                       stats_synced_at
                FROM campaign_routing
                WHERE instantly_campaign_id IS NOT NULL
                ORDER BY lifecycle_segment
            """)
            rows = [dict(r) for r in cur.fetchall()]
        return {"campaigns": rows, "total": len(rows)}
    except Exception as e:
        logger.error("list_campaigns failed: %s", e)
        return {"campaigns": [], "total": 0, "error": str(e)[:200]}


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
    and update the campaign_routing row. Called once per campaign per polling cycle.
    """
    try:
        with get_cursor(commit=True) as cur:
            cur.execute("""
                UPDATE campaign_routing SET
                    leads_count     = COALESCE(%s, leads_count),
                    emails_sent     = COALESCE(%s, emails_sent),
                    unique_opens    = COALESCE(%s, unique_opens),
                    opens           = COALESCE(%s, opens),
                    replies         = COALESCE(%s, replies),
                    clicks          = COALESCE(%s, clicks),
                    bounces         = COALESCE(%s, bounces),
                    unsubscribes    = COALESCE(%s, unsubscribes),
                    open_rate       = COALESCE(%s, open_rate),
                    reply_rate      = COALESCE(%s, reply_rate),
                    stats_synced_at = now()
                WHERE instantly_campaign_id = %s
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

    Filters to DabbahWala campaigns only via campaign_routing table (single source of truth).

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
        # campaign_routing is the single source of truth — no upsert needed here
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


# ── Telnyx inbound SMS webhook ───────────────────────────────────────────────

@router.post("/telnyx")
async def telnyx_webhook(request: Request):
    """
    Receive Telnyx inbound SMS webhooks (event_type: message.received).

    Configure in Telnyx:
      Messaging → Messaging Profiles → <profile> → Webhooks
      Inbound message webhook URL: https://dabbahwala-latest.onrender.com/api/webhooks/telnyx

    Telnyx sends a POST for every inbound SMS. We store it as a telnyx_message
    and fire the agent cycle for the contact so the AI can reason immediately.
    """
    import json as _json
    try:
        payload = await request.json()
    except Exception:
        logger.warning("Telnyx webhook: non-JSON body")
        return {"status": "ignored", "reason": "non-JSON body"}

    data = payload.get("data") or {}
    event_type = data.get("event_type") or ""

    if event_type != "message.received":
        logger.debug("Telnyx webhook: ignoring event_type=%s", event_type)
        return {"status": "ignored", "event_type": event_type}

    msg = data.get("payload") or {}
    from_number = (msg.get("from") or {}).get("phone_number") or ""
    to_list     = msg.get("to") or []
    to_number   = to_list[0].get("phone_number") if to_list else ""
    body        = msg.get("text") or ""
    telnyx_id   = msg.get("id") or ""
    status      = (to_list[0].get("status") if to_list else None) or "received"

    if not from_number:
        logger.warning("Telnyx webhook: no from_number in payload")
        return {"status": "ignored", "reason": "no from_number"}

    logger.info("Telnyx inbound SMS: from=%s to=%s id=%s", from_number, to_number, telnyx_id)

    # Resolve contact by phone — skip gracefully if unknown number
    normalized = "".join(c for c in from_number if c.isdigit() or c == "+")
    with get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT id, email FROM contacts WHERE phone = %s OR phone = %s LIMIT 1",
            (from_number, normalized),
        )
        row = cur.fetchone()

    if not row:
        logger.info("Telnyx webhook: no contact for phone=%s — message stored as-is", from_number)
        # Still store the message; store_telnyx_message will raise if contact missing
        return {"status": "ignored", "reason": "contact_not_found", "from": from_number}

    contact_id    = row["id"]
    contact_email = row["email"]

    try:
        with get_cursor(commit=True) as cur:
            cur.execute(
                "SELECT store_telnyx_message(%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
                (
                    contact_email,
                    "inbound",
                    from_number,
                    to_number,
                    body,
                    telnyx_id,
                    status,
                    False,
                    _json.dumps({"source": "telnyx_webhook", "event_id": data.get("id")}),
                ),
            )
            msg_row = cur.fetchone()
            msg_id  = msg_row["store_telnyx_message"]
    except Exception as e:
        logger.error("Telnyx webhook: store_telnyx_message failed: %s", e, exc_info=True)
        return {"status": "error", "detail": str(e)[:300]}

    # Fire agent cycle immediately — customer just replied, context is live
    threading.Thread(
        target=_fire_agent_cycle,
        args=(contact_id, "sms_inbound_webhook"),
        daemon=True,
    ).start()

    return {"status": "ok", "msg_id": msg_id, "contact_id": contact_id}


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

    # For DELIVERED: delay 4 hours so the customer has time to eat, experience the food,
    # and potentially leave feedback before any outreach decision is made.
    # For FAILED: fire immediately — same-day escalation is still time-sensitive.
    agent_cycle = "skipped"
    if contact_id and our_status == "failed":
        threading.Thread(
            target=_fire_agent_cycle,
            args=(contact_id, f"shipday_{raw_status.lower()}"),
            daemon=True,
        ).start()
        agent_cycle = "triggered"
    elif contact_id and our_status == "delivered":
        delay_seconds = 4 * 3600  # 4 hours post-delivery
        threading.Timer(
            delay_seconds,
            _fire_agent_cycle,
            args=(contact_id, "shipday_delivered_delayed"),
        ).start()
        agent_cycle = "scheduled_4h"
        logger.info(
            "Agent cycle scheduled in 4h for contact_id=%s after delivery", contact_id
        )

    return {
        "status":         "ok",
        "order_id":       order_id,
        "shipday_status": raw_status,
        "mapped_status":  our_status,
        "contact_found":  contact_id is not None,
        "agent_cycle":    agent_cycle,
    }
