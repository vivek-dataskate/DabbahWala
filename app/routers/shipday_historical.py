"""
Shipday Historical Data Fetcher + Inbound Webhook
===================================================
Fetches up to 1 year of Shipday delivery data and stores it in the database.
Also provides the "top 10 people to call" analysis using the urgency scoring model.

Endpoints:
  POST /api/shipday/sync-historical          — Fetch 1 year of Shipday data (paginated)
  GET  /api/shipday/sync-status              — How many orders synced so far
  GET  /api/shipday/top-calls                — 10 people to call with scripts
  POST /api/shipday/run-migration            — Run migration 034 (creates schema)
  POST /api/shipday/webhook                  — Inbound status updates from Shipday (update-only)

Authentication: Shipday API key from env: SHIPDAY_API_KEY
"""
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, BackgroundTasks, Request
from pydantic import BaseModel

from app.db import get_cursor

logger = logging.getLogger(__name__)

router = APIRouter()

SHIPDAY_API_BASE = "https://api.shipday.com"
SHIPDAY_API_KEY = None


def _get_shipday_key() -> str:
    key = os.environ.get("SHIPDAY_API_KEY", "")
    if not key:
        # Try alternate env var names
        key = os.environ.get("SHIPDAY_KEY", "")
    if not key:
        logger.error("SHIPDAY_API_KEY is not set in environment")
        raise HTTPException(
            status_code=500,
            detail="SHIPDAY_API_KEY not configured. Set it in your .env file.",
        )
    return key


# ─────────────────────────────────────────────────────────────────
# Shipday API client helpers
# ─────────────────────────────────────────────────────────────────

def _fetch_shipday_page(api_key: str, from_date: str, page: int = 1, limit: int = 100) -> list:
    """Fetch one page of orders from the Shipday API.

    Args:
        api_key:   Shipday API key
        from_date: ISO date string YYYY-MM-DD
        page:      Page number (1-indexed)
        limit:     Orders per page (max 100)

    Returns list of order dicts (may be empty if no more orders).
    """
    url = f"{SHIPDAY_API_BASE}/orders"
    headers = {
        "Authorization": f"Basic {api_key}",
        "Content-Type": "application/json",
    }
    params = {
        "from": from_date,
        "limit": limit,
        "page": page,
    }
    logger.debug("Shipday API request: page=%d from=%s limit=%d", page, from_date, limit)
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            raw = resp.json()
            # API returns either array or {orders: [...]} or {data: [...]}
            if isinstance(raw, list):
                return raw
            return raw.get("orders", raw.get("data", []))
    except httpx.HTTPStatusError as e:
        logger.error(
            "Shipday API HTTP error: status=%d body=%s",
            e.response.status_code,
            e.response.text[:200],
        )
        raise
    except httpx.TimeoutException as e:
        logger.error("Shipday API timeout on page=%d: %s", page, e)
        raise
    except Exception as e:
        logger.error("Shipday API unexpected error page=%d: %s", page, e, exc_info=True)
        raise


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
# Background sync worker
# ─────────────────────────────────────────────────────────────────

_sync_state = {
    "running": False,
    "started_at": None,
    "pages_fetched": 0,
    "orders_fetched": 0,
    "orders_synced": 0,
    "orders_matched": 0,
    "errors": 0,
    "last_error": None,
    "completed_at": None,
    "from_date": None,
}


def _run_historical_sync(api_key: str, from_date: str, max_pages: int = 500) -> None:
    """Background worker: paginate through all Shipday orders and sync to DB.

    Designed to be safe to run multiple times (idempotent via ON CONFLICT DO NOTHING).
    """
    global _sync_state
    _sync_state.update({
        "running": True,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "pages_fetched": 0,
        "orders_fetched": 0,
        "orders_synced": 0,
        "orders_matched": 0,
        "errors": 0,
        "last_error": None,
        "completed_at": None,
        "from_date": from_date,
    })
    logger.info(
        "=== Starting Shipday historical sync: from=%s max_pages=%d ===",
        from_date,
        max_pages,
    )

    try:
        page = 1
        consecutive_empty = 0
        while page <= max_pages:
            try:
                orders = _fetch_shipday_page(api_key, from_date, page=page)
            except Exception as e:
                _sync_state["errors"] += 1
                _sync_state["last_error"] = str(e)
                logger.warning("Page %d fetch failed: %s — retrying once", page, e)
                time.sleep(2)
                try:
                    orders = _fetch_shipday_page(api_key, from_date, page=page)
                except Exception as e2:
                    logger.error("Page %d retry also failed: %s — stopping sync", page, e2)
                    _sync_state["errors"] += 1
                    _sync_state["last_error"] = str(e2)
                    break

            if not orders:
                consecutive_empty += 1
                logger.info("Empty page %d (consecutive_empty=%d)", page, consecutive_empty)
                if consecutive_empty >= 2:
                    logger.info("Two consecutive empty pages — all orders fetched")
                    break
                page += 1
                continue

            consecutive_empty = 0
            _sync_state["pages_fetched"] += 1
            _sync_state["orders_fetched"] += len(orders)

            logger.info(
                "Syncing page %d: %d orders (total_fetched=%d total_synced=%d)",
                page,
                len(orders),
                _sync_state["orders_fetched"],
                _sync_state["orders_synced"],
            )

            for order in orders:
                try:
                    result = _sync_one_order(order)
                    if result.get("status") != "skipped":
                        _sync_state["orders_synced"] += 1
                    if result.get("matched"):
                        _sync_state["orders_matched"] += 1
                except Exception as e:
                    _sync_state["errors"] += 1
                    _sync_state["last_error"] = f"order {order.get('orderId', '?')}: {e}"
                    logger.warning(
                        "Failed to sync order %s: %s",
                        order.get("orderId", "?"),
                        e,
                    )

            page += 1
            # Small delay to be respectful to the API
            time.sleep(0.3)

    except Exception as e:
        logger.error("Historical sync crashed: %s", e, exc_info=True)
        _sync_state["last_error"] = str(e)

    finally:
        _sync_state["running"] = False
        _sync_state["completed_at"] = datetime.now(timezone.utc).isoformat()
        logger.info(
            "=== Shipday historical sync complete: pages=%d fetched=%d synced=%d matched=%d errors=%d ===",
            _sync_state["pages_fetched"],
            _sync_state["orders_fetched"],
            _sync_state["orders_synced"],
            _sync_state["orders_matched"],
            _sync_state["errors"],
        )


# ─────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────

class SyncRequest(BaseModel):
    days_back: int = 365          # How many days of history to fetch
    max_pages: int = 500          # Safety cap on pagination
    run_in_background: bool = True  # If False, sync synchronously (blocks)


@router.post("/sync-historical")
async def sync_historical(req: SyncRequest, background_tasks: BackgroundTasks):
    """Fetch and store up to N days of Shipday delivery history.

    The sync is idempotent — re-running will update existing records and skip
    duplicates. Matched orders (where we found a contact in our DB) will have
    their delivery status and order events updated.

    By default runs in the background — poll /api/shipday/sync-status for progress.
    """
    if _sync_state["running"]:
        logger.warning("Historical sync already in progress — returning current state")
        return {"status": "already_running", "state": _sync_state}

    api_key = _get_shipday_key()
    from_date = (datetime.now(timezone.utc) - timedelta(days=req.days_back)).strftime("%Y-%m-%d")

    logger.info(
        "Historical sync requested: days_back=%d from=%s max_pages=%d background=%s",
        req.days_back,
        from_date,
        req.max_pages,
        req.run_in_background,
    )

    if req.run_in_background:
        background_tasks.add_task(_run_historical_sync, api_key, from_date, req.max_pages)
        return {
            "status": "started",
            "from_date": from_date,
            "days_back": req.days_back,
            "message": "Sync running in background. Poll /api/shipday/sync-status for progress.",
        }
    else:
        # Synchronous — blocks until complete (only for testing)
        _run_historical_sync(api_key, from_date, req.max_pages)
        return {"status": "complete", "state": _sync_state}


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
        "sync_state": _sync_state,
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


# ─── Shipday status map → our delivery_status_type enum ─────────────────────
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


@router.get("/webhook")
async def shipday_webhook_ping(request: Request):
    """Shipday verification ping — just needs a 200 OK."""
    logger.info(
        "Shipday GET /webhook ping — headers: %s",
        dict(request.headers),
    )
    return {"status": "ok"}


@router.post("/webhook")
async def shipday_webhook(request: Request):
    """
    Inbound Shipday webhook — receives order status change callbacks.

    ONLY updates existing records (shipday_orders_raw + delivery_status).
    Never creates new orders, contacts, or events.
    """
    # Log everything immediately so we can see what Shipday sends
    try:
        body = await request.body()
    except Exception as e:
        logger.error("Shipday webhook: failed to read body: %s", e)
        raise HTTPException(status_code=400, detail="Could not read request body")

    logger.info(
        "Shipday POST /webhook — content_type=%s content_length=%s auth=%s body_bytes=%d body=%s",
        request.headers.get("content-type", "(none)"),
        request.headers.get("content-length", "(none)"),
        request.headers.get("authorization", "(none)"),
        len(body),
        body[:500].decode("utf-8", errors="replace"),
    )

    # Empty or whitespace-only body = Shipday verification ping
    if not body or not body.strip():
        logger.info("Shipday webhook: empty body — verification ping, returning 200")
        return {"status": "ok"}

    # Non-empty body: verify Bearer token if configured
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

    order_id    = str(payload.get("orderId") or payload.get("id") or "").strip()
    if not order_id:
        logger.info("Shipday webhook: no orderId in payload (likely verification) — returning 200. payload=%s", str(payload)[:300])
        return {"status": "ok"}

    raw_status  = (payload.get("orderStatus") or payload.get("status") or "UNKNOWN").upper()
    our_status  = _SHIPDAY_TO_STATUS.get(raw_status)   # None if unknown status
    order_number = payload.get("orderNumber") or ""
    carrier      = payload.get("assignedCarrier") or {}
    driver_name  = carrier.get("name")
    driver_phone = carrier.get("phone")

    # Parse actual delivery time if present
    actual_delivery: Optional[str] = None
    try:
        actual_delivery = payload.get("actualDeliveryTime") or None
    except Exception:
        pass

    logger.info(
        "Shipday webhook: order_id=%s status=%s → %s driver=%s",
        order_id, raw_status, our_status, driver_name
    )

    with get_cursor(commit=True) as cur:
        # 1. Look up the existing order — reject unknown orders (no creation)
        cur.execute(
            """SELECT contact_id, customer_phone, customer_email, order_number
               FROM shipday_orders_raw
               WHERE shipday_order_id = %s""",
            (order_id,)
        )
        existing = cur.fetchone()
        if not existing:
            logger.info(
                "Shipday webhook: order_id=%s not found in shipday_orders_raw — ignoring",
                order_id
            )
            return {"status": "ignored", "reason": "order_not_found", "order_id": order_id}

        contact_id   = existing["contact_id"]
        order_ref    = order_number or existing.get("order_number") or order_id

        # 2. Update shipday_orders_raw — status + driver info
        cur.execute(
            """UPDATE shipday_orders_raw
               SET shipday_status  = %s,
                   actual_delivery = COALESCE(%s::timestamptz, actual_delivery),
                   driver_name     = COALESCE(%s, driver_name),
                   driver_phone    = COALESCE(%s, driver_phone),
                   synced_at       = NOW(),
                   raw_payload     = raw_payload || %s::jsonb
               WHERE shipday_order_id = %s""",
            (
                raw_status,
                actual_delivery,
                driver_name,
                driver_phone,
                json.dumps({"webhook_updated_at": datetime.now(timezone.utc).isoformat()}),
                order_id,
            )
        )

        # 3. Update orders table if matched by external order ID (align delivery date)
        if contact_id and our_status == "delivered" and order_ref:
            cur.execute(
                """UPDATE orders
                   SET delivery_date = CURRENT_DATE,
                       metadata = metadata || %s::jsonb
                   WHERE contact_id = %s
                     AND (order_id_external = %s OR order_id_external = %s)
                     AND delivery_date IS NULL""",
                (
                    json.dumps({"shipday_status": raw_status, "shipday_order_id": order_id}),
                    contact_id,
                    order_ref,
                    order_id,
                )
            )

        # 4. Insert delivery_status row for this status change (audit trail)
        if contact_id and our_status:
            cur.execute(
                """INSERT INTO delivery_status
                     (contact_id, order_ref, status, updated_by, occurred_at, metadata)
                   VALUES (%s, %s, %s, 'shipday_webhook', NOW(), %s)""",
                (
                    contact_id,
                    order_ref,
                    our_status,
                    json.dumps({
                        "shipday_order_id": order_id,
                        "raw_status":       raw_status,
                        "source":           "webhook",
                        "driver_name":      driver_name,
                    }),
                )
            )

    return {
        "status":        "ok",
        "order_id":      order_id,
        "shipday_status": raw_status,
        "mapped_status": our_status,
        "contact_found": contact_id is not None,
    }
