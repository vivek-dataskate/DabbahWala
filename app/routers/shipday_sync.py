"""
Shipday Communications Sync
============================
Polls the Shipday API for post-delivery customer communications:
  - customer_feedback     : text feedback left by the customer after delivery
  - delivery_instruction  : special delivery notes attached to the order
  - proof_of_delivery     : photo URLs / signature captured at the door

All records land in shipday_communications (migration 045) linked to a contact.
Feedback fires a delivery_update event so the lifecycle engine and inference
agents can respond to customer sentiment.

After each sync a follow-up step creates Opportunities for:
  - negative feedback  → hot priority recovery action (apology + offer)
  - positive feedback  → warm priority re-order nudge (strike while iron is hot)

Endpoints (registered under /api/shipday):
  POST /api/shipday/sync-feedback    — sync feedback for recently delivered orders
  GET  /api/shipday/feedback-stats   — sentiment breakdown + recent feedback log
"""

import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.db import get_cursor

logger = logging.getLogger(__name__)

router = APIRouter()

SHIPDAY_API_BASE = "https://api.shipday.com"


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _get_shipday_key() -> str:
    key = os.environ.get("SHIPDAY_API_KEY", "") or os.environ.get("SHIPDAY_KEY", "")
    if not key:
        raise HTTPException(status_code=500, detail="SHIPDAY_API_KEY not configured")
    return key


def _fetch_order_detail(api_key: str, shipday_order_id: str) -> Optional[dict]:
    """
    GET /orders/{orderId} — fetch full order object including feedback and
    proof-of-delivery fields.  Returns None on 404 or network error.
    """
    url = f"{SHIPDAY_API_BASE}/orders/{shipday_order_id}"
    headers = {
        "Authorization": f"Basic {api_key}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 404:
                logger.debug("Shipday order %s → 404", shipday_order_id)
                return None
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        logger.warning(
            "Shipday order %s HTTP %d: %s",
            shipday_order_id, e.response.status_code, e.response.text[:120],
        )
        return None
    except Exception as e:
        logger.warning("Shipday order %s fetch failed: %s", shipday_order_id, e)
        return None


def _store_order_communications(shipday_order_id: str, order: dict, occurred_at: Optional[str]) -> list[str]:
    """
    Extract and persist all communication fields from one Shipday order object.
    Returns list of comm_types that were stored.
    """
    stored = []

    with get_cursor(commit=True) as cur:

        # 1 ── Customer feedback (free-text review left after delivery)
        feedback = (order.get("feedback") or "").strip()
        if feedback:
            cur.execute(
                "SELECT store_shipday_communication(%s, 'customer_feedback', %s, NULL, %s::jsonb, %s::timestamptz)",
                (
                    shipday_order_id,
                    feedback,
                    json.dumps({"raw_feedback": feedback}),
                    occurred_at,
                ),
            )
            stored.append("customer_feedback")
            logger.debug("Order %s: stored feedback (%d chars)", shipday_order_id, len(feedback))

        # 2 ── Delivery instruction (customer note at order time)
        instr = (order.get("deliveryInstruction") or "").strip()
        if instr:
            cur.execute(
                "SELECT store_shipday_communication(%s, 'delivery_instruction', %s, NULL, %s::jsonb, %s::timestamptz)",
                (
                    shipday_order_id,
                    instr,
                    json.dumps({"raw_instruction": instr}),
                    occurred_at,
                ),
            )
            stored.append("delivery_instruction")

        # 3 ── Proof of delivery (signature + photos captured at doorstep)
        pod = order.get("proofOfDelivery") or {}
        pic_paths: list = pod.get("picturePaths") or []
        sig_path: str   = (pod.get("signaturePath") or "").strip()
        all_urls = ([sig_path] if sig_path else []) + [p for p in pic_paths if p]
        if all_urls:
            cur.execute(
                "SELECT store_shipday_communication(%s, 'proof_of_delivery', NULL, %s::text[], %s::jsonb, %s::timestamptz)",
                (
                    shipday_order_id,
                    all_urls,
                    json.dumps({"proof_of_delivery": pod}),
                    occurred_at,
                ),
            )
            stored.append("proof_of_delivery")

    return stored


def _create_feedback_opportunity(contact_id: int, shipday_order_id: str, sentiment: str) -> None:
    """
    Create an Opportunity so the system acts on the feedback signal:
      negative  → hot  priority recovery (apologise + offer)
      positive  → warm priority re-order nudge (momentum capture)
    """
    if sentiment == "negative":
        priority = "hot"
        reason = (
            "Customer left negative delivery feedback in Shipday. "
            "Reach out with an apology and a recovery offer before trust erodes."
        )
        suggested_msg = (
            "Hi! We saw your recent DabbahWala delivery didn't meet your expectations — "
            "we're really sorry. Can we make it right? Reply here and we'll send a special offer."
        )
    elif sentiment == "positive":
        priority = "warm"
        reason = (
            "Customer left positive delivery feedback in Shipday — "
            "strike while the iron is hot with a re-order nudge."
        )
        suggested_msg = (
            "Hi! So glad you enjoyed your DabbahWala delivery! 🍱 "
            "We'd love to cook for you again — ready for your next order?"
        )
    else:
        return  # neutral: no proactive opportunity needed

    try:
        with get_cursor(commit=True) as cur:
            cur.execute(
                """
                INSERT INTO opportunities
                    (contact_id, action, priority, reason, suggested_message,
                     confidence_score, status)
                VALUES (%s, 'send_sms', %s, %s, %s, %s, 'pending')
                ON CONFLICT DO NOTHING
                """,
                (
                    contact_id,
                    priority,
                    reason,
                    suggested_msg,
                    0.85 if sentiment == "negative" else 0.70,
                ),
            )
        logger.info(
            "Opportunity created for contact %s: %s feedback → %s priority",
            contact_id, sentiment, priority,
        )
    except Exception as e:
        logger.warning("Failed to create feedback opportunity for contact %s: %s", contact_id, e)


# ─────────────────────────────────────────────────────────────────
# Background sync worker
# ─────────────────────────────────────────────────────────────────

_sync_state: dict = {
    "running":               False,
    "started_at":            None,
    "orders_checked":        0,
    "orders_with_comms":     0,
    "feedback_positive":     0,
    "feedback_negative":     0,
    "feedback_neutral":      0,
    "opportunities_created": 0,
    "errors":                0,
    "completed_at":          None,
}


def _run_feedback_sync(days_back: int = 7) -> None:
    """
    Background worker.  For every delivered Shipday order in the last N days
    that hasn't been communication-synced yet:
      1. Fetch full order detail from Shipday API
      2. Store feedback / delivery notes / proof-of-delivery
      3. Create opportunities for actionable sentiment
    """
    global _sync_state

    api_key = os.environ.get("SHIPDAY_API_KEY", "") or os.environ.get("SHIPDAY_KEY", "")
    if not api_key:
        logger.error("Feedback sync: SHIPDAY_API_KEY not set — aborting")
        _sync_state["running"] = False
        return

    _sync_state.update({
        "running":               True,
        "started_at":            datetime.now(timezone.utc).isoformat(),
        "orders_checked":        0,
        "orders_with_comms":     0,
        "feedback_positive":     0,
        "feedback_negative":     0,
        "feedback_neutral":      0,
        "opportunities_created": 0,
        "errors":                0,
        "completed_at":          None,
    })

    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    try:
        # Find delivered orders that have not yet had their feedback pulled
        with get_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT sor.shipday_order_id,
                       sor.contact_id,
                       sor.actual_delivery
                FROM   shipday_orders_raw sor
                WHERE  sor.shipday_status IN ('COMPLETED', 'DELIVERED')
                  AND  sor.contact_id IS NOT NULL
                  AND  (
                      sor.actual_delivery >= %s
                      OR sor.synced_at    >= %s
                  )
                  AND  NOT EXISTS (
                      SELECT 1
                      FROM   shipday_communications sc
                      WHERE  sc.shipday_order_id = sor.shipday_order_id
                        AND  sc.comm_type = 'customer_feedback'
                  )
                ORDER BY sor.actual_delivery DESC NULLS LAST
                """,
                (cutoff, cutoff),
            )
            pending = [dict(r) for r in cur.fetchall()]

        logger.info("Feedback sync: %d unprocessed delivered orders", len(pending))

        for row in pending:
            order_id   = row["shipday_order_id"]
            contact_id = row["contact_id"]
            occurred_at = (
                row["actual_delivery"].isoformat()
                if row["actual_delivery"] else None
            )

            try:
                order = _fetch_order_detail(api_key, order_id)
                _sync_state["orders_checked"] += 1

                if not order:
                    continue

                stored = _store_order_communications(order_id, order, occurred_at)

                if stored:
                    _sync_state["orders_with_comms"] += 1

                # Post-process feedback sentiment for opportunity creation
                if "customer_feedback" in stored:
                    with get_cursor(commit=False) as cur:
                        cur.execute(
                            """SELECT sentiment FROM shipday_communications
                               WHERE shipday_order_id = %s
                                 AND comm_type = 'customer_feedback'""",
                            (order_id,),
                        )
                        sentiment_row = cur.fetchone()

                    sentiment = sentiment_row["sentiment"] if sentiment_row else "neutral"
                    if sentiment == "positive":
                        _sync_state["feedback_positive"] += 1
                    elif sentiment == "negative":
                        _sync_state["feedback_negative"] += 1
                    else:
                        _sync_state["feedback_neutral"] += 1

                    _create_feedback_opportunity(contact_id, order_id, sentiment)
                    if sentiment in ("positive", "negative"):
                        _sync_state["opportunities_created"] += 1

                # Respectful API pacing
                time.sleep(0.25)

            except Exception as e:
                _sync_state["errors"] += 1
                logger.warning("Feedback sync: order %s failed: %s", order_id, e)

    except Exception as e:
        logger.error("Feedback sync crashed: %s", e, exc_info=True)
        _sync_state["errors"] += 1

    finally:
        _sync_state["running"]      = False
        _sync_state["completed_at"] = datetime.now(timezone.utc).isoformat()
        logger.info(
            "Feedback sync done: checked=%d with_comms=%d "
            "positive=%d negative=%d neutral=%d opportunities=%d errors=%d",
            _sync_state["orders_checked"],
            _sync_state["orders_with_comms"],
            _sync_state["feedback_positive"],
            _sync_state["feedback_negative"],
            _sync_state["feedback_neutral"],
            _sync_state["opportunities_created"],
            _sync_state["errors"],
        )


# ─────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────

class FeedbackSyncRequest(BaseModel):
    days_back:          int  = 7     # look back N days for delivered orders
    run_in_background:  bool = True


@router.post("/sync-feedback")
async def sync_feedback(req: FeedbackSyncRequest, background_tasks: BackgroundTasks):
    """
    Poll Shipday for customer feedback, delivery instructions, and
    proof-of-delivery on all delivered orders in the last N days.

    Stores results in shipday_communications and creates Opportunities for
    actionable sentiment (negative → recovery, positive → re-order nudge).

    Runs in the background by default — check /api/shipday/feedback-stats
    for progress and results.
    """
    if _sync_state["running"]:
        return {"status": "already_running", "state": _sync_state}

    if req.run_in_background:
        background_tasks.add_task(_run_feedback_sync, req.days_back)
        return {
            "status":    "started",
            "days_back": req.days_back,
            "message":   "Feedback sync running in background. Poll /api/shipday/feedback-stats for results.",
        }

    _run_feedback_sync(req.days_back)
    return {"status": "complete", "state": _sync_state}


@router.get("/feedback-stats")
def feedback_stats():
    """
    Return:
      - Current sync state (running / last run summary)
      - Sentiment breakdown (comm_type × sentiment × count)
      - 20 most recent customer feedback entries
    """
    try:
        with get_cursor(commit=False) as cur:

            cur.execute(
                """
                SELECT comm_type,
                       COALESCE(sentiment, 'n/a') AS sentiment,
                       COUNT(*)                    AS count,
                       MAX(occurred_at)            AS latest
                FROM   shipday_communications
                GROUP  BY comm_type, sentiment
                ORDER  BY comm_type, sentiment
                """
            )
            breakdown = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT sc.id,
                       sc.shipday_order_id,
                       sc.order_number,
                       sc.content,
                       sc.sentiment,
                       sc.occurred_at,
                       c.email,
                       TRIM(COALESCE(c.first_name,'') || ' ' || COALESCE(c.last_name,'')) AS full_name,
                       c.phone
                FROM   shipday_communications sc
                LEFT   JOIN contacts c ON c.id = sc.contact_id
                WHERE  sc.comm_type = 'customer_feedback'
                ORDER  BY sc.occurred_at DESC
                LIMIT  20
                """
            )
            recent = [dict(r) for r in cur.fetchall()]

        # Serialize datetimes
        for rows in (breakdown, recent):
            for r in rows:
                for k, v in r.items():
                    if hasattr(v, "isoformat"):
                        r[k] = v.isoformat()

        return {
            "sync_state":      _sync_state,
            "breakdown":       breakdown,
            "recent_feedback": recent,
        }

    except Exception as e:
        logger.error("feedback_stats failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch feedback stats: {e}")
