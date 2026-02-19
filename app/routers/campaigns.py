import copy
import json
import re
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import ANTHROPIC_API_KEY, INSTANTLY_API_KEY
from app.db import get_cursor
from app.models import CampaignMove

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
        "label": "Nurture Slow — Cold contacts",
        "json_file": "nurture_slow.json",
        "instantly_id": "90ecd160-22cc-46b1-9fa5-9342fe970837",
    },
    "PROMO_STANDARD": {
        "label": "Promo Standard — Engaged & active",
        "json_file": "promo_standard.json",
        "instantly_id": "30292b3d-9f39-4ef3-b0ba-ea15c634acef",
    },
    "PROMO_AGGRESSIVE": {
        "label": "Promo Aggressive — Lapsed customers",
        "json_file": "promo_aggressive.json",
        "instantly_id": "c9af877a-77ac-491c-a5ee-a8ea7646416b",
    },
    "NEW_CUSTOMER_ONBOARDING": {
        "label": "New Customer Onboarding",
        "json_file": "new_customer_onboarding.json",
        "instantly_id": "c4c42e73-83fd-4d43-b629-db5b11be66ae",
    },
    "REACTIVATION": {
        "label": "Reactivation — Long-dormant",
        "json_file": "reactivation.json",
        "instantly_id": "0c760ec8-3415-48cd-87ff-b58babc17dde",
    },
}


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
