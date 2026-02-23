"""
Bulk Messaging — Broadcast Jobs

Handles two scenarios:
  1. Promotional / festival / event blasts  → all opted-in customers
  2. Delay alerts (storm, traffic, etc.)    → contacts with active orders on a given date

Flow
----
1. Create a job  POST /api/broadcasts/
   (or use the quick shortcut  POST /api/broadcasts/delay-alert)
2. Queue it      POST /api/broadcasts/{id}/queue
   → populates broadcast_recipients and sets status='queued'
3. n8n polls     GET  /api/broadcasts/pending-recipients  (every 5 min)
   → claims a batch, sends via Telnyx (SMS) or SMTP (email)
4. Mark result   POST /api/broadcasts/recipients/{id}/sent
                 POST /api/broadcasts/recipients/{id}/failed
5. Job auto-completes when all recipients reach a terminal state
"""

import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_cursor

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------

class BroadcastCreate(BaseModel):
    title: str
    broadcast_type: str          # 'promotional' | 'delay_alert'
    channels: list[str] = ["sms", "email"]
    sms_message: Optional[str] = None
    email_subject: Optional[str] = None
    email_body: Optional[str] = None
    target_type: str             # 'all_customers' | 'active_orders'
    target_date: Optional[date] = None   # required for delay_alert / active_orders
    created_by: Optional[str] = None


class DelayAlertCreate(BaseModel):
    """Convenience payload for the common delay-alert use-case."""
    sms_message: str
    email_subject: str
    email_body: str
    target_date: Optional[date] = None   # defaults to today
    created_by: Optional[str] = None


class RecipientFailure(BaseModel):
    error_message: Optional[str] = None


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_VALID_TYPES    = {"promotional", "delay_alert"}
_VALID_TARGETS  = {"all_customers", "active_orders"}
_VALID_CHANNELS = {"sms", "email"}


def _validate_broadcast(payload: BroadcastCreate):
    if payload.broadcast_type not in _VALID_TYPES:
        raise HTTPException(400, f"broadcast_type must be one of {_VALID_TYPES}")
    if payload.target_type not in _VALID_TARGETS:
        raise HTTPException(400, f"target_type must be one of {_VALID_TARGETS}")
    bad_ch = set(payload.channels) - _VALID_CHANNELS
    if bad_ch:
        raise HTTPException(400, f"Unknown channels: {bad_ch}. Must be subset of {_VALID_CHANNELS}")
    if payload.broadcast_type == "delay_alert" and payload.target_type != "active_orders":
        raise HTTPException(400, "delay_alert broadcasts must use target_type='active_orders'")
    if payload.target_type == "active_orders" and not payload.target_date:
        raise HTTPException(400, "target_date is required when target_type='active_orders'")


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------

def _populate_recipients_all_customers(cur, job_id: int, channels: list[str]) -> int:
    """Insert recipients for ALL opted-in customers (promotional broadcast)."""
    cur.execute(
        """
        INSERT INTO broadcast_recipients (job_id, contact_id, channel)
        SELECT %s, c.id, ch.channel
        FROM contacts c
        CROSS JOIN UNNEST(%s::text[]) AS ch(channel)
        WHERE c.lifecycle_segment NOT IN ('optout')
          AND (
               (ch.channel = 'sms'   AND c.sms_promo_enabled  IS TRUE AND c.phone  IS NOT NULL)
            OR (ch.channel = 'email' AND c.email_promo_enabled IS TRUE AND c.email IS NOT NULL)
          )
        ON CONFLICT (job_id, contact_id, channel) DO NOTHING
        RETURNING id
        """,
        (job_id, channels),
    )
    return len(cur.fetchall())


def _populate_recipients_active_orders(cur, job_id: int, channels: list[str], target_date: date) -> int:
    """Insert recipients for contacts with deliveries on target_date.
    Uses delivery_date when available (set during CSV intake), falls back to order_date
    for legacy records without a delivery_date.
    """
    cur.execute(
        """
        INSERT INTO broadcast_recipients (job_id, contact_id, channel)
        SELECT %s, o.contact_id, ch.channel
        FROM (
            SELECT DISTINCT contact_id
            FROM orders
            WHERE COALESCE(delivery_date, order_date) = %s
        ) o
        JOIN contacts c ON c.id = o.contact_id
        CROSS JOIN UNNEST(%s::text[]) AS ch(channel)
        WHERE c.lifecycle_segment NOT IN ('optout')
          AND (
               (ch.channel = 'sms'   AND c.phone  IS NOT NULL)
            OR (ch.channel = 'email' AND c.email IS NOT NULL)
          )
        ON CONFLICT (job_id, contact_id, channel) DO NOTHING
        RETURNING id
        """,
        (job_id, target_date, channels),
    )
    return len(cur.fetchall())


def _maybe_complete_job(cur, job_id: int):
    """Mark job 'completed' when no recipients remain in a non-terminal state."""
    cur.execute(
        """
        SELECT COUNT(*) AS remaining
        FROM broadcast_recipients
        WHERE job_id = %s AND status IN ('pending', 'sending')
        """,
        (job_id,),
    )
    if cur.fetchone()["remaining"] == 0:
        cur.execute(
            "UPDATE broadcast_jobs SET status='completed', completed_at=NOW() WHERE id=%s AND status='sending'",
            (job_id,),
        )
        logger.info("Broadcast job id=%d completed", job_id)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/")
def create_broadcast(payload: BroadcastCreate):
    """
    Create a broadcast job in **draft** state.

    Call `POST /api/broadcasts/{id}/queue` next to populate recipients
    and start sending.
    """
    _validate_broadcast(payload)
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO broadcast_jobs
              (title, broadcast_type, channels, sms_message,
               email_subject, email_body, target_type, target_date, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, created_at
            """,
            (
                payload.title,
                payload.broadcast_type,
                payload.channels,
                payload.sms_message,
                payload.email_subject,
                payload.email_body,
                payload.target_type,
                payload.target_date,
                payload.created_by,
            ),
        )
        row = cur.fetchone()
    logger.info(
        "Broadcast job created id=%d title=%s type=%s target=%s",
        row["id"], payload.title, payload.broadcast_type, payload.target_type,
    )
    return {"status": "created", "job_id": row["id"], "created_at": row["created_at"]}


@router.post("/delay-alert")
def create_delay_alert(payload: DelayAlertCreate):
    """
    Convenience endpoint: **create + queue** a delay alert for a specific day's
    active orders in a single call.

    Use this when orders are delayed due to weather, traffic, staffing, etc.
    Targets every contact who placed an order on `target_date` (defaults to today).
    Sends both SMS and email.
    """
    target_date = payload.target_date or date.today()
    title = f"Delay Alert — {target_date.isoformat()}"

    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO broadcast_jobs
              (title, broadcast_type, channels, sms_message, email_subject,
               email_body, target_type, target_date, status, created_by)
            VALUES (%s, 'delay_alert', ARRAY['sms','email'], %s, %s, %s,
                    'active_orders', %s, 'queued', %s)
            RETURNING id
            """,
            (
                title,
                payload.sms_message,
                payload.email_subject,
                payload.email_body,
                target_date,
                payload.created_by,
            ),
        )
        job_id = cur.fetchone()["id"]
        logger.info("Delay alert job created id=%d date=%s", job_id, target_date)

        count = _populate_recipients_active_orders(cur, job_id, ["sms", "email"], target_date)
        cur.execute(
            "UPDATE broadcast_jobs SET total_recipients=%s, started_at=NOW() WHERE id=%s",
            (count, job_id),
        )

    logger.info("Delay alert id=%d queued %d recipients for %s", job_id, count, target_date)
    return {
        "status": "queued",
        "job_id": job_id,
        "target_date": target_date.isoformat(),
        "total_recipients": count,
    }


@router.post("/{job_id}/queue")
def queue_broadcast(job_id: int):
    """
    Populate recipients for a **draft** job and transition it to **queued**.

    - `all_customers` → every contact with `sms_promo_enabled` / `email_promo_enabled`
    - `active_orders` → contacts with orders on `target_date`

    n8n picks up queued jobs automatically every 5 minutes via
    `GET /api/broadcasts/pending-recipients`.
    """
    with get_cursor() as cur:
        cur.execute("SELECT * FROM broadcast_jobs WHERE id = %s", (job_id,))
        job = cur.fetchone()
        if not job:
            raise HTTPException(404, f"Broadcast job {job_id} not found")
        if job["status"] != "draft":
            raise HTTPException(400, f"Job {job_id} is in status '{job['status']}'; only 'draft' jobs can be queued")

        channels = list(job["channels"])
        if job["target_type"] == "all_customers":
            count = _populate_recipients_all_customers(cur, job_id, channels)
        else:
            target_date = job["target_date"] or date.today()
            count = _populate_recipients_active_orders(cur, job_id, channels, target_date)

        cur.execute(
            "UPDATE broadcast_jobs SET status='queued', total_recipients=%s, started_at=NOW() WHERE id=%s",
            (count, job_id),
        )

    logger.info("Broadcast job id=%d queued with %d recipients", job_id, count)
    return {"status": "queued", "job_id": job_id, "total_recipients": count}


@router.get("/")
def list_broadcasts(limit: int = 20, offset: int = 0):
    """List broadcast jobs, newest first."""
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT id, title, broadcast_type, channels, target_type, target_date,
                   status, total_recipients, sent_sms, sent_email, failed_count,
                   created_by, created_at, started_at, completed_at
            FROM broadcast_jobs
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )
        return cur.fetchall()


@router.get("/pending-recipients")
def get_pending_recipients(limit: int = 50):
    """
    Claim and return a batch of pending broadcast recipients for n8n to dispatch.

    Uses `SELECT … FOR UPDATE SKIP LOCKED` to prevent double-sending when
    multiple n8n executions overlap.  Returns full contact + message context
    so n8n can send without extra DB round-trips.
    """
    with get_cursor() as cur:
        # Atomically claim a batch
        cur.execute(
            """
            WITH claimed AS (
                SELECT br.id
                FROM broadcast_recipients br
                JOIN broadcast_jobs bj ON bj.id = br.job_id
                WHERE br.status = 'pending'
                  AND bj.status IN ('queued', 'sending')
                ORDER BY bj.id, br.id
                LIMIT %s
                FOR UPDATE OF br SKIP LOCKED
            )
            UPDATE broadcast_recipients br
            SET status = 'sending'
            FROM claimed
            WHERE br.id = claimed.id
            RETURNING br.id AS recipient_id, br.job_id, br.contact_id, br.channel
            """,
            (limit,),
        )
        claimed = cur.fetchall()
        if not claimed:
            return []

        recipient_ids = [r["recipient_id"] for r in claimed]

        # Fetch full context in one query
        cur.execute(
            """
            SELECT
                br.id   AS recipient_id,
                br.job_id,
                br.channel,
                c.id    AS contact_id,
                c.first_name,
                c.email,
                c.phone,
                bj.sms_message,
                bj.email_subject,
                bj.email_body,
                bj.broadcast_type,
                bj.title AS job_title
            FROM broadcast_recipients br
            JOIN contacts c ON c.id = br.contact_id
            JOIN broadcast_jobs bj ON bj.id = br.job_id
            WHERE br.id = ANY(%s)
            ORDER BY br.id
            """,
            (recipient_ids,),
        )
        rows = cur.fetchall()

        # Transition job(s) from queued → sending
        job_ids = list({r["job_id"] for r in claimed})
        cur.execute(
            "UPDATE broadcast_jobs SET status='sending' WHERE id = ANY(%s) AND status='queued'",
            (job_ids,),
        )

        logger.info("Claimed %d broadcast recipients for dispatch", len(rows))
        return rows


@router.post("/recipients/{recipient_id}/sent")
def mark_recipient_sent(recipient_id: int):
    """Mark a broadcast recipient as successfully sent and update job counters."""
    with get_cursor() as cur:
        cur.execute(
            """
            UPDATE broadcast_recipients
            SET status='sent', sent_at=NOW()
            WHERE id=%s
            RETURNING job_id, channel
            """,
            (recipient_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, f"Recipient {recipient_id} not found")

        col = "sent_sms" if row["channel"] == "sms" else "sent_email"
        cur.execute(
            f"UPDATE broadcast_jobs SET {col} = {col} + 1 WHERE id = %s",
            (row["job_id"],),
        )
        _maybe_complete_job(cur, row["job_id"])

    return {"status": "ok"}


@router.post("/recipients/{recipient_id}/failed")
def mark_recipient_failed(recipient_id: int, payload: RecipientFailure = RecipientFailure()):
    """Mark a broadcast recipient as failed and update job counters."""
    with get_cursor() as cur:
        cur.execute(
            """
            UPDATE broadcast_recipients
            SET status='failed', error_message=%s
            WHERE id=%s
            RETURNING job_id
            """,
            (payload.error_message, recipient_id),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, f"Recipient {recipient_id} not found")

        cur.execute(
            "UPDATE broadcast_jobs SET failed_count = failed_count + 1 WHERE id = %s",
            (row["job_id"],),
        )
        _maybe_complete_job(cur, row["job_id"])

    return {"status": "ok"}


@router.get("/{job_id}")
def get_broadcast(job_id: int):
    """Get broadcast job details with per-channel progress breakdown."""
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT * FROM broadcast_jobs WHERE id = %s", (job_id,))
        job = cur.fetchone()
        if not job:
            raise HTTPException(404, f"Broadcast job {job_id} not found")

        cur.execute(
            """
            SELECT
                channel,
                COUNT(*) FILTER (WHERE status = 'pending')  AS pending,
                COUNT(*) FILTER (WHERE status = 'sending')  AS sending,
                COUNT(*) FILTER (WHERE status = 'sent')     AS sent,
                COUNT(*) FILTER (WHERE status = 'failed')   AS failed,
                COUNT(*) FILTER (WHERE status = 'skipped')  AS skipped
            FROM broadcast_recipients
            WHERE job_id = %s
            GROUP BY channel
            """,
            (job_id,),
        )
        progress = cur.fetchall()

    return {**dict(job), "progress": [dict(p) for p in progress]}
