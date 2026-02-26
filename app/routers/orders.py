"""
Order Intake — Shipday Data Ingestion + Feedback Sync
=====================================================
Receives Shipday delivery data from n8n and stores it in the database.
Combines shipday_historical.py (order ingestion + pipeline) and
shipday_sync.py (feedback/communication sync).

n8n workflows push data here — Python never calls Shipday directly.

Registered at /api/shipday in main.py.
"""
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

import time
from datetime import datetime, timezone, timedelta
from app.db import get_cursor

SHIPDAY_API_BASE = "https://api.shipday.com"


def _get_shipday_key() -> str:
    key = os.environ.get("SHIPDAY_API_KEY", "") or os.environ.get("SHIPDAY_KEY", "")
    if not key:
        raise HTTPException(status_code=500, detail="SHIPDAY_API_KEY not configured")
    return key


# State dict for the historical orders sync (Phase 1 of the pipeline)
_sync_state: dict = {
    "running":          False,
    "started_at":       None,
    "completed_at":     None,
    "pages_fetched":    0,
    "orders_synced":    0,
    "orders_matched":   0,
    "contacts_created": 0,
    "orders_created":   0,
    "orders_updated":   0,
    "errors":           0,
    "error":            None,
}


def _run_historical_sync(api_key: str, from_date: str, max_pages: int) -> None:
    """
    Fetch all Shipday orders from `from_date` to now and ingest them via
    _sync_one_order().  Updates the module-level _sync_state.
    Uses date-window chunking (30-day windows) to avoid huge single responses.
    """
    global _sync_state
    _sync_state.update({
        "running":          True,
        "started_at":       datetime.now(timezone.utc).isoformat(),
        "completed_at":     None,
        "pages_fetched":    0,
        "orders_synced":    0,
        "orders_matched":   0,
        "contacts_created": 0,
        "orders_created":   0,
        "orders_updated":   0,
        "errors":           0,
        "error":            None,
    })
    headers = {
        "Authorization": f"Basic {api_key}",
        "Content-Type": "application/json",
    }

    try:
        start = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        window_days = 30
        pages_used = 0

        current = start
        while current < now and pages_used < max_pages:
            chunk_end = min(current + timedelta(days=window_days), now)
            params = {
                "startDate": current.strftime("%Y-%m-%d"),
                "endDate":   chunk_end.strftime("%Y-%m-%d"),
            }
            try:
                with httpx.Client(timeout=30.0) as client:
                    resp = client.get(
                        f"{SHIPDAY_API_BASE}/orders",
                        headers=headers,
                        params=params,
                    )
                    resp.raise_for_status()
                    orders = resp.json()
                    if isinstance(orders, dict):
                        orders = orders.get("orders", orders.get("data", []))
            except Exception as e:
                logger.warning("Historical sync fetch error %s–%s: %s", params["startDate"], params["endDate"], e)
                _sync_state["errors"] += 1
                current = chunk_end
                continue

            pages_used += 1
            _sync_state["pages_fetched"] += 1
            logger.info(
                "Historical sync: fetched %d orders for %s–%s (pages=%d)",
                len(orders), params["startDate"], params["endDate"], pages_used,
            )

            for order in orders:
                try:
                    result = _sync_one_order(order)
                    status = result.get("status", "")
                    if status != "skipped":
                        _sync_state["orders_synced"] += 1
                    if result.get("matched"):
                        _sync_state["orders_matched"] += 1
                    if result.get("contact_created"):
                        _sync_state["contacts_created"] += 1
                    if status == "created":
                        _sync_state["orders_created"] += 1
                    elif status == "updated":
                        _sync_state["orders_updated"] += 1
                except Exception as e:
                    _sync_state["errors"] += 1
                    logger.warning("Historical sync order ingest error: %s", e)

            current = chunk_end

        logger.info(
            "Historical sync complete: pages=%d synced=%d matched=%d contacts_created=%d errors=%d",
            _sync_state["pages_fetched"],
            _sync_state["orders_synced"],
            _sync_state["orders_matched"],
            _sync_state["contacts_created"],
            _sync_state["errors"],
        )

    except Exception as e:
        _sync_state["error"] = str(e)
        logger.error("Historical sync fatal error: %s", e, exc_info=True)
    finally:
        _sync_state["running"] = False
        _sync_state["completed_at"] = datetime.now(timezone.utc).isoformat()

logger = logging.getLogger(__name__)

router = APIRouter()



def _sync_one_order(payload: dict) -> dict:
    """Store one Shipday order in the DB via the sync_shipday_order() stored proc."""
    with get_cursor(commit=True) as cur:
        cur.execute(
            "SELECT sync_shipday_order(%s::jsonb)",
            (json.dumps(payload),),
        )
        row = cur.fetchone()
        result = row["sync_shipday_order"] if row else {}
        if isinstance(result, str):
            result = json.loads(result)
        return result or {}


# ─────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────

class IngestOrdersRequest(BaseModel):
    """Payload sent by n8n's Shipday Delivery Collector workflow."""
    orders: list[dict]


@router.post("/ingest-orders")
def ingest_orders(req: IngestOrdersRequest):
    """
    Accept a batch of Shipday orders from n8n and store them in the DB.

    n8n's [Evidence Collection] Shipday Delivery Collector workflow fetches
    orders from the Shipday API and POSTs them here. Python only writes to
    Postgres — it never calls Shipday directly.

    This endpoint is idempotent: orders already in the DB are updated (not duplicated).
    """
    synced = 0
    matched = 0
    errors = 0
    last_error = None

    for order in req.orders:
        try:
            result = _sync_one_order(order)
            if result.get("status") != "skipped":
                synced += 1
            if result.get("matched"):
                matched += 1
        except Exception as e:
            errors += 1
            last_error = f"order {order.get('orderId', '?')}: {e}"
            logger.warning("Failed to ingest Shipday order %s: %s", order.get("orderId", "?"), e)

    logger.info(
        "Shipday ingest complete: received=%d synced=%d matched=%d errors=%d",
        len(req.orders), synced, matched, errors,
    )
    return {
        "status": "ok",
        "received": len(req.orders),
        "synced": synced,
        "matched": matched,
        "errors": errors,
        "last_error": last_error,
    }


@router.get("/sync-status")
def sync_status():
    """Return current sync progress and counts."""
    # Also query how many raw orders we have in the DB
    try:
        with get_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS total,
                       COUNT(contact_id) AS matched,
                       COUNT(CASE WHEN shipday_status = 'COMPLETED' THEN 1 END) AS completed,
                       MIN(order_created_at) AS oldest_order,
                       MAX(order_created_at) AS newest_order
                FROM shipday_orders_raw
                """
            )
            row = cur.fetchone()
            db_stats = dict(row) if row else {}
    except Exception as e:
        logger.warning("Could not query shipday_orders_raw: %s — migration may not have run yet", e)
        db_stats = {"error": str(e)}

    return {
        "sync_mode": "n8n_push",
        "note": "Shipday data is fetched by n8n ([Shipday: Evidence] Delivery Collector) and pushed to POST /api/shipday/ingest-orders",
        "db_stats": db_stats,
    }


@router.get("/top-calls")
def get_top_calls(limit: int = 10):
    """Return the N contacts most worth calling, with call scripts.

    Scoring model (see migration 034):
    - Recent email engagement (opens_7d weight: 35%)
    - Days since last order (sweet spot 14-60 days: 25%)
    - Order history depth (loyal customers easier to convert: 20%)
    - Recent delivery completed (prime reorder window: 15%)
    - Outcome feedback (previous 'ordered': +5%, 'declined': -10%)
    - Penalise if already SMS'd in last 7 days

    Returns contacts ranked by urgency_score DESC with full call script.
    """
    logger.info("GET /top-calls limit=%d", limit)
    try:
        with get_cursor(commit=False) as cur:
            cur.execute(
                "SELECT * FROM get_top_reorder_candidates(%s)",
                (limit,),
            )
            rows = [dict(r) for r in cur.fetchall()]

        if not rows:
            logger.warning("get_top_reorder_candidates returned 0 rows — no eligible contacts")
            # Fallback: raw query without stored proc
            with get_cursor(commit=False) as cur:
                cur.execute(
                    """
                    SELECT
                        ROW_NUMBER() OVER (ORDER BY total_orders DESC, last_order_at DESC) AS rank,
                        c.id AS contact_id,
                        TRIM(COALESCE(c.first_name, '') || ' ' || COALESCE(c.last_name, '')) AS full_name,
                        c.phone,
                        c.email,
                        c.lifecycle_segment::TEXT,
                        c.total_orders,
                        c.last_order_at,
                        EXTRACT(DAY FROM NOW() - c.last_order_at)::INT AS days_since_last_order,
                        COALESCE(er.opens_7d, 0) AS opens_7d,
                        COALESCE(er.opens_30d, 0) AS opens_30d,
                        COALESCE(er.orders_90d, 0) AS orders_90d,
                        0.5 AS urgency_score,
                        'Top customer by order count' AS call_reason,
                        'Hi! This is DabbahWala. We noticed you haven''t ordered recently. '
                        || 'We''d love to cook for you again — can I tell you about our specials?' AS suggested_script
                    FROM contacts c
                    LEFT JOIN engagement_rollups er ON er.contact_id = c.id
                    WHERE c.phone IS NOT NULL
                      AND c.phone != ''
                      AND c.last_order_at IS NOT NULL
                    ORDER BY c.total_orders DESC, er.opens_7d DESC NULLS LAST
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = [dict(r) for r in cur.fetchall()]
            logger.info("Fallback query returned %d candidates", len(rows))

        logger.info(
            "Top calls: %d candidates returned (top urgency=%.2f)",
            len(rows),
            rows[0].get("urgency_score", 0) if rows else 0,
        )

        # Serialize datetime fields
        for r in rows:
            for k, v in r.items():
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()

        return {
            "count": len(rows),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model_notes": (
                "Urgency score = engagement (35%) + recency gap (25%) + loyalty (20%) "
                "+ delivery timing (15%) + outcome feedback (±10%). "
                "Contacts with recent SMS are penalised to avoid over-contact."
            ),
            "candidates": rows,
        }
    except Exception as e:
        logger.error("get_top_calls failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to compute top calls: {e}")


@router.post("/run-migration")
def run_shipday_migration():
    """Run migration 034 to create the shipday schema. Admin use only."""
    import glob
    matches = glob.glob("migrations/034_*.sql")
    if not matches:
        raise HTTPException(status_code=404, detail="Migration 034 not found")

    migration_file = matches[0]
    logger.info("Running Shipday migration: %s", migration_file)
    try:
        with open(migration_file) as f:
            sql = f.read()
        with get_cursor(commit=True) as cur:
            cur.execute(sql)
        logger.info("Migration %s executed successfully", migration_file)
        return {"status": "ok", "migration": migration_file}
    except Exception as e:
        logger.error("Migration failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Migration failed: {e}")


# ─────────────────────────────────────────────────────────────────
# Full import pipeline: orders → notes/feedback → evidence → agents
# ─────────────────────────────────────────────────────────────────

_pipeline_state = {
    "running":           False,
    "phase":             None,   # "orders" | "feedback" | "rollups" | "agents" | "done"
    "started_at":        None,
    "completed_at":      None,
    "orders_synced":     0,
    "orders_matched":    0,
    "contacts_created":  0,
    "orders_created":    0,
    "orders_updated":    0,
    "comms_synced":      0,
    "agents_run":        0,
    "agent_errors":      0,
    "error":             None,
}


def _run_import_pipeline(api_key: str, days_back: int, max_pages: int) -> None:
    """
    Full sequential pipeline:
      1. Sync ALL historic Shipday orders into shipday_orders_raw
      2. Pull all notes / feedback / proof-of-delivery into shipday_communications
      3. Refresh engagement rollups (evidence layer)
      4. Run the Claude agent cycle for every active contact
    """
    from app.routers.shipday_sync import _run_feedback_sync
    from app.routers.agents import _run_full_cycle

    global _pipeline_state
    _pipeline_state.update({
        "running":          True,
        "phase":            "orders",
        "started_at":       datetime.now(timezone.utc).isoformat(),
        "completed_at":     None,
        "orders_synced":    0,
        "orders_matched":   0,
        "contacts_created": 0,
        "orders_created":   0,
        "orders_updated":   0,
        "comms_synced":     0,
        "agents_run":       0,
        "agent_errors":     0,
        "error":            None,
    })

    try:
        # ── Phase 1: Sync all historic orders ──────────────────────────────
        logger.info("=== PIPELINE Phase 1: Syncing historic Shipday orders ===")
        from_date = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
        _run_historical_sync(api_key, from_date, max_pages)
        _pipeline_state["orders_synced"]    = _sync_state.get("orders_synced", 0)
        _pipeline_state["orders_matched"]   = _sync_state.get("orders_matched", 0)
        _pipeline_state["contacts_created"] = _sync_state.get("contacts_created", 0)
        _pipeline_state["orders_created"]   = _sync_state.get("orders_created", 0)
        _pipeline_state["orders_updated"]   = _sync_state.get("orders_updated", 0)
        logger.info(
            "Pipeline Phase 1 done: synced=%d matched=%d contacts_created=%d "
            "orders_created=%d orders_updated=%d errors=%d",
            _pipeline_state["orders_synced"],
            _pipeline_state["orders_matched"],
            _pipeline_state["contacts_created"],
            _pipeline_state["orders_created"],
            _pipeline_state["orders_updated"],
            _sync_state.get("errors", 0),
        )

        # ── Phase 2: Pull all notes / feedback into evidence ───────────────
        _pipeline_state["phase"] = "feedback"
        logger.info("=== PIPELINE Phase 2: Syncing all Shipday feedback & notes ===")
        _run_feedback_sync(days_back=days_back, all_historical=True)
        from app.routers import shipday_sync as _ss
        _pipeline_state["comms_synced"] = _ss._sync_state.get("orders_with_comms", 0)
        logger.info(
            "Pipeline Phase 2 done: comms_synced=%d positive=%d negative=%d",
            _pipeline_state["comms_synced"],
            _ss._sync_state.get("feedback_positive", 0),
            _ss._sync_state.get("feedback_negative", 0),
        )

        # ── Phase 3: Refresh engagement rollups (evidence) ────────────────
        _pipeline_state["phase"] = "rollups"
        logger.info("=== PIPELINE Phase 3: Refreshing engagement rollups ===")
        try:
            with get_cursor(commit=True) as cur:
                cur.execute("SELECT refresh_engagement_rollups()")
            logger.info("Pipeline Phase 3 done: engagement rollups refreshed")
        except Exception as e:
            logger.warning("Pipeline Phase 3: rollup refresh failed (non-fatal): %s", e)

        # ── Phase 4: Run agent cycle for all active contacts ───────────────
        _pipeline_state["phase"] = "agents"
        logger.info("=== PIPELINE Phase 4: Running agent cycle for all contacts ===")
        with get_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT id FROM contacts
                WHERE lifecycle_segment != 'optout'
                  AND (email IS NOT NULL OR phone IS NOT NULL)
                ORDER BY last_order_at DESC NULLS LAST
                LIMIT 1000
                """
            )
            contact_ids = [r["id"] for r in cur.fetchall()]

        logger.info("Pipeline Phase 4: %d contacts to process", len(contact_ids))
        for i, cid in enumerate(contact_ids, 1):
            try:
                _run_full_cycle(cid)
                _pipeline_state["agents_run"] += 1
            except Exception as e:
                _pipeline_state["agent_errors"] += 1
                logger.warning("Pipeline Phase 4: agent cycle failed contact_id=%s: %s", cid, e)
            if i % 50 == 0:
                logger.info(
                    "Pipeline Phase 4 progress: %d/%d processed (%d errors)",
                    i, len(contact_ids), _pipeline_state["agent_errors"],
                )

        logger.info(
            "Pipeline Phase 4 done: agents_run=%d errors=%d",
            _pipeline_state["agents_run"],
            _pipeline_state["agent_errors"],
        )

    except Exception as e:
        logger.error("Import pipeline crashed: %s", e, exc_info=True)
        _pipeline_state["error"] = str(e)

    finally:
        _pipeline_state["running"]      = False
        _pipeline_state["phase"]        = "done"
        _pipeline_state["completed_at"] = datetime.now(timezone.utc).isoformat()
        logger.info(
            "=== PIPELINE complete: orders=%d contacts_created=%d "
            "orders_created=%d orders_updated=%d comms=%d agents=%d agent_errors=%d ===",
            _pipeline_state["orders_synced"],
            _pipeline_state["contacts_created"],
            _pipeline_state["orders_created"],
            _pipeline_state["orders_updated"],
            _pipeline_state["comms_synced"],
            _pipeline_state["agents_run"],
            _pipeline_state["agent_errors"],
        )


class ImportAllRequest(BaseModel):
    days_back:          int  = 365   # How many days of Shipday history to pull
    max_pages:          int  = 500   # Safety cap on order pagination


@router.post("/import-all-and-run-agents")
async def import_all_and_run_agents(req: ImportAllRequest, background_tasks: BackgroundTasks):
    """
    Full import pipeline (runs in background):
      1. Pull ALL historic Shipday orders into the database
      2. Pull all delivery notes, customer feedback, and proof-of-delivery
         into the evidence layer (shipday_communications)
      3. Refresh engagement rollups so agents have up-to-date signals
      4. Run the Claude agent cycle for every active contact

    Poll /api/shipday/import-pipeline-status for progress.
    """
    if _pipeline_state["running"]:
        return {"status": "already_running", "state": _pipeline_state}

    if _sync_state["running"]:
        return {
            "status": "blocked",
            "reason": "A historical sync is already running. Wait for it to finish.",
            "sync_state": _sync_state,
        }

    api_key = _get_shipday_key()
    background_tasks.add_task(_run_import_pipeline, api_key, req.days_back, req.max_pages)
    return {
        "status":    "started",
        "days_back": req.days_back,
        "message":   (
            "Full import pipeline started in background. "
            "Phases: orders → feedback/notes → rollups → agents. "
            "Poll /api/shipday/import-pipeline-status for progress."
        ),
    }


@router.get("/import-pipeline-status")
def import_pipeline_status():
    """Return current status of the full import pipeline."""
    return {"pipeline_state": _pipeline_state}




# ─── Shipday Feedback Sync (was shipday_sync.py) ──────────────────────────────

def _fetch_order_detail(api_key: str, shipday_order_id: str) -> Optional[dict]:
    url = f"{SHIPDAY_API_BASE}/orders/{shipday_order_id}"
    headers = {"Authorization": f"Basic {api_key}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning("Shipday order %s fetch failed: %s", shipday_order_id, e)
        return None


def _store_order_communications(shipday_order_id: str, order: dict, occurred_at: Optional[str]) -> list:
    stored = []
    with get_cursor(commit=True) as cur:
        feedback = (order.get("feedback") or "").strip()
        if feedback:
            cur.execute(
                "SELECT store_shipday_communication(%s, 'customer_feedback', %s, NULL, %s::jsonb, %s::timestamptz)",
                (shipday_order_id, feedback, json.dumps({"raw_feedback": feedback}), occurred_at),
            )
            stored.append("customer_feedback")
        instr = (order.get("deliveryInstruction") or "").strip()
        if instr:
            cur.execute(
                "SELECT store_shipday_communication(%s, 'delivery_instruction', %s, NULL, %s::jsonb, %s::timestamptz)",
                (shipday_order_id, instr, json.dumps({"raw_instruction": instr}), occurred_at),
            )
            stored.append("delivery_instruction")
        pod = order.get("proofOfDelivery") or {}
        pic_paths = pod.get("picturePaths") or []
        sig_path = (pod.get("signaturePath") or "").strip()
        all_urls = ([sig_path] if sig_path else []) + [p for p in pic_paths if p]
        if all_urls:
            cur.execute(
                "SELECT store_shipday_communication(%s, 'proof_of_delivery', NULL, %s::text[], %s::jsonb, %s::timestamptz)",
                (shipday_order_id, all_urls, json.dumps({"proof_of_delivery": pod}), occurred_at),
            )
            stored.append("proof_of_delivery")
    return stored


def _create_feedback_opportunity(contact_id: int, shipday_order_id: str, sentiment: str) -> None:
    if sentiment == "negative":
        priority, reason, suggested_msg = (
            "hot",
            "Customer left negative delivery feedback. Reach out with apology and recovery offer.",
            "Hi! We saw your recent DabbahWala delivery didn't meet expectations — we're really sorry. Can we make it right?",
        )
    elif sentiment == "positive":
        priority, reason, suggested_msg = (
            "warm",
            "Customer left positive feedback — strike while iron is hot with a re-order nudge.",
            "Hi! So glad you enjoyed your DabbahWala delivery! We'd love to cook for you again.",
        )
    else:
        return
    try:
        with get_cursor(commit=True) as cur:
            cur.execute(
                """INSERT INTO opportunities (contact_id, action, priority, reason, suggested_message, confidence_score, status)
                   VALUES (%s, 'send_sms', %s, %s, %s, %s, 'pending') ON CONFLICT DO NOTHING""",
                (contact_id, priority, reason, suggested_msg, 0.85 if sentiment == "negative" else 0.70),
            )
    except Exception as e:
        logger.warning("Failed to create feedback opportunity for contact %s: %s", contact_id, e)


_feedback_sync_state: dict = {
    "running": False, "started_at": None, "orders_checked": 0,
    "orders_with_comms": 0, "feedback_positive": 0, "feedback_negative": 0,
    "feedback_neutral": 0, "opportunities_created": 0, "errors": 0, "completed_at": None,
}


def _run_feedback_sync(days_back: int = 7, all_historical: bool = False) -> None:
    global _feedback_sync_state
    api_key = os.environ.get("SHIPDAY_API_KEY", "") or os.environ.get("SHIPDAY_KEY", "")
    if not api_key:
        logger.error("Feedback sync: SHIPDAY_API_KEY not set — aborting")
        _feedback_sync_state["running"] = False
        return
    _feedback_sync_state.update({
        "running": True, "started_at": datetime.now(timezone.utc).isoformat(),
        "orders_checked": 0, "orders_with_comms": 0, "feedback_positive": 0,
        "feedback_negative": 0, "feedback_neutral": 0, "opportunities_created": 0,
        "errors": 0, "completed_at": None,
    })
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    try:
        with get_cursor(commit=False) as cur:
            if all_historical:
                cur.execute(
                    """SELECT sor.shipday_order_id, sor.contact_id, sor.actual_delivery
                       FROM shipday_orders_raw sor
                       WHERE sor.shipday_status IN ('COMPLETED','DELIVERED') AND sor.contact_id IS NOT NULL
                         AND NOT EXISTS (SELECT 1 FROM shipday_communications sc
                                         WHERE sc.shipday_order_id=sor.shipday_order_id AND sc.comm_type='customer_feedback')
                       ORDER BY sor.actual_delivery DESC NULLS LAST"""
                )
            else:
                cur.execute(
                    """SELECT sor.shipday_order_id, sor.contact_id, sor.actual_delivery
                       FROM shipday_orders_raw sor
                       WHERE sor.shipday_status IN ('COMPLETED','DELIVERED') AND sor.contact_id IS NOT NULL
                         AND (sor.actual_delivery >= %s OR sor.synced_at >= %s)
                         AND NOT EXISTS (SELECT 1 FROM shipday_communications sc
                                         WHERE sc.shipday_order_id=sor.shipday_order_id AND sc.comm_type='customer_feedback')
                       ORDER BY sor.actual_delivery DESC NULLS LAST""",
                    (cutoff, cutoff),
                )
            pending = [dict(r) for r in cur.fetchall()]
        for row in pending:
            order_id = row["shipday_order_id"]
            contact_id = row["contact_id"]
            occurred_at = row["actual_delivery"].isoformat() if row["actual_delivery"] else None
            try:
                order = _fetch_order_detail(api_key, order_id)
                _feedback_sync_state["orders_checked"] += 1
                if not order:
                    continue
                stored = _store_order_communications(order_id, order, occurred_at)
                if stored:
                    _feedback_sync_state["orders_with_comms"] += 1
                if "customer_feedback" in stored:
                    with get_cursor(commit=False) as cur:
                        cur.execute(
                            "SELECT sentiment FROM shipday_communications WHERE shipday_order_id=%s AND comm_type='customer_feedback'",
                            (order_id,),
                        )
                        sr = cur.fetchone()
                    sentiment = sr["sentiment"] if sr else "neutral"
                    if sentiment == "positive":
                        _feedback_sync_state["feedback_positive"] += 1
                    elif sentiment == "negative":
                        _feedback_sync_state["feedback_negative"] += 1
                    else:
                        _feedback_sync_state["feedback_neutral"] += 1
                    _create_feedback_opportunity(contact_id, order_id, sentiment)
                    if sentiment in ("positive", "negative"):
                        _feedback_sync_state["opportunities_created"] += 1
                time.sleep(0.25)
            except Exception as e:
                _feedback_sync_state["errors"] += 1
                logger.warning("Feedback sync: order %s failed: %s", order_id, e)
    except Exception as e:
        logger.error("Feedback sync crashed: %s", e, exc_info=True)
        _feedback_sync_state["errors"] += 1
    finally:
        _feedback_sync_state["running"] = False
        _feedback_sync_state["completed_at"] = datetime.now(timezone.utc).isoformat()


class FeedbackSyncRequest(BaseModel):
    days_back:          int  = 7
    run_in_background:  bool = True
    all_historical:     bool = False


@router.post("/sync-feedback")
async def sync_feedback(req: FeedbackSyncRequest, background_tasks: BackgroundTasks):
    """
    Poll Shipday for customer feedback, delivery instructions, and
    proof-of-delivery on all delivered orders in the last N days.

    Set all_historical=True to sync ALL delivered orders regardless of date.

    Stores results in shipday_communications and creates Opportunities for
    actionable sentiment (negative → recovery, positive → re-order nudge).

    Runs in the background by default — check /api/shipday/feedback-stats
    for progress and results.
    """
    if _feedback_sync_state["running"]:
        return {"status": "already_running", "state": _feedback_sync_state}

    if req.run_in_background:
        background_tasks.add_task(_run_feedback_sync, req.days_back, req.all_historical)
        return {
            "status":        "started",
            "days_back":     req.days_back,
            "all_historical": req.all_historical,
            "message":       "Feedback sync running in background. Poll /api/shipday/feedback-stats for results.",
        }

    _run_feedback_sync(req.days_back, req.all_historical)
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

