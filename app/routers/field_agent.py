"""
DabbahWala — Field Agent Intelligence
Automatically analyses call transcripts so the business owner gets an honest,
AI-generated verdict on every field agent call — no self-reporting required.

Endpoints
---------
POST /analyze-call/{call_id}    — Claude reads the Telnyx call transcript and
                                   produces a scored review + recommended next action.
                                   A follow-up opportunity is auto-created when needed.
POST /daily-brief               — Generates today's proactive call list (top contacts
                                   in reorder window) and creates field_sales_call
                                   opportunities so the list isn't empty.
GET  /reviews                   — Recent AI-generated call reviews.
GET  /scorecard                 — Per-agent performance stats over the last N days.
GET  /pending-calls             — field_sales_call opportunities not yet acted on.
"""

import json
import logging
import os
import time
from typing import Optional

import anthropic
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_cursor

logger = logging.getLogger(__name__)
router = APIRouter()

MODEL = "claude-sonnet-4-5-20250929"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _claude() -> anthropic.Anthropic:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")
    return anthropic.Anthropic(api_key=key)


def _fetch_call(call_id: int) -> dict:
    """Fetch a telnyx_calls row with contact info joined."""
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT tc.id, tc.contact_id, tc.direction, tc.from_number, tc.to_number,
                   tc.duration_sec, tc.transcript, tc.summary, tc.agent_name,
                   tc.started_at, tc.ended_at,
                   c.first_name, c.last_name, c.phone, c.email,
                   c.lifecycle_segment, c.total_orders, c.last_order_at
            FROM telnyx_calls tc
            JOIN contacts c ON c.id = tc.contact_id
            WHERE tc.id = %s
            """,
            (call_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Call {call_id} not found")
        return dict(row)


def _fetch_customer_order_history(contact_id: int) -> list:
    """Last 10 orders for this customer — gives Claude context on their value."""
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT o.order_date, o.total_amount, o.source, o.order_type,
                   array_agg(oi.item_name ORDER BY oi.quantity DESC) AS items
            FROM orders o
            LEFT JOIN order_items oi ON oi.order_id = o.id
            WHERE o.contact_id = %s
            GROUP BY o.id
            ORDER BY o.order_date DESC
            LIMIT 10
            """,
            (contact_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def _fetch_open_opportunity(contact_id: int) -> Optional[dict]:
    """Find the most recent open field_sales_call opportunity for this contact."""
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT id, reason, priority, suggested_message, created_at
            FROM opportunities
            WHERE contact_id = %s
              AND action = 'field_sales_call'
              AND status IN ('pending', 'dispatched')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (contact_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def _create_opportunity(contact_id: int, action: str, priority: str,
                        reason: str, suggested_message: str = "",
                        confidence: float = 0.85) -> int:
    """Create a new opportunity via the stored procedure. Returns opportunity id."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT create_opportunity(%s, %s::opportunity_action, %s, %s, %s, %s)",
            (contact_id, action, priority, reason, suggested_message, confidence),
        )
        return cur.fetchone()["create_opportunity"]


# ---------------------------------------------------------------------------
# Claude — call transcript analyser
# ---------------------------------------------------------------------------

_ANALYZE_TOOL = {
    "name": "call_review",
    "description": "Structured review of a field agent call transcript.",
    "input_schema": {
        "type": "object",
        "properties": {
            "pitch_quality_score": {
                "type": "number",
                "description": "0–10 score. 10 = perfect pitch, clear value prop, strong close. "
                               "0 = no attempt to sell, incoherent, hostile."
            },
            "asked_for_order": {
                "type": "boolean",
                "description": "True only if the agent explicitly asked the customer to place an order."
            },
            "customer_signal": {
                "type": "string",
                "enum": ["interested", "warm", "neutral", "objecting", "not_interested"],
                "description": "Overall customer disposition at end of call."
            },
            "objections_raised": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of specific objections raised by the customer, e.g. 'price too high', "
                               "'busy this week', 'already have a meal service'."
            },
            "honest_assessment": {
                "type": "string",
                "description": "2–4 sentence unfiltered verdict. Be blunt. If the agent was vague, "
                               "never asked for the order, or missed obvious buying signals — say so. "
                               "End with one concrete improvement tip."
            },
            "recommended_next_action": {
                "type": "string",
                "enum": [
                    "close_won",          # Customer agreed — mark as ordered
                    "call_again_today",   # Customer was warm, agent fumbled — call back same day
                    "send_sms_now",       # Customer prefers text — send personalised SMS
                    "hand_to_automation", # Customer needs nurturing, not ready for call
                    "close_lost"          # Clear not interested, stop pursuing
                ]
            },
            "next_action_timing": {
                "type": "string",
                "enum": ["immediate", "today", "tomorrow", "3days", "none"]
            },
            "next_action_reason": {
                "type": "string",
                "description": "One sentence explaining why you chose that next action."
            }
        },
        "required": [
            "pitch_quality_score", "asked_for_order", "customer_signal",
            "objections_raised", "honest_assessment",
            "recommended_next_action", "next_action_timing", "next_action_reason"
        ]
    }
}


def _run_call_analysis(client: anthropic.Anthropic, call: dict, orders: list) -> dict:
    """Send transcript to Claude and return structured review."""
    name = f"{call.get('first_name', '')} {call.get('last_name', '')}".strip()
    system = (
        "You are the Call Quality Analyst for DabbahWala, a fresh home-style Indian food "
        "delivery service in Atlanta, GA. Your job is to review field agent call transcripts "
        "with complete honesty — the business owner reads your reports, not the agent. "
        "The goal of every call is to get a daily order or a subscription. "
        "Judge the agent's performance strictly on whether they moved the customer toward ordering. "
        "Do not soften criticism. If an agent was friendly but never asked for the order, that is a failure."
    )
    user_parts = [
        f"Customer: {name} | Segment: {call.get('lifecycle_segment')} | "
        f"Total orders: {call.get('total_orders')} | "
        f"Last order: {call.get('last_order_at', 'unknown')}",
        "",
        f"Field agent: {call.get('agent_name', 'unknown')}",
        f"Call duration: {call.get('duration_sec', 0)} seconds",
        "",
    ]
    if orders:
        user_parts.append("Customer order history (most recent first):")
        for o in orders[:5]:
            items = ", ".join((o.get("items") or [])[:3])
            user_parts.append(
                f"  {o.get('order_date')} — ${o.get('total_amount')} — {items}"
            )
        user_parts.append("")

    transcript = call.get("transcript") or ""
    if transcript.strip():
        user_parts.append("CALL TRANSCRIPT:")
        user_parts.append(transcript)
    else:
        user_parts.append("CALL TRANSCRIPT: [No transcript available — call may have been too short]")

    user = "\n".join(user_parts)

    t0 = time.time()
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=system,
        tools=[_ANALYZE_TOOL],
        tool_choice={"type": "tool", "name": "call_review"},
        messages=[{"role": "user", "content": user}],
    )
    elapsed = time.time() - t0
    logger.info("Call analysis complete call_id=%s elapsed=%.2fs", call["id"], elapsed)

    for block in response.content:
        if block.type == "tool_use" and block.name == "call_review":
            return block.input

    raise ValueError("Claude did not return a call_review tool call")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/analyze-call/{call_id}")
def analyze_call(call_id: int):
    """
    Claude reads the Telnyx call transcript and produces a scored, honest review.
    No field agent input required — the transcript is the only source of truth.

    Auto-creates a follow-up opportunity based on the recommended next action.
    """
    logger.info("POST /field-agent/analyze-call/%s", call_id)
    call = _fetch_call(call_id)
    contact_id = call["contact_id"]
    orders = _fetch_customer_order_history(contact_id)
    open_opp = _fetch_open_opportunity(contact_id)

    client = _claude()
    review = _run_call_analysis(client, call, orders)

    # Map next action → opportunity action
    action_map = {
        "call_again_today": ("field_sales_call", "hot"),
        "send_sms_now":     ("send_sms",         "hot"),
        "hand_to_automation": ("send_sms",        "warm"),
        "close_won":        None,
        "close_lost":       None,
    }
    next_opp_id = None
    next_action = review.get("recommended_next_action", "")
    opp_spec = action_map.get(next_action)

    if opp_spec:
        opp_action, opp_priority = opp_spec
        name = f"{call.get('first_name', '')} {call.get('last_name', '')}".strip()
        reason = (
            f"Post-call follow-up ({next_action}): {review.get('next_action_reason', '')} "
            f"| Agent: {call.get('agent_name', 'unknown')} | "
            f"Customer signal: {review.get('customer_signal')}"
        )
        suggested = ""
        if next_action == "send_sms_now" and review.get("objections_raised"):
            objections = ", ".join(review["objections_raised"][:2])
            suggested = (
                f"Hi {call.get('first_name', name)}, thanks for chatting earlier! "
                f"Wanted to follow up and answer any questions. "
                f"We'd love to get your next meal sorted — reply here and I'll help."
            )
        next_opp_id = _create_opportunity(
            contact_id, opp_action, opp_priority, reason, suggested, 0.90
        )
        logger.info(
            "Follow-up opportunity created id=%s action=%s for contact_id=%s",
            next_opp_id, opp_action, contact_id
        )

    # Persist the review
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO field_agent_reviews
                (contact_id, call_id, opportunity_id, agent_name,
                 pitch_quality_score, asked_for_order, customer_signal,
                 objections_raised, honest_assessment,
                 recommended_next_action, next_action_timing, next_action_reason,
                 follow_up_opportunity_id, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                contact_id,
                call_id,
                open_opp["id"] if open_opp else None,
                call.get("agent_name"),
                review.get("pitch_quality_score"),
                review.get("asked_for_order"),
                review.get("customer_signal"),
                review.get("objections_raised", []),
                review.get("honest_assessment"),
                next_action,
                review.get("next_action_timing"),
                review.get("next_action_reason"),
                next_opp_id,
                "actioned" if next_opp_id else "closed",
            ),
        )
        review_id = cur.fetchone()["id"]

    logger.info(
        "Field agent review saved id=%s call_id=%s agent=%s pitch=%.1f asked_order=%s signal=%s action=%s",
        review_id, call_id, call.get("agent_name"),
        review.get("pitch_quality_score", 0),
        review.get("asked_for_order"),
        review.get("customer_signal"),
        next_action,
    )

    return {
        "review_id": review_id,
        "call_id": call_id,
        "contact_id": contact_id,
        "agent_name": call.get("agent_name"),
        "review": review,
        "follow_up_opportunity_id": next_opp_id,
    }


@router.post("/daily-brief")
def generate_daily_brief():
    """
    Generates today's proactive call list from DB (top contacts in reorder window)
    and creates a field_sales_call opportunity for each — so the list is never empty.

    Called by the daily_field_brief n8n cron at 7:30 AM.
    """
    logger.info("POST /field-agent/daily-brief")
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT get_top_contacts_to_call(15)")
        raw = cur.fetchone()["get_top_contacts_to_call"]
        contacts = json.loads(raw) if isinstance(raw, str) else (raw or [])

    if not contacts:
        logger.info("daily-brief: no eligible contacts found")
        return {"created": 0, "brief": []}

    created = 0
    brief = []
    for c in contacts:
        contact_id = c["contact_id"]
        days = c.get("days_since_last_order", 0)
        orders = c.get("total_orders", 0)
        spend = c.get("lifetime_spend", 0)
        name = f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()

        # Determine urgency
        if days <= 30:
            priority = "hot"
            urgency_note = "in prime reorder window"
        elif days <= 45:
            priority = "warm"
            urgency_note = "approaching lapse risk"
        else:
            priority = "cold"
            urgency_note = "nearing 7-week mark — call now"

        reason = (
            f"Daily field brief: {orders} lifetime orders, "
            f"${spend} lifetime spend, {days} days since last order — {urgency_note}."
        )
        suggested = (
            f"Hi {c.get('first_name', name)}, this is [your name] calling personally from DabbahWala. "
            f"We noticed it's been about {days // 7} weeks since your last order and genuinely wanted to check in. "
            f"With {orders} orders you're one of our most loyal customers — "
            f"can I help you place an order for this week?"
        )

        try:
            opp_id = _create_opportunity(
                contact_id, "field_sales_call", priority, reason, suggested, 0.88
            )
            created += 1
            brief.append({
                **c,
                "opportunity_id": opp_id,
                "priority": priority,
                "urgency_note": urgency_note,
                "suggested_script": suggested,
            })
            logger.info(
                "Daily brief opportunity created id=%s contact_id=%s name=%s priority=%s",
                opp_id, contact_id, name, priority
            )
        except Exception as e:
            logger.warning(
                "Could not create opportunity for contact_id=%s: %s", contact_id, e
            )

    logger.info("daily-brief: created %d opportunities from %d candidates", created, len(contacts))
    return {"created": created, "brief": brief}


@router.get("/reviews")
def get_reviews(days: int = 7):
    """Return recent AI-generated call reviews with agent name and verdict."""
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT get_recent_field_agent_reviews(%s)", (days,))
        raw = cur.fetchone()["get_recent_field_agent_reviews"]
        reviews = json.loads(raw) if isinstance(raw, str) else (raw or [])
    return {"count": len(reviews), "reviews": reviews}


@router.get("/scorecard")
def get_scorecard(days: int = 30):
    """
    Per-agent performance scorecard for the last N days.
    Shows: calls reviewed, pitch quality score, asked-for-order rate, conversion rate.
    Agents who never ask for the order or consistently miss buying signals are flagged.
    """
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT get_field_agent_scorecard(%s)", (days,))
        raw = cur.fetchone()["get_field_agent_scorecard"]
        scorecard = json.loads(raw) if isinstance(raw, str) else (raw or [])

    # Flag underperformers
    for agent in scorecard:
        issues = []
        if agent.get("avg_pitch_quality", 10) < 5:
            issues.append("low pitch quality score")
        total = agent.get("calls_reviewed", 1) or 1
        ask_rate = (agent.get("asked_for_order_count", 0) / total) * 100
        if ask_rate < 50:
            issues.append(f"only asked for order in {ask_rate:.0f}% of calls")
        won = agent.get("calls_closed_won", 0)
        conversion_rate = (won / total) * 100
        if total >= 3 and conversion_rate < 20:
            issues.append(f"low conversion rate ({conversion_rate:.0f}%)")
        agent["performance_flags"] = issues
        agent["needs_coaching"] = len(issues) > 0

    return {"days": days, "agents": scorecard}


@router.get("/pending-calls")
def get_pending_calls():
    """All open field_sales_call opportunities — the active call queue."""
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT o.id AS opportunity_id, o.priority, o.reason, o.suggested_message,
                   o.created_at, o.status,
                   c.first_name, c.last_name, c.phone, c.email,
                   c.lifecycle_segment, c.total_orders, c.last_order_at,
                   (CURRENT_DATE - c.last_order_at::date) AS days_since_last_order
            FROM opportunities o
            JOIN contacts c ON c.id = o.contact_id
            WHERE o.action = 'field_sales_call'
              AND o.status IN ('pending', 'dispatched')
            ORDER BY
                CASE o.priority WHEN 'hot' THEN 1 WHEN 'warm' THEN 2 ELSE 3 END,
                c.total_orders DESC
            LIMIT 50
            """
        )
        rows = [dict(r) for r in cur.fetchall()]
    return {"count": len(rows), "calls": rows}
