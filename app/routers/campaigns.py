import copy
import json
import logging
import re
from pathlib import Path
from typing import Optional

import httpx
import threading

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.config import ANTHROPIC_API_KEY, INSTANTLY_API_KEY
from app.db import get_cursor

logger = logging.getLogger(__name__)
router = APIRouter()


class PushLeadRequest(BaseModel):
    email: str
    first_name: str
    last_name: str
    phone: str
    campaign_name: str
    contact_id: Optional[int] = None


class PushLogEntry(BaseModel):
    queue_id: Optional[int] = None
    email: str
    to_campaign: str
    success: bool
    status_code: Optional[int] = None
    error_message: Optional[str] = None
    response_body: Optional[str] = None


@router.get("/active-contacts")
def get_active_contacts():
    """Return all contacts with an active campaign assignment (derived from lifecycle_segment)."""
    with get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT c.id AS contact_id,
                   c.email,
                   c.first_name,
                   c.last_name,
                   c.phone,
                   cr.default_campaign AS current_campaign,
                   cr.instantly_campaign_id
            FROM contacts c
            JOIN campaign_routing cr ON cr.lifecycle_segment = c.lifecycle_segment
            WHERE cr.instantly_campaign_id IS NOT NULL
              AND c.email IS NOT NULL
              AND c.email NOT LIKE '%@app.placeholder.local'
              AND c.lifecycle_segment NOT IN ('optout', 'cooling')
            ORDER BY c.id
        """)
        rows = cur.fetchall()
    logger.info("get_active_contacts — returned %d contacts with active campaigns", len(rows))
    return [dict(r) for r in rows]


@router.get("/active-contacts-stats")
def get_active_contacts_stats():
    """Diagnostic: show how many contacts are excluded by each filter."""
    with get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT
                COUNT(*)                                                              AS total_contacts,
                COUNT(*) FILTER (WHERE email IS NULL)                                 AS no_email,
                COUNT(*) FILTER (WHERE email LIKE '%%@app.placeholder.local')         AS placeholder_email,
                COUNT(*) FILTER (WHERE lifecycle_segment = 'optout')                  AS optout,
                COUNT(*) FILTER (WHERE lifecycle_segment = 'cooling')                 AS cooling,
                COUNT(*) FILTER (WHERE lifecycle_segment NOT IN ('optout','cooling')
                                   AND email IS NOT NULL
                                   AND email NOT LIKE '%%@app.placeholder.local')     AS returned_by_api
            FROM contacts
        """)
        row = dict(cur.fetchone())

        # Campaign distribution for contacts that pass all filters (derived from lifecycle_segment)
        cur.execute("""
            SELECT cr.default_campaign AS current_campaign, COUNT(*) AS cnt
            FROM contacts c
            JOIN campaign_routing cr ON cr.lifecycle_segment = c.lifecycle_segment
            WHERE c.email IS NOT NULL
              AND c.email NOT LIKE '%%@app.placeholder.local'
              AND c.lifecycle_segment NOT IN ('optout', 'cooling')
            GROUP BY cr.default_campaign
            ORDER BY cnt DESC
        """)
        campaign_dist = [dict(r) for r in cur.fetchall()]

    return {**row, "campaign_distribution": campaign_dist}


@router.post("/{queue_id}/executed")
def mark_executed(queue_id: int):
    with get_cursor() as cur:
        cur.execute("SELECT mark_campaign_executed(%s)", (queue_id,))
        return {"status": "ok"}


@router.post("/bulk-executed")
def bulk_executed(body: dict):
    """
    Mark a batch of action_queue push_instantly_lead rows as done.
    Called by the lifecycle_cycle_cron n8n workflow after processing each batch.
    Accepts {queue_ids: [int, ...]}.
    """
    queue_ids = body.get("queue_ids") or []
    if not queue_ids:
        return {"marked": 0}
    with get_cursor() as cur:
        cur.execute(
            """
            UPDATE action_queue
            SET status = 'done', executed_at = now()
            WHERE id = ANY(%s)
              AND action_type = 'push_instantly_lead'
            """,
            (queue_ids,),
        )
        marked = cur.rowcount
    return {"marked": marked}


@router.post("/push-lead")
def push_lead(body: PushLeadRequest):
    """
    Enqueue a lead to be pushed to an Instantly campaign via action_queue.
    n8n's Action Queue Executor picks this up within 30 minutes.
    Returns the new action_queue row id so callers can track it.
    """
    try:
        row = _get_routing_row(body.campaign_name)
    except HTTPException as e:
        raise e
    instantly_id = row.get("instantly_campaign_id")
    if not instantly_id:
        raise HTTPException(status_code=422, detail=f"campaign_routing has no instantly_campaign_id for {body.campaign_name}")

    payload = {
        "instantly_campaign_id": instantly_id,
        "campaign_name": body.campaign_name,
        "email": body.email,
        "first_name": body.first_name,
        "last_name": body.last_name,
        "phone": body.phone,
    }
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO action_queue (contact_id, action_type, payload)
                VALUES (%s, 'push_instantly_lead', %s::jsonb)
                RETURNING id
                """,
                (body.contact_id, json.dumps(payload)),
            )
            queue_id = cur.fetchone()["id"]
        logger.info("push-lead: enqueued action_queue id=%d email=%s campaign=%s", queue_id, body.email, body.campaign_name)
        return {"queued": True, "queue_id": queue_id, "instantly_campaign_id": instantly_id}
    except Exception as e:
        logger.error("push-lead: DB insert failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)[:300])


@router.get("/pending")
def get_pending_leads():
    """
    Return pending push_instantly_lead items from action_queue,
    joined with contacts for first/last name where contact_id is set.
    """
    with get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT
                aq.id            AS queue_id,
                aq.contact_id,
                c.first_name     AS contact_first_name,
                c.last_name      AS contact_last_name,
                aq.payload->>'email'         AS email,
                aq.payload->>'campaign_name' AS campaign_name,
                aq.payload->>'instantly_campaign_id' AS instantly_campaign_id,
                aq.status,
                aq.created_at
            FROM action_queue aq
            LEFT JOIN contacts c ON c.id = aq.contact_id
            WHERE aq.action_type = 'push_instantly_lead'
              AND aq.status = 'pending'
            ORDER BY aq.created_at DESC
            LIMIT 100
        """)
        rows = [dict(r) for r in cur.fetchall()]
    return rows


@router.post("/log-push")
def log_push(entry: PushLogEntry):
    """Record a push attempt result in campaign_push_log."""
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO campaign_push_log
                    (queue_id, email, to_campaign, success, status_code, error_message, response_body)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (entry.queue_id, entry.email, entry.to_campaign, entry.success,
                 entry.status_code, entry.error_message, entry.response_body),
            )
            log_id = cur.fetchone()["id"]
        return {"status": "ok", "id": log_id}
    except Exception as e:
        logger.error("log-push: insert failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)[:300])


@router.get("/push-log")
def get_push_log(limit: int = 20, success: Optional[bool] = None):
    """Return recent campaign push log entries, optionally filtered by success."""
    with get_cursor(commit=False) as cur:
        if success is None:
            cur.execute(
                "SELECT * FROM campaign_push_log ORDER BY created_at DESC LIMIT %s",
                (min(limit, 200),),
            )
        else:
            cur.execute(
                "SELECT * FROM campaign_push_log WHERE success = %s ORDER BY created_at DESC LIMIT %s",
                (success, min(limit, 200)),
            )
        return [dict(r) for r in cur.fetchall()]


# ── Campaign email template editor ─────────────────────────────────────────────

_DATA_DIR = Path(__file__).parent.parent.parent / "data" / "campaigns"

# ── Branded HTML email wrapper ──────────────────────────────────────────────────
# Applied at Instantly push time only. Local JSON stores raw inner <div> content
# so the dashboard editor stays clean. We use string concat to preserve
# {{merge_tags}} without Python f-string escaping issues.

_TEMPLATE_HEADER = (
    '<!DOCTYPE html><html lang="en">'
    '<head><meta charset="UTF-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1.0">'
    '<meta http-equiv="X-UA-Compatible" content="IE=edge"></head>'
    '<body style="margin:0;padding:0;background-color:#f7f2ed;">'
    '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#f7f2ed">'
    '<tr><td align="center" style="padding:28px 12px;">'
    '<table role="presentation" style="max-width:580px;width:100%;" cellspacing="0" cellpadding="0" border="0">'
    # ── Header bar ──
    '<tr><td style="background-color:#1a1a1a;border-radius:8px 8px 0 0;padding:24px 40px;text-align:center;">'
    '<div style="font-family:Georgia,\'Times New Roman\',serif;font-size:24px;font-weight:bold;'
    'color:#ffffff;letter-spacing:1px;">DabbahWala</div>'
    '<div style="font-family:Arial,Helvetica,sans-serif;font-size:10px;color:#b8956a;'
    'letter-spacing:3px;text-transform:uppercase;margin-top:6px;">'
    'Fresh &nbsp;&middot;&nbsp; Cooked to Order &nbsp;&middot;&nbsp; Delivered</div>'
    '</td></tr>'
    # ── Accent line ──
    '<tr><td bgcolor="#c8581a" style="height:3px;font-size:0;line-height:0;">&nbsp;</td></tr>'
    # ── Body ──
    '<tr><td style="background-color:#ffffff;padding:40px 48px;border-radius:0;">'
    '<div style="font-family:Georgia,\'Times New Roman\',serif;font-size:16px;'
    'line-height:1.8;color:#252525;">'
)

_TEMPLATE_FOOTER = (
    '</div>'
    '</td></tr>'
    # ── Footer ──
    '<tr><td style="background-color:#f7f2ed;border-radius:0 0 8px 8px;'
    'padding:20px 40px 16px;text-align:center;">'
    '<p style="font-family:Arial,Helvetica,sans-serif;font-size:11px;'
    'color:#aaa;margin:0;line-height:1.8;">'
    'DabbahWala &nbsp;&middot;&nbsp; Fresh meals, cooked to order<br>'
    '<a href="{{unsubscribeLink}}" style="color:#aaa;text-decoration:underline;">Unsubscribe</a>'
    '</p>'
    '</td></tr>'
    '</table>'
    '</td></tr>'
    '</table>'
    '</body></html>'
)


def _wrap_body(inner_html: str) -> str:
    """Wrap inner email body with the DabbahWala branded HTML template."""
    return _TEMPLATE_HEADER + inner_html + _TEMPLATE_FOOTER

# ── campaign_routing helpers (single source of truth) ────────────────────────

def _get_routing_rows() -> list[dict]:
    """Return all rows from campaign_routing as dicts."""
    with get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT default_campaign, instantly_campaign_id, instantly_campaign_name, template_file
            FROM campaign_routing
            WHERE instantly_campaign_id IS NOT NULL
            ORDER BY lifecycle_segment
        """)
        return [dict(r) for r in cur.fetchall()]


def _get_routing_row(campaign_name: str) -> dict:
    """Return the campaign_routing row for campaign_name or raise 404."""
    with get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT default_campaign, instantly_campaign_id, instantly_campaign_name, template_file
            FROM campaign_routing
            WHERE default_campaign = %s
        """, (campaign_name,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Unknown campaign: {campaign_name}")
    return dict(row)


def push_lead_to_instantly(
    email: str,
    first_name: str,
    last_name: str,
    phone: str,
    campaign_name: str,
    contact_id: int | None = None,
) -> bool:
    """
    Enqueue a lead push to an Instantly campaign via the action_queue.
    n8n's Action Queue Executor will pick this up and call Instantly directly.
    Returns True if enqueued, False if campaign is unknown.
    """
    try:
        row = _get_routing_row(campaign_name)
    except HTTPException:
        return False
    instantly_id = row.get("instantly_campaign_id")
    if not instantly_id:
        return False
    payload = {
        "instantly_campaign_id": instantly_id,
        "campaign_name": campaign_name,
        "email": email,
        "first_name": first_name,
        "last_name": last_name,
        "phone": phone,
    }
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO action_queue (contact_id, action_type, payload)
                VALUES (%s, 'push_instantly_lead', %s::jsonb)
                """,
                (contact_id, json.dumps(payload)),
            )
        return True
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Failed to enqueue push_instantly_lead: %s", e)
        return False


def _load_campaign_json(campaign_name: str) -> tuple[dict, dict]:
    """Return (routing_row, parsed_json) or raise HTTPException."""
    row = _get_routing_row(campaign_name)
    template_file = row.get("template_file")
    if not template_file:
        raise HTTPException(status_code=404, detail=f"No template_file set for campaign: {campaign_name}")
    path = _DATA_DIR / template_file
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Template file missing for {campaign_name}")
    return row, json.loads(path.read_text())


class TemplateUpdate(BaseModel):
    step_index: int
    variant_index: int
    subject: str
    body: str


class RewriteRequest(BaseModel):
    step_index: int
    variant_index: int
    subject: str
    body: str
    instruction: Optional[str] = ""


@router.get("/analytics")
def get_campaign_analytics():
    """
    Fetch live analytics for all campaigns from Instantly API v2.
    Returns per-campaign: total leads, emails sent, open rate, reply rate, bounces.
    """
    if not INSTANTLY_API_KEY:
        raise HTTPException(status_code=503, detail="INSTANTLY_API_KEY not configured")

    headers = {"Authorization": f"Bearer {INSTANTLY_API_KEY}"}
    results = []

    for row in _get_routing_rows():
        campaign_id = row["instantly_campaign_id"]
        name = row["default_campaign"]
        label = row.get("instantly_campaign_name") or name
        try:
            resp = httpx.get(
                "https://api.instantly.ai/api/v2/campaigns/analytics",
                headers=headers,
                params={"id": campaign_id, "exclude_total_leads_count": "true"},
                timeout=30,
            )
            resp.raise_for_status()
            raw = resp.json()
            item = raw[0] if isinstance(raw, list) and raw else (raw if isinstance(raw, dict) else {})
            results.append({
                "campaign_name": name,
                "label": label,
                "instantly_id": campaign_id,
                "total_leads": int(item.get("leads_count") or item.get("total_leads_count") or 0),
                "emails_sent": int(item.get("emails_sent_count") or item.get("total_sent_count") or 0),
                "open_rate": round(float(item.get("open_rate") or 0), 1),
                "reply_rate": round(float(item.get("reply_rate") or 0), 1),
                "bounces": int(item.get("bounced_count") or item.get("bounce_count") or 0),
                "unsubscribed": int(item.get("unsubscribed_count") or 0),
            })
        except Exception as e:
            results.append({
                "campaign_name": name,
                "label": label,
                "instantly_id": campaign_id,
                "error": str(e)[:200],
            })

    return results


@router.get("/templates")
def list_campaign_templates():
    """List all campaign scenarios with their step counts."""
    result = []
    for row in _get_routing_rows():
        name = row["default_campaign"]
        template_file = row.get("template_file") or ""
        total_steps = 0
        if template_file:
            path = _DATA_DIR / template_file
            if path.exists():
                data = json.loads(path.read_text())
                seqs = data.get("sequences", [])
                total_steps = len(seqs[0].get("steps", [])) if seqs else 0
        result.append({
            "campaign_name": name,
            "label": row.get("instantly_campaign_name") or name,
            "instantly_id": row.get("instantly_campaign_id"),
            "total_steps": total_steps,
        })
    return result


@router.get("/templates/{campaign_name}")
def get_campaign_template(campaign_name: str, step: int = 0, variant: int = 0):
    """Return metadata + subject/body for a specific step and variant."""
    meta, data = _load_campaign_json(campaign_name)
    steps = data.get("sequences", [{}])[0].get("steps", [])
    if not steps:
        raise HTTPException(status_code=404, detail="No steps found in template")
    if step >= len(steps):
        raise HTTPException(status_code=400, detail=f"Step {step} out of range (0–{len(steps)-1})")
    variants = steps[step].get("variants", [])
    if not variants:
        raise HTTPException(status_code=404, detail=f"No variants in step {step}")
    if variant >= len(variants):
        raise HTTPException(status_code=400, detail=f"Variant {variant} out of range (0–{len(variants)-1})")

    v = variants[variant]
    step_summaries = [
        {
            "index": i,
            "delay": s.get("delay"),
            "variant_count": len(s.get("variants", [])),
            "first_subject": (s.get("variants") or [{}])[0].get("subject", "")[:70],
        }
        for i, s in enumerate(steps)
    ]
    return {
        "campaign_name": campaign_name,
        "label": meta.get("instantly_campaign_name") or campaign_name,
        "instantly_id": meta.get("instantly_campaign_id"),
        "total_steps": len(steps),
        "step_index": step,
        "total_variants": len(variants),
        "variant_index": variant,
        "delay_days": steps[step].get("delay"),
        "subject": v.get("subject", ""),
        "body": v.get("body", ""),
        "step_summaries": step_summaries,
    }


@router.put("/templates/{campaign_name}")
def update_campaign_template(campaign_name: str, payload: TemplateUpdate):
    """
    Save updated subject/body to local JSON and push full sequences to Instantly.
    Returns save status and Instantly push result.
    """
    meta, data = _load_campaign_json(campaign_name)
    seqs = data.get("sequences", [])
    steps = seqs[0].get("steps", []) if seqs else []

    if payload.step_index >= len(steps):
        raise HTTPException(status_code=400, detail=f"Step {payload.step_index} out of range")
    variants = steps[payload.step_index].get("variants", [])
    if payload.variant_index >= len(variants):
        raise HTTPException(status_code=400, detail=f"Variant {payload.variant_index} out of range")

    # Update local JSON
    variants[payload.variant_index]["subject"] = payload.subject
    variants[payload.variant_index]["body"] = payload.body
    path = _DATA_DIR / meta["template_file"]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    # Enqueue a sequences push to n8n — deep-copy seqs and wrap each email step body
    # with the branded HTML template before enqueueing (local JSON stays as raw inner HTML)
    instantly_status = "enqueued"
    instantly_error = None
    try:
        seqs_to_push = copy.deepcopy(seqs)
        for seq in seqs_to_push:
            for step in seq.get("steps", []):
                if step.get("type") == "email":
                    for variant in step.get("variants", []):
                        if "body" in variant:
                            variant["body"] = _wrap_body(variant["body"])
        action_payload = {
            "instantly_campaign_id": meta["instantly_campaign_id"],
            "campaign_name": campaign_name,
            "sequences": seqs_to_push,
        }
        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO action_queue (contact_id, action_type, payload)
                VALUES (NULL, 'push_instantly_sequences', %s::jsonb)
                """,
                (json.dumps(action_payload),),
            )
    except Exception as e:
        instantly_status = "enqueue_failed"
        instantly_error = str(e)[:400]

    return {
        "status": "saved",
        "instantly_status": instantly_status,
        "instantly_error": instantly_error,
        "campaign_name": campaign_name,
        "step_index": payload.step_index,
        "variant_index": payload.variant_index,
    }


@router.post("/templates/{campaign_name}/rewrite")
def rewrite_template_with_claude(campaign_name: str, payload: RewriteRequest):
    """
    Ask Claude to rewrite the given subject/body while preserving brand voice
    and Instantly merge-tag syntax. Returns suggested subject + body.
    """
    import anthropic

    meta = _get_routing_row(campaign_name)
    label = meta.get("instantly_campaign_name") or campaign_name

    body_plain = re.sub(r"<[^>]+>", " ", payload.body).strip()
    body_plain = re.sub(r"\s{2,}", " ", body_plain)

    instruction_line = f"\nSpecial instruction: {payload.instruction}" if payload.instruction else ""

    prompt = f"""You are editing a campaign email for DabbahWala — a home-style Indian food delivery kitchen in Michigan.

Campaign: {label}
Email step: {payload.step_index + 1}

CURRENT SUBJECT: {payload.subject}
CURRENT BODY (plain text):
{body_plain}
{instruction_line}

Rewrite this email to be more compelling while:
- Keeping the DabbahWala brand voice: genuine, warm, non-pushy, never salesy
- Preserving {{{{firstName|there}}}} and {{{{RANDOM | option | option}}}} Instantly merge-tag syntax exactly
- Using <div> tags for paragraphs (Instantly HTML format)
- Matching the purpose of the "{label}" campaign
- Keeping a similar length

Return ONLY a valid JSON object with two keys — "subject" and "body" — no other text:
{{"subject": "...", "body": "<div>...</div>"}}"""

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = resp.content[0].text.strip()
    # Strip markdown code fences if Claude wraps it
    raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("` \n")
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise HTTPException(status_code=500, detail="Could not parse Claude response")
    try:
        result = json.loads(m.group())
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"JSON parse error: {e}")

    return {
        "subject": result.get("subject", ""),
        "body": result.get("body", ""),
    }


# ── One-time setup: tag existing campaigns + create ACTIVE_CUSTOMER ──────────

_DABBAHWALA_TAG = "Dabbahwala"

# DW sending schedule (mirrors promo_standard.json)
_DW_SCHEDULE = {
    "schedules": [
        {
            "name": "Weekday",
            "timing": {"from": "12:00", "to": "22:00"},
            "days": {"1": True, "2": True, "3": True, "4": True, "5": True},
            "timezone": "America/Detroit",
        },
        {
            "name": "Weekend",
            "timing": {"from": "09:00", "to": "23:59"},
            "days": {"0": True, "6": True},
            "timezone": "America/Detroit",
        },
    ],
    "start_date": "2026-02-20",
    "end_date": None,
}


def _get_all_campaigns(headers: dict) -> list[dict]:
    """Fetch all campaigns from Instantly, paginating through all pages."""
    all_campaigns: list[dict] = []
    starting_after: Optional[str] = None
    while True:
        params: dict = {"limit": 100}
        if starting_after:
            params["starting_after"] = starting_after
        try:
            resp = httpx.get(
                "https://api.instantly.ai/api/v2/campaigns",
                headers=headers,
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            page = data.get("items", [])
            all_campaigns.extend(page)
            # Stop if we got fewer than limit (last page) or no next_cursor
            if len(page) < 100 or not data.get("next_cursor"):
                break
            starting_after = data["next_cursor"]
        except Exception as e:
            logger.warning("setup-instantly: error fetching campaigns page: %s", e)
            break
    return all_campaigns


def _get_or_create_tag_id(headers: dict, tag_name: str) -> Optional[str]:
    """
    Find or create a custom tag in Instantly.
    If multiple tags with the same name exist (duplicates), deletes extras and returns the surviving ID.
    Returns the tag ID or None.
    """
    try:
        resp = httpx.get(
            "https://api.instantly.ai/api/v2/custom-tags",
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        tags = data if isinstance(data, list) else data.get("items", data.get("tags", []))

        # Collect ALL matching tags (duplicates may exist)
        matching = [
            tag for tag in tags
            if (tag.get("label") or tag.get("name") or "").lower() == tag_name.lower()
        ]

        if len(matching) > 1:
            # Keep the first one, delete the rest to eliminate duplicates
            keep = matching[0]
            tag_id = str(keep.get("id", "")).strip()
            logger.warning(
                "setup-instantly: found %d duplicate '%s' tags — keeping %s, deleting others",
                len(matching), tag_name, tag_id,
            )
            for dup in matching[1:]:
                dup_id = str(dup.get("id", "")).strip()
                try:
                    httpx.delete(
                        f"https://api.instantly.ai/api/v2/custom-tags/{dup_id}",
                        headers=headers,
                        timeout=10,
                    )
                    logger.info("setup-instantly: deleted duplicate tag %s", dup_id)
                except Exception as de:
                    logger.warning("setup-instantly: could not delete duplicate tag %s: %s", dup_id, de)
            return tag_id

        if len(matching) == 1:
            tag_id = str(matching[0].get("id", "")).strip()
            logger.info("setup-instantly: found existing tag '%s' → %s", tag_name, tag_id)
            return tag_id

        # Not found — create it (Instantly v2 uses "label" field)
        create = httpx.post(
            "https://api.instantly.ai/api/v2/custom-tags",
            headers=headers,
            json={"label": tag_name},
            timeout=10,
        )
        create.raise_for_status()
        tag_id = str(create.json().get("id", "")).strip()
        logger.info("setup-instantly: created tag '%s' → %s", tag_name, tag_id)
        return tag_id or None
    except Exception as e:
        logger.warning("setup-instantly: could not get/create tag '%s': %s", tag_name, e)
        return None


def _tag_instantly_campaigns(headers: dict, campaign_ids: list[str], tag_id: str) -> dict:
    """Assign a tag to multiple Instantly campaigns via toggle-resource. Returns {id: bool}."""
    results = {}
    try:
        resp = httpx.post(
            "https://api.instantly.ai/api/v2/custom-tags/toggle-resource",
            headers=headers,
            json={
                "tag_ids": [tag_id],
                "resource_type": 1,          # 1 = campaign
                "resource_ids": campaign_ids,
                "assign": True,
            },
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("setup-instantly: tagged %d campaigns with tag %s", len(campaign_ids), tag_id)
        for cid in campaign_ids:
            results[cid] = True
    except Exception as e:
        logger.warning("setup-instantly: bulk tagging failed: %s", e)
        for cid in campaign_ids:
            results[cid] = False
    return results


def _create_instantly_campaign(headers: dict, name: str) -> Optional[str]:
    """Create a new campaign in Instantly with schedule. Returns the new campaign ID or None."""
    try:
        resp = httpx.post(
            "https://api.instantly.ai/api/v2/campaigns",
            headers=headers,
            json={
                "name": name,
                "campaign_schedule": _DW_SCHEDULE,
                "sequences": [{"steps": []}],   # required field; sequences pushed via PATCH
            },
            timeout=15,
        )
        resp.raise_for_status()
        created = resp.json()
        campaign_id = str(created.get("id") or created.get("campaign_id") or "").strip()
        if campaign_id:
            logger.info("setup-instantly: created campaign '%s' → %s", name, campaign_id)
            return campaign_id
        logger.error("setup-instantly: create campaign response had no id: %s", created)
    except Exception as e:
        logger.error("setup-instantly: failed to create campaign '%s': %s", name, e)
    return None


def _get_all_account_emails(headers: dict) -> list[str]:
    """Fetch all sending email addresses from Instantly."""
    try:
        resp = httpx.get(
            "https://api.instantly.ai/api/v2/accounts",
            headers=headers,
            params={"limit": 100},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        # API may return list directly or wrap in items/accounts/data key
        items = (
            data
            if isinstance(data, list)
            else data.get("items", data.get("accounts", data.get("data", [])))
        )
        emails = [str(acct.get("email", "")).strip() for acct in items if acct.get("email")]
        logger.info("setup-instantly: found %d sending accounts: %s", len(emails), emails[:5])
        return emails
    except Exception as e:
        logger.warning("setup-instantly: could not fetch email accounts: %s", e)
        return []



@router.post("/setup-instantly")
def setup_instantly_campaigns():
    """
    Idempotent setup endpoint:
      0. Deletes duplicate campaigns in Instantly (keeps canonical IDs from campaign_routing)
      1. Gets or creates the 'Dabbahwala' custom tag
      2. Tags all campaigns with it
      3. Pushes full HTML-wrapped sequences + settings + email_accounts in one PATCH per campaign
      4. Persists any newly created campaign IDs back to campaign_routing
    """
    if not INSTANTLY_API_KEY:
        raise HTTPException(status_code=503, detail="INSTANTLY_API_KEY not configured")

    headers = {
        "Authorization": f"Bearer {INSTANTLY_API_KEY}",
        "Content-Type": "application/json",
    }

    # Load canonical campaign data from campaign_routing (single source of truth)
    routing_rows = _get_routing_rows()

    # Canonical IDs we want to keep
    canonical_ids: set[str] = {r["instantly_campaign_id"] for r in routing_rows if r.get("instantly_campaign_id")}

    # 0. Fetch ALL campaigns (paginated) and dedup: delete copies whose ID is not canonical
    dedup_results: dict = {}
    all_campaigns: list[dict] = _get_all_campaigns(headers)
    logger.info("setup-instantly: fetched %d total campaigns from Instantly", len(all_campaigns))

    by_name: dict[str, list] = {}
    for c in all_campaigns:
        by_name.setdefault(c["name"], []).append(c)
    for name, copies in by_name.items():
        if len(copies) <= 1:
            continue
        to_delete = [c for c in copies if c["id"] not in canonical_ids]
        for c in to_delete:
            try:
                del_resp = httpx.delete(
                    f"https://api.instantly.ai/api/v2/campaigns/{c['id']}",
                    headers=headers,
                    timeout=10,
                )
                if del_resp.status_code < 300:
                    dedup_results.setdefault(name, []).append(f"deleted:{c['id']}")
                    logger.info("setup-instantly: deleted duplicate %s (%s)", c["id"], name)
                else:
                    dedup_results.setdefault(name, []).append(f"delete_failed:{c['id']}:http{del_resp.status_code}")
                    logger.warning("setup-instantly: could not delete %s (%s): %s %s", c["id"], name, del_resp.status_code, del_resp.text[:100])
            except Exception as de:
                dedup_results.setdefault(name, []).append(f"delete_failed:{c['id']}:{str(de)[:80]}")

    # Early exit: all canonical campaigns already exist and no duplicates — nothing to do
    fetched_ids: set[str] = {c["id"] for c in all_campaigns}
    all_canonical_present = bool(canonical_ids) and canonical_ids.issubset(fetched_ids)
    if all_canonical_present and not dedup_results:
        logger.info("setup-instantly: all %d campaigns already present, skipping setup", len(routing_rows))
        return {
            "status": "already_setup",
            "message": "All campaigns already exist in Instantly — no changes made",
            "campaign_count": len(routing_rows),
        }

    # 1. Get or create the Dabbahwala tag (also deduplicates if multiple exist)
    tag_id = _get_or_create_tag_id(headers, _DABBAHWALA_TAG)

    # 2. Resolve ALL campaign IDs: canonical if still alive → find by name → create fresh
    existing_ids_in_instantly: set[str] = {c["id"] for c in all_campaigns}
    existing_by_name: dict[str, str] = {c["name"]: c["id"] for c in all_campaigns}
    resolved_ids: dict[str, str] = {}    # campaign_key → id
    created_campaigns: dict[str, str] = {}  # campaign_key → newly created id
    db_updates: list[tuple] = []         # (new_id, lifecycle_segment, campaign_name) for DB write

    for row in routing_rows:
        campaign_key = row["default_campaign"]
        label = row.get("instantly_campaign_name") or campaign_key
        canonical = (row.get("instantly_campaign_id") or "").strip()

        if canonical and canonical in existing_ids_in_instantly:
            resolved_ids[campaign_key] = canonical
            logger.info("setup-instantly: %s using existing id=%s", campaign_key, canonical)
        elif label in existing_by_name:
            resolved_ids[campaign_key] = existing_by_name[label]
            logger.info("setup-instantly: %s found by name, id=%s", campaign_key, existing_by_name[label])
        else:
            new_id = _create_instantly_campaign(headers, label)
            if new_id:
                resolved_ids[campaign_key] = new_id
                created_campaigns[campaign_key] = new_id
                db_updates.append((new_id, campaign_key))
                logger.info("setup-instantly: %s created fresh id=%s", campaign_key, new_id)
            else:
                logger.error("setup-instantly: could not resolve id for %s", campaign_key)

    # 3. Fetch all sending account emails
    account_emails = _get_all_account_emails(headers)

    configure_results: dict = {}

    # 4. Push HTML-wrapped sequences + settings + email_accounts to EVERY campaign
    seq_results: dict = {}
    for row in routing_rows:
        campaign_key = row["default_campaign"]
        cid = resolved_ids.get(campaign_key, "")
        if not cid:
            seq_results[campaign_key] = "no_id"
            continue
        try:
            _, camp_data = _load_campaign_json(campaign_key)
            seqs_to_push = copy.deepcopy(camp_data.get("sequences", []))
            for seq in seqs_to_push:
                for step in seq.get("steps", []):
                    if step.get("type") == "email":
                        for variant in step.get("variants", []):
                            if "body" in variant:
                                variant["body"] = _wrap_body(variant["body"])
            patch_body: dict = {
                "sequences": seqs_to_push,
                "daily_limit": 100,
                "open_tracking": True,
                "link_tracking": True,
                "stop_on_reply": True,
                "stop_on_auto_reply": True,
                "prioritize_new_leads": True,
                "insert_unsubscribe_header": True,
            }
            if account_emails:
                patch_body["email_accounts"] = account_emails
            patch_resp = httpx.patch(
                f"https://api.instantly.ai/api/v2/campaigns/{cid}",
                headers=headers,
                json=patch_body,
                timeout=20,
            )
            seq_results[campaign_key] = "ok" if patch_resp.status_code < 300 else f"failed:{patch_resp.text[:200]}"
        except Exception as e:
            seq_results[campaign_key] = f"error:{str(e)[:200]}"
    configure_results["sequences_and_settings"] = seq_results

    # 5. Tag every campaign
    all_campaign_ids = [cid for cid in resolved_ids.values() if cid]
    if tag_id and all_campaign_ids:
        _tag_instantly_campaigns(headers, all_campaign_ids, tag_id)
        configure_results["tagged"] = True
    else:
        configure_results["tagged"] = False

    # 6. Persist any newly created campaign IDs back to campaign_routing
    if db_updates:
        try:
            with get_cursor(commit=True) as cur:
                for new_id, campaign_key in db_updates:
                    cur.execute(
                        "UPDATE campaign_routing SET instantly_campaign_id = %s WHERE default_campaign = %s",
                        (new_id, campaign_key),
                    )
            configure_results["db_saved"] = len(db_updates)
            logger.info("setup-instantly: saved %d new campaign IDs to campaign_routing", len(db_updates))
        except Exception as e:
            configure_results["db_saved"] = False
            logger.error("setup-instantly: DB save failed: %s", e)

    return {
        "status": "ok",
        "dedup_results": dedup_results,
        "dabbahwala_tag_id": tag_id or "FAILED",
        "resolved_campaign_ids": resolved_ids,
        "created_campaigns": created_campaigns,
        "configure_results": configure_results,
    }


def _do_bulk_push(contacts: list, activation_results: dict) -> None:
    """Background worker that pushes contacts to Instantly. Runs in a thread."""
    if not INSTANTLY_API_KEY:
        logger.error("bulk-push-now background: INSTANTLY_API_KEY not set")
        return

    headers = {
        "Authorization": f"Bearer {INSTANTLY_API_KEY}",
        "Content-Type": "application/json",
    }

    pushed = 0
    skipped = 0
    failed = 0
    errors: list[str] = []

    for row in contacts:
        email = row["email"]
        campaign_id = row["instantly_campaign_id"]
        try:
            resp = httpx.post(
                "https://api.instantly.ai/api/v2/leads",
                headers=headers,
                json={
                    "email": email,
                    "first_name": row["first_name"] or "",
                    "last_name": row["last_name"] or "",
                    "campaign_id": campaign_id,
                    "skip_if_in_workspace": True,
                },
                timeout=15,
            )
            if resp.status_code in (200, 201):
                pushed += 1
            elif resp.status_code == 422:
                skipped += 1
            else:
                failed += 1
                if len(errors) < 5:
                    errors.append(f"{email}: http {resp.status_code} {resp.text[:120]}")
        except Exception as e:
            failed += 1
            if len(errors) < 5:
                errors.append(f"{email}: {str(e)[:120]}")

    logger.info(
        "bulk-push-now: done — pushed=%d skipped=%d failed=%d errors=%s",
        pushed, skipped, failed, errors,
    )


@router.post("/bulk-push-now")
def bulk_push_now(background_tasks: BackgroundTasks):
    """
    Immediately push all eligible contacts to their Instantly campaigns.

    Eligible contacts:
      - Have a real email (not null, not placeholder)
      - lifecycle_segment is not 'optout' or 'cooling'
      - Has a campaign_routing entry
      - last_order_at is NULL or more than 2 days ago (recently active customers excluded)

    Pushes directly to Instantly API in a background thread (does NOT use action_queue).
    Uses skip_if_in_workspace=true so re-running is safe and idempotent.
    Also activates all 6 DW campaigns if they are inactive.
    Returns immediately — results are logged to Render logs.
    """
    if not INSTANTLY_API_KEY:
        raise HTTPException(status_code=503, detail="INSTANTLY_API_KEY not configured")

    headers = {
        "Authorization": f"Bearer {INSTANTLY_API_KEY}",
        "Content-Type": "application/json",
    }

    # 1. Activate any inactive DW campaigns (fast, just 6 calls)
    routing_rows = _get_routing_rows()
    activation_results: dict = {}
    for row in routing_rows:
        cid = row.get("instantly_campaign_id")
        cname = row.get("default_campaign", "")
        if not cid:
            continue
        try:
            resp = httpx.post(
                f"https://api.instantly.ai/api/v2/campaigns/{cid}/activate",
                headers=headers,
                timeout=10,
            )
            activation_results[cname] = "activated" if resp.status_code < 300 else f"http_{resp.status_code}"
        except Exception as e:
            activation_results[cname] = f"error:{str(e)[:80]}"

    # 2. Load all eligible contacts with their campaign routing
    with get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT
                c.id            AS contact_id,
                c.email,
                c.first_name,
                c.last_name,
                c.phone,
                cr.instantly_campaign_id,
                cr.default_campaign AS campaign_name
            FROM contacts c
            JOIN campaign_routing cr ON cr.lifecycle_segment = c.lifecycle_segment
            WHERE c.email IS NOT NULL
              AND c.email NOT LIKE '%%@app.placeholder.local'
              AND c.lifecycle_segment NOT IN ('optout', 'cooling')
              AND cr.instantly_campaign_id IS NOT NULL
              AND (
                  c.last_order_at IS NULL
                  OR c.last_order_at < NOW() - INTERVAL '2 days'
              )
            ORDER BY c.id
        """)
        contacts = cur.fetchall()

    logger.info("bulk-push-now: queued %d eligible contacts for background push", len(contacts))

    # 3. Run the actual Instantly API calls in a background thread so we don't time out
    background_tasks.add_task(_do_bulk_push, contacts, activation_results)

    return {
        "status": "started",
        "total_eligible": len(contacts),
        "campaign_activations": activation_results,
        "note": "Push is running in background. Check Render logs for pushed/skipped/failed counts.",
    }
