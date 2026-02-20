import copy
import json
import logging
import re
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import ANTHROPIC_API_KEY, INSTANTLY_API_KEY
from app.db import get_cursor
from app.models import CampaignMove

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/pending", response_model=list[CampaignMove])
def get_pending_campaigns():
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT * FROM get_pending_campaign_moves()")
        rows = cur.fetchall()
        return [
            CampaignMove(
                queue_id=r["queue_id"],
                contact_email=r["contact_email"],
                contact_phone=r["contact_phone"],
                from_campaign=r["from_campaign"],
                to_campaign=r["to_campaign"],
            )
            for r in rows
        ]


@router.post("/{queue_id}/executed")
def mark_executed(queue_id: int):
    with get_cursor() as cur:
        cur.execute("SELECT mark_campaign_executed(%s)", (queue_id,))
        return {"status": "ok"}


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

_CAMPAIGN_META: dict[str, dict] = {
    "NURTURE_SLOW": {
        "label": "DW-NurtureSlow-ColdContacts",
        "json_file": "nurture_slow.json",
        "instantly_id": "90ecd160-22cc-46b1-9fa5-9342fe970837",
    },
    "PROMO_STANDARD": {
        "label": "DW-PromoStandard-ActiveEngaged",
        "json_file": "promo_standard.json",
        "instantly_id": "30292b3d-9f39-4ef3-b0ba-ea15c634acef",
    },
    "ACTIVE_CUSTOMER": {
        "label": "DW-ActiveCustomer",
        "json_file": "promo_standard.json",  # reuse promo_standard sequences until dedicated copy is made
        "instantly_id": "",  # populated after setup-instantly creates this campaign
    },
    "PROMO_AGGRESSIVE": {
        "label": "DW-PromoAggressive-LapsedCustomers",
        "json_file": "promo_aggressive.json",
        "instantly_id": "c9af877a-77ac-491c-a5ee-a8ea7646416b",
    },
    "NEW_CUSTOMER_ONBOARDING": {
        "label": "DW-NewCustomerOnboarding",
        "json_file": "new_customer_onboarding.json",
        "instantly_id": "c4c42e73-83fd-4d43-b629-db5b11be66ae",
    },
    "REACTIVATION": {
        "label": "DW-Reactivation-LongDormant",
        "json_file": "reactivation.json",
        "instantly_id": "0c760ec8-3415-48cd-87ff-b58babc17dde",
    },
}


def push_lead_to_instantly(
    email: str,
    first_name: str,
    last_name: str,
    phone: str,
    campaign_name: str,
) -> bool:
    """
    Add a lead directly to an Instantly campaign. Returns True on success.
    Silently returns False if the campaign is unknown or API key is missing.
    """
    meta = _CAMPAIGN_META.get(campaign_name)
    if not meta or not INSTANTLY_API_KEY:
        return False
    campaign_id = meta["instantly_id"]
    lead = {"email": email, "first_name": first_name, "last_name": last_name}
    if phone:
        lead["phone"] = phone
    response = httpx.post(
        f"https://api.instantly.ai/api/v2/campaigns/{campaign_id}/leads",
        headers={"Authorization": f"Bearer {INSTANTLY_API_KEY}", "Content-Type": "application/json"},
        json={"leads": [lead]},
        timeout=10,
    )
    response.raise_for_status()
    return True


def _load_campaign_json(campaign_name: str) -> tuple[dict, dict]:
    """Return (meta, parsed_json) or raise HTTPException."""
    meta = _CAMPAIGN_META.get(campaign_name)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Unknown campaign: {campaign_name}")
    path = _DATA_DIR / meta["json_file"]
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Template file missing for {campaign_name}")
    return meta, json.loads(path.read_text())


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
    Fetch live analytics for all 5 campaigns from Instantly API v2.
    Returns per-campaign: total leads, emails sent, open rate, reply rate, bounces.
    """
    if not INSTANTLY_API_KEY:
        raise HTTPException(status_code=503, detail="INSTANTLY_API_KEY not configured")

    headers = {"Authorization": f"Bearer {INSTANTLY_API_KEY}"}
    results = []

    for name, meta in _CAMPAIGN_META.items():
        campaign_id = meta["instantly_id"]
        try:
            resp = httpx.get(
                "https://api.instantly.ai/api/v2/analytics/campaigns/summary",
                headers=headers,
                params={"id": campaign_id},
                timeout=10,
            )
            resp.raise_for_status()
            raw = resp.json()
            # API returns a list; grab the first (matching) item
            item = raw[0] if isinstance(raw, list) and raw else (raw if isinstance(raw, dict) else {})
            results.append({
                "campaign_name": name,
                "label": meta["label"],
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
                "label": meta["label"],
                "instantly_id": campaign_id,
                "error": str(e)[:200],
            })

    return results


@router.get("/templates")
def list_campaign_templates():
    """List all campaign scenarios with their step counts."""
    result = []
    for name, meta in _CAMPAIGN_META.items():
        path = _DATA_DIR / meta["json_file"]
        total_steps = 0
        if path.exists():
            data = json.loads(path.read_text())
            seqs = data.get("sequences", [])
            total_steps = len(seqs[0].get("steps", [])) if seqs else 0
        result.append({
            "campaign_name": name,
            "label": meta["label"],
            "instantly_id": meta["instantly_id"],
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
        "label": meta["label"],
        "instantly_id": meta["instantly_id"],
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
    path = _DATA_DIR / meta["json_file"]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    # Push to Instantly — deep-copy seqs and wrap each email step body
    # with the branded HTML template before sending (local JSON stays as raw inner HTML)
    instantly_status = "skipped"
    instantly_error = None
    if INSTANTLY_API_KEY:
        try:
            seqs_to_push = copy.deepcopy(seqs)
            for seq in seqs_to_push:
                for step in seq.get("steps", []):
                    if step.get("type") == "email":
                        for variant in step.get("variants", []):
                            if "body" in variant:
                                variant["body"] = _wrap_body(variant["body"])
            resp = httpx.patch(
                f"https://api.instantly.ai/api/v2/campaigns/{meta['instantly_id']}",
                headers={
                    "Authorization": f"Bearer {INSTANTLY_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={"sequences": seqs_to_push},
                timeout=15,
            )
            if resp.status_code < 300:
                instantly_status = "pushed"
            else:
                instantly_status = "failed"
                instantly_error = resp.text[:400]
        except Exception as e:
            instantly_status = "failed"
            instantly_error = str(e)[:400]
    else:
        instantly_status = "no_key"
        instantly_error = "INSTANTLY_API_KEY not configured — saved locally only"

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

    meta = _CAMPAIGN_META.get(campaign_name)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Unknown campaign: {campaign_name}")

    body_plain = re.sub(r"<[^>]+>", " ", payload.body).strip()
    body_plain = re.sub(r"\s{2,}", " ", body_plain)

    instruction_line = f"\nSpecial instruction: {payload.instruction}" if payload.instruction else ""

    prompt = f"""You are editing a campaign email for DabbahWala — a home-style Indian food delivery kitchen in Michigan.

Campaign: {meta['label']}
Email step: {payload.step_index + 1}

CURRENT SUBJECT: {payload.subject}
CURRENT BODY (plain text):
{body_plain}
{instruction_line}

Rewrite this email to be more compelling while:
- Keeping the DabbahWala brand voice: genuine, warm, non-pushy, never salesy
- Preserving {{{{firstName|there}}}} and {{{{RANDOM | option | option}}}} Instantly merge-tag syntax exactly
- Using <div> tags for paragraphs (Instantly HTML format)
- Matching the purpose of the "{meta['label']}" campaign
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

# The 5 campaigns that already exist in Instantly (confirmed IDs)
_EXISTING_CAMPAIGN_IDS: list[str] = [
    "90ecd160-22cc-46b1-9fa5-9342fe970837",  # NURTURE_SLOW
    "30292b3d-9f39-4ef3-b0ba-ea15c634acef",  # PROMO_STANDARD
    "c9af877a-77ac-491c-a5ee-a8ea7646416b",  # PROMO_AGGRESSIVE
    "c4c42e73-83fd-4d43-b629-db5b11be66ae",  # NEW_CUSTOMER_ONBOARDING
    "0c760ec8-3415-48cd-87ff-b58babc17dde",  # REACTIVATION
]

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


def _get_or_create_tag_id(headers: dict, tag_name: str) -> Optional[str]:
    """Find or create a custom tag in Instantly. Returns the tag ID or None."""
    try:
        resp = httpx.get(
            "https://api.instantly.ai/api/v2/custom-tags",
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        tags = data if isinstance(data, list) else data.get("items", data.get("tags", []))
        for tag in tags:
            if tag.get("name", "").lower() == tag_name.lower():
                tag_id = str(tag.get("id", "")).strip()
                logger.info("setup-instantly: found existing tag '%s' → %s", tag_name, tag_id)
                return tag_id
        # Not found — create it
        create = httpx.post(
            "https://api.instantly.ai/api/v2/custom-tags",
            headers=headers,
            json={"name": tag_name},
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


def _add_accounts_to_campaign(headers: dict, campaign_id: str, emails: list[str]) -> dict:
    """
    Attach sending accounts to a campaign via account-campaign-mappings.
    Returns {email: "ok"|"failed"}.
    """
    results = {}
    for email in emails:
        try:
            resp = httpx.post(
                f"https://api.instantly.ai/api/v2/account-campaign-mappings/{email}",
                headers=headers,
                json={"campaign_id": campaign_id},
                timeout=10,
            )
            resp.raise_for_status()
            results[email] = "ok"
        except Exception as e:
            results[email] = f"failed:{str(e)[:80]}"
            logger.warning("setup-instantly: add account %s failed: %s", email, e)
    logger.info(
        "setup-instantly: added accounts — ok=%d failed=%d",
        sum(1 for v in results.values() if v == "ok"),
        sum(1 for v in results.values() if v != "ok"),
    )
    return results


@router.post("/setup-instantly")
def setup_instantly_campaigns():
    """
    One-time setup endpoint:
      1. Gets or creates the 'Dabbahwala' custom tag in Instantly
      2. Tags the 5 existing DabbahWala campaigns with it
      3. Creates the new DW-ActiveCustomer campaign (with schedule + blank sequences)
      4. PATCHes it with full sequences, daily_limit=100, open+click tracking
      5. Attaches all sending accounts
      6. Tags the new campaign with 'Dabbahwala'
      7. Saves the new campaign ID into campaign_routing DB row

    After this runs, hardcode the returned active_customer_campaign_id into
    _CAMPAIGN_META['ACTIVE_CUSTOMER']['instantly_id'] in campaigns.py and redeploy.
    """
    if not INSTANTLY_API_KEY:
        raise HTTPException(status_code=503, detail="INSTANTLY_API_KEY not configured")

    headers = {
        "Authorization": f"Bearer {INSTANTLY_API_KEY}",
        "Content-Type": "application/json",
    }

    # 1. Get or create the Dabbahwala tag
    tag_id = _get_or_create_tag_id(headers, _DABBAHWALA_TAG)

    # 2. Tag the 5 existing campaigns (bulk call)
    tag_results: dict = {}
    if tag_id:
        tag_results = _tag_instantly_campaigns(headers, _EXISTING_CAMPAIGN_IDS, tag_id)
    else:
        tag_results = {cid: False for cid in _EXISTING_CAMPAIGN_IDS}

    # 3. Fetch all sending account emails
    account_emails = _get_all_account_emails(headers)

    # 4. Create DW-ActiveCustomer campaign
    active_customer_id = _create_instantly_campaign(headers, "DW-ActiveCustomer")
    configure_results: dict = {}

    if active_customer_id:
        # 4a. Push sequences + settings
        _, promo_data = _load_campaign_json("ACTIVE_CUSTOMER")
        seqs_to_push = copy.deepcopy(promo_data.get("sequences", []))
        for seq in seqs_to_push:
            for step in seq.get("steps", []):
                if step.get("type") == "email":
                    for variant in step.get("variants", []):
                        if "body" in variant:
                            variant["body"] = _wrap_body(variant["body"])
        try:
            patch_resp = httpx.patch(
                f"https://api.instantly.ai/api/v2/campaigns/{active_customer_id}",
                headers=headers,
                json={
                    "sequences": seqs_to_push,
                    "daily_limit": 100,
                    "open_tracking": True,
                    "link_tracking": True,
                    "stop_on_reply": True,
                    "stop_on_auto_reply": True,
                    "prioritize_new_leads": True,
                    "insert_unsubscribe_header": True,
                },
                timeout=20,
            )
            configure_results["sequences_and_settings"] = (
                "ok" if patch_resp.status_code < 300 else f"failed:{patch_resp.text[:300]}"
            )
        except Exception as e:
            configure_results["sequences_and_settings"] = f"error:{e}"

        # 4b. Attach sending accounts
        configure_results["accounts"] = _add_accounts_to_campaign(
            headers, active_customer_id, account_emails
        )

        # 4c. Tag the new campaign
        if tag_id:
            _tag_instantly_campaigns(headers, [active_customer_id], tag_id)
            configure_results["tagged"] = True
        else:
            configure_results["tagged"] = False

        # 4d. Persist new campaign ID to campaign_routing
        try:
            with get_cursor(commit=True) as cur:
                cur.execute(
                    """UPDATE campaign_routing
                          SET instantly_campaign_id   = %s,
                              instantly_campaign_name = 'DW-ActiveCustomer'
                        WHERE lifecycle_segment = 'active_customer'""",
                    (active_customer_id,),
                )
            configure_results["db_saved"] = True
            logger.info("setup-instantly: saved ACTIVE_CUSTOMER id=%s to DB", active_customer_id)
        except Exception as e:
            configure_results["db_saved"] = False
            logger.error("setup-instantly: DB save failed: %s", e)

    return {
        "status": "ok",
        "dabbahwala_tag_id": tag_id or "FAILED",
        "tagged_existing": tag_results,
        "active_customer_campaign_id": active_customer_id or "FAILED",
        "configure_results": configure_results,
        "next_step": (
            f"Hardcode _CAMPAIGN_META['ACTIVE_CUSTOMER']['instantly_id'] = '{active_customer_id or 'FAILED'}' "
            "in campaigns.py, then redeploy."
        ),
    }
