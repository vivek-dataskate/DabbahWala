"""
DabbahWala — Claude Agent Stack
Inference → Decision → Orchestrator → Action Queue
Plus daily Activity and Outcome reporting agents.
"""

import csv
import io
import json
import os
import smtplib
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from typing import Optional

import anthropic
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_cursor

router = APIRouter()

MODEL = "claude-sonnet-4-5-20250929"

# ---------------------------------------------------------------------------
# Anthropic client
# ---------------------------------------------------------------------------

def _claude() -> anthropic.Anthropic:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")
    return anthropic.Anthropic(api_key=key)


def _tool_call(client: anthropic.Anthropic, system: str, user: str, tool: dict) -> dict:
    """Call Claude with a single forced tool and return its input dict."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=system,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{"role": "user", "content": user}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return block.input
    return {}


# ---------------------------------------------------------------------------
# DB helpers — gather context for each agent type
# ---------------------------------------------------------------------------

def _fetch_contact(contact_id: int) -> dict:
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT c.id, c.first_name, c.last_name, c.email, c.phone,
                   c.lifecycle_segment, c.total_orders, c.sms_level,
                   c.last_order_date, c.created_at,
                   er.opens_7d, er.opens_30d, er.clicks_7d, er.clicks_30d,
                   er.sms_sent_30d, er.orders_90d
            FROM contacts c
            LEFT JOIN engagement_rollups er ON er.contact_id = c.id
            WHERE c.id = %s
            """,
            (contact_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Contact {contact_id} not found")
        return dict(row)


def _fetch_recent_events(contact_id: int, days: int = 30) -> list:
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT event_type, occurred_at, metadata
            FROM events
            WHERE contact_id = %s AND occurred_at >= NOW() - (%s || ' days')::INTERVAL
            ORDER BY occurred_at DESC
            LIMIT 50
            """,
            (contact_id, days),
        )
        return [dict(r) for r in cur.fetchall()]


def _fetch_communications(contact_id: int, days: int = 30) -> list:
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT direction, body, transcript, summary,
                   started_at, ended_at, duration_sec, is_delivery_staff,
                   created_at
            FROM telnyx_tracking
            WHERE contact_id = %s AND created_at >= NOW() - (%s || ' days')::INTERVAL
            ORDER BY created_at DESC
            LIMIT 20
            """,
            (contact_id, days),
        )
        return [dict(r) for r in cur.fetchall()]


def _fetch_latest_inference(contact_id: int) -> Optional[dict]:
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT * FROM inference_results
            WHERE contact_id = %s
            ORDER BY run_at DESC LIMIT 1
            """,
            (contact_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def _fetch_latest_decision(contact_id: int) -> Optional[dict]:
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT * FROM decision_recommendations
            WHERE contact_id = %s
            ORDER BY run_at DESC LIMIT 1
            """,
            (contact_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def _fetch_active_goal(contact_id: int) -> Optional[dict]:
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT * FROM customer_goals
            WHERE contact_id = %s AND status = 'active'
            ORDER BY created_at DESC LIMIT 1
            """,
            (contact_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def _fetch_recent_actions(contact_id: int, hours: int = 48) -> list:
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT action_type, payload, status, created_at, executed_at
            FROM action_queue
            WHERE contact_id = %s AND created_at >= NOW() - (%s || ' hours')::INTERVAL
            ORDER BY created_at DESC
            """,
            (contact_id, hours),
        )
        return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# LAYER 1 — Inference Agents
# ---------------------------------------------------------------------------

def _run_sentiment_agent(client: anthropic.Anthropic, contact: dict, comms: list) -> dict:
    system = (
        "You are the Sentiment Inference Agent for DabbahWala, a food delivery business in the UAE. "
        "Your only job is to assess customer sentiment from their communication history. "
        "Be concise. Focus on tone, word choice, and responsiveness patterns."
    )
    user = (
        f"Customer: {contact.get('first_name', '')} {contact.get('last_name', '')} | "
        f"Lifecycle: {contact.get('lifecycle_segment', 'unknown')} | "
        f"Total orders: {contact.get('total_orders', 0)} | "
        f"Last order: {contact.get('last_order_date', 'never')}\n\n"
        f"Recent communications:\n{json.dumps(comms, indent=2, default=str)}"
    )
    tool = {
        "name": "submit_sentiment",
        "description": "Submit customer sentiment assessment",
        "input_schema": {
            "type": "object",
            "properties": {
                "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]},
                "confidence": {"type": "number", "description": "0.0 to 1.0"},
                "summary": {"type": "string", "description": "1-2 sentence explanation"},
            },
            "required": ["sentiment", "confidence", "summary"],
        },
    }
    result = _tool_call(client, system, user, tool)
    return result or {"sentiment": "neutral", "confidence": 0.4, "summary": "Insufficient data"}


def _run_intent_agent(client: anthropic.Anthropic, contact: dict, events: list, comms: list) -> dict:
    system = (
        "You are the Intent Inference Agent for DabbahWala food delivery. "
        "Analyse email engagement events and communication history to classify purchase intent. "
        "Look for signals: multiple opens without ordering, questions about menu/prices, "
        "re-engagement after absence, reorder language in call transcripts.\n"
        "Delivery events are strong buying signals:\n"
        "  - 'delivered' or 'order_placed' in recent events = customer is ACTIVELY ordering; classify as ready_to_order\n"
        "  - 'delivery_failed' = customer had a bad experience; weight this heavily against purchase readiness\n"
        "  - 'out_for_delivery' / 'driver_assigned' = order in progress; do NOT classify as ready_to_order yet\n"
        "  - No delivery events + long gap since last order = likely lapsed; use email engagement to calibrate."
    )
    user = (
        f"Customer: {contact.get('first_name', '')} {contact.get('last_name', '')} | "
        f"Lifecycle: {contact.get('lifecycle_segment', 'unknown')} | "
        f"Orders: {contact.get('total_orders', 0)} | "
        f"Opens (7d/30d): {contact.get('opens_7d', 0)}/{contact.get('opens_30d', 0)} | "
        f"Clicks (7d/30d): {contact.get('clicks_7d', 0)}/{contact.get('clicks_30d', 0)}\n\n"
        f"Recent events:\n{json.dumps(events[:25], indent=2, default=str)}\n\n"
        f"Recent communications:\n{json.dumps(comms[:10], indent=2, default=str)}"
    )
    tool = {
        "name": "submit_intent",
        "description": "Submit customer intent classification",
        "input_schema": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": ["ready_to_order", "needs_info", "price_sensitive", "not_interested", "unknown"],
                },
                "signals": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Key evidence signals that led to this classification",
                },
                "confidence": {"type": "number", "description": "0.0 to 1.0"},
            },
            "required": ["intent", "signals", "confidence"],
        },
    }
    result = _tool_call(client, system, user, tool)
    return result or {"intent": "unknown", "signals": [], "confidence": 0.3}


def _run_engagement_agent(client: anthropic.Anthropic, contact: dict, events: list) -> dict:
    system = (
        "You are the Engagement Scoring Agent for DabbahWala food delivery. "
        "Score this customer's engagement level (0.0=completely cold, 1.0=highly active). "
        "Identify whether engagement is rising, flat, or falling based on recency and frequency of events."
    )
    now = datetime.now(timezone.utc)
    last_event_hours = 999
    if events:
        try:
            last_ts = events[0].get("occurred_at") or events[0].get("created_at")
            if last_ts:
                if isinstance(last_ts, str):
                    last_ts = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                if last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=timezone.utc)
                last_event_hours = int((now - last_ts).total_seconds() / 3600)
        except Exception:
            pass

    user = (
        f"Customer profile:\n{json.dumps(contact, indent=2, default=str)}\n\n"
        f"Events last 30 days (most recent first):\n{json.dumps(events[:30], indent=2, default=str)}"
    )
    tool = {
        "name": "submit_engagement",
        "description": "Submit engagement score and trend",
        "input_schema": {
            "type": "object",
            "properties": {
                "engagement_score": {"type": "number", "description": "0.0 (cold) to 1.0 (very active)"},
                "trend": {"type": "string", "enum": ["rising", "falling", "flat"]},
                "last_touch_hours_ago": {
                    "type": "integer",
                    "description": "Hours since last meaningful interaction",
                },
            },
            "required": ["engagement_score", "trend", "last_touch_hours_ago"],
        },
    }
    result = _tool_call(client, system, user, tool)
    if not result:
        result = {"engagement_score": 0.2, "trend": "flat", "last_touch_hours_ago": last_event_hours}
    return result


def _store_inference(contact_id: int, goal_id: Optional[int], sentiment: dict, intent: dict, engagement: dict) -> int:
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO inference_results
                (contact_id, goal_id,
                 sentiment, sentiment_confidence, sentiment_summary,
                 intent, intent_signals, intent_confidence,
                 engagement_score, engagement_trend, last_touch_hours_ago)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                contact_id, goal_id,
                sentiment.get("sentiment"), sentiment.get("confidence"), sentiment.get("summary"),
                intent.get("intent"), json.dumps(intent.get("signals", [])), intent.get("confidence"),
                engagement.get("engagement_score"), engagement.get("trend"), engagement.get("last_touch_hours_ago"),
            ),
        )
        return cur.fetchone()["id"]


# ---------------------------------------------------------------------------
# LAYER 2 — Decision Agents
# ---------------------------------------------------------------------------

def _run_stage_agent(client: anthropic.Anthropic, contact: dict, inference: dict) -> dict:
    system = (
        "You are the Stage Transition Decision Agent for DabbahWala. "
        "Valid lifecycle stages: cold, engaged, new_customer, active_customer, lapsed_customer, churned. "
        "Recommend a lifecycle stage change only if the inference data clearly supports it. "
        "If the current stage is appropriate, recommend the same stage."
    )
    user = (
        f"Current stage: {contact.get('lifecycle_segment', 'unknown')}\n"
        f"Inference results:\n{json.dumps(inference, indent=2, default=str)}"
    )
    tool = {
        "name": "submit_stage",
        "description": "Recommend lifecycle stage",
        "input_schema": {
            "type": "object",
            "properties": {
                "recommended_stage": {"type": "string"},
                "confidence": {"type": "number"},
                "reason": {"type": "string"},
            },
            "required": ["recommended_stage", "confidence", "reason"],
        },
    }
    result = _tool_call(client, system, user, tool)
    return result or {"recommended_stage": contact.get("lifecycle_segment", "cold"), "confidence": 0.5, "reason": "No change recommended"}


def _run_channel_agent(client: anthropic.Anthropic, contact: dict, inference: dict, recent_actions: list) -> dict:
    system = (
        "You are the Channel Selection Decision Agent for DabbahWala. "
        "Choose the best next communication channel: sms, email, call, or none. "
        "Consider recency of last contact, engagement trend, and intent. "
        "Avoid recommending a channel if it was used in the last 24 hours. "
        "Timing options: immediate, tomorrow, 3days, none."
    )
    user = (
        f"Customer: {contact.get('first_name', '')} | Engagement: {inference.get('engagement_score', 0):.2f} "
        f"| Trend: {inference.get('engagement_trend', 'flat')} | Intent: {inference.get('intent', 'unknown')}\n\n"
        f"Recent actions (last 48h):\n{json.dumps(recent_actions, indent=2, default=str)}"
    )
    tool = {
        "name": "submit_channel",
        "description": "Recommend communication channel and timing",
        "input_schema": {
            "type": "object",
            "properties": {
                "recommended_channel": {"type": "string", "enum": ["sms", "email", "call", "none"]},
                "channel_timing": {"type": "string", "enum": ["immediate", "tomorrow", "3days", "none"]},
                "reason": {"type": "string"},
            },
            "required": ["recommended_channel", "channel_timing", "reason"],
        },
    }
    result = _tool_call(client, system, user, tool)
    return result or {"recommended_channel": "none", "channel_timing": "none", "reason": "Insufficient data"}


def _run_offer_agent(client: anthropic.Anthropic, contact: dict, inference: dict) -> dict:
    system = (
        "You are the Offer Selection Decision Agent for DabbahWala food delivery. "
        "Select the right offer type for this customer: discount, reminder, social_proof, or none. "
        "Also draft a short suggested SMS/email copy (max 160 chars for SMS suitability). "
        "Base your choice on intent and sentiment."
    )
    user = (
        f"Customer: {contact.get('first_name', '')} | Orders: {contact.get('total_orders', 0)} | "
        f"Sentiment: {inference.get('sentiment', 'neutral')} | Intent: {inference.get('intent', 'unknown')}\n"
        f"Engagement score: {inference.get('engagement_score', 0):.2f}"
    )
    tool = {
        "name": "submit_offer",
        "description": "Recommend offer type and copy",
        "input_schema": {
            "type": "object",
            "properties": {
                "offer_type": {"type": "string", "enum": ["discount", "reminder", "social_proof", "none"]},
                "suggested_copy": {"type": "string", "description": "Ready-to-send message copy, max 160 chars"},
                "reason": {"type": "string"},
            },
            "required": ["offer_type", "suggested_copy", "reason"],
        },
    }
    result = _tool_call(client, system, user, tool)
    return result or {"offer_type": "none", "suggested_copy": "", "reason": "No offer appropriate"}


def _run_escalation_agent(client: anthropic.Anthropic, contact: dict, inference: dict, goal: Optional[dict]) -> dict:
    system = (
        "You are the Escalation Decision Agent for DabbahWala. "
        "Determine if this customer needs a human sales team member to intervene. "
        "Escalate when: sentiment is very negative, intent is ready_to_order but stalled, "
        "goal deadline is approaching, or multiple automated attempts have failed. "
        "Urgency: high (act today), medium (act this week), none (no escalation)."
    )
    user = (
        f"Customer: {contact.get('first_name', '')} {contact.get('last_name', '')} | "
        f"Phone: {contact.get('phone', 'N/A')}\n"
        f"Inference: {json.dumps(inference, indent=2, default=str)}\n"
        f"Active goal: {json.dumps(goal, indent=2, default=str)}"
    )
    tool = {
        "name": "submit_escalation",
        "description": "Escalation decision",
        "input_schema": {
            "type": "object",
            "properties": {
                "should_escalate": {"type": "boolean"},
                "urgency": {"type": "string", "enum": ["high", "medium", "none"]},
                "reason": {"type": "string"},
            },
            "required": ["should_escalate", "urgency", "reason"],
        },
    }
    result = _tool_call(client, system, user, tool)
    return result or {"should_escalate": False, "urgency": "none", "reason": "No escalation needed"}


def _store_decision(contact_id: int, inference_id: int, stage: dict, channel: dict, offer: dict, escalation: dict) -> int:
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO decision_recommendations
                (contact_id, inference_result_id,
                 recommended_stage, stage_confidence, stage_reason,
                 recommended_channel, channel_timing, channel_reason,
                 offer_type, suggested_copy, offer_reason,
                 should_escalate, escalation_urgency, escalation_reason)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                contact_id, inference_id,
                stage.get("recommended_stage"), stage.get("confidence"), stage.get("reason"),
                channel.get("recommended_channel"), channel.get("channel_timing"), channel.get("reason"),
                offer.get("offer_type"), offer.get("suggested_copy"), offer.get("reason"),
                escalation.get("should_escalate", False), escalation.get("urgency", "none"), escalation.get("reason"),
            ),
        )
        return cur.fetchone()["id"]


# ---------------------------------------------------------------------------
# LAYER 3 — Orchestrator Agent
# ---------------------------------------------------------------------------

def _run_orchestrator(
    client: anthropic.Anthropic,
    contact: dict,
    decision: dict,
    goal: Optional[dict],
    recent_actions: list,
    delivery_context: Optional[dict] = None,
) -> dict:
    system = (
        "You are the Orchestrator Agent for DabbahWala food delivery marketing. "
        "You receive recommendations from 4 specialist decision agents and must choose the single best next action. "
        "You are goal-oriented: every decision must move the customer closer to placing their NEXT order.\n\n"
        "DELIVERY-AWARE RULES (check these first — they override other signals):\n"
        "  - If latest delivery event is 'delivered': this is the prime reorder window. "
        "Send a warm thank-you SMS with a gentle reorder nudge — UNLESS already messaged in the last 24h.\n"
        "  - If latest delivery event is 'delivery_failed' or 'delivery_returned': action must be "
        "'escalate_airtable' with urgency=high so the team can recover the relationship BEFORE any selling.\n"
        "  - If latest delivery event is 'out_for_delivery', 'driver_assigned', or 'delivery_arrived': "
        "action must be 'none' — do not interrupt an order in progress with marketing.\n"
        "  - If no delivery event in last 7 days: use intent/engagement signals as normal.\n\n"
        "GENERAL GUARDRAILS:\n"
        "  - Never contact a customer more than once every 24 hours via the same channel\n"
        "  - Maximum 3 SMS per week per customer\n"
        "  - Escalation to Airtable takes priority over automated channels\n"
        "  - If intent is not_interested, action must be 'none' unless escalation is high urgency\n"
        "Choose ONE action: send_sms, move_campaign, escalate_airtable, or none. "
        "Explain your full chain of reasoning so it can be audited."
    )
    user = (
        f"Customer: {contact.get('first_name', '')} {contact.get('last_name', '')} | "
        f"Stage: {contact.get('lifecycle_segment', 'unknown')} | "
        f"Phone: {contact.get('phone', 'N/A')} | Email: {contact.get('email', 'N/A')}\n\n"
        f"Latest delivery signal: {json.dumps(delivery_context, indent=2, default=str)}\n\n"
        f"Active goal: {json.dumps(goal, indent=2, default=str)}\n\n"
        f"Decision agent recommendations:\n{json.dumps(decision, indent=2, default=str)}\n\n"
        f"Recent actions (last 48h):\n{json.dumps(recent_actions, indent=2, default=str)}"
    )
    tool = {
        "name": "submit_orchestration",
        "description": "Submit the final orchestrated action decision",
        "input_schema": {
            "type": "object",
            "properties": {
                "chosen_action": {"type": "string", "enum": ["send_sms", "move_campaign", "escalate_airtable", "none"]},
                "chosen_channel": {"type": "string", "enum": ["sms", "email", "airtable", "none"]},
                "action_payload": {
                    "type": "object",
                    "description": "Action-specific payload: for send_sms include message_body; for move_campaign include to_campaign; for escalate_airtable include urgency and notes",
                },
                "reasoning": {"type": "string", "description": "Full chain-of-thought explaining this decision"},
                "guardrails_applied": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of guardrails that were checked",
                },
            },
            "required": ["chosen_action", "chosen_channel", "action_payload", "reasoning", "guardrails_applied"],
        },
    }
    result = _tool_call(client, system, user, tool)
    return result or {
        "chosen_action": "none",
        "chosen_channel": "none",
        "action_payload": {},
        "reasoning": "Orchestrator fallback — no action taken",
        "guardrails_applied": [],
    }


def _store_orchestration(contact_id: int, decision_id: int, goal_id: Optional[int], result: dict) -> int:
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO orchestrator_log
                (contact_id, decision_recommendation_id, goal_id,
                 chosen_action, chosen_channel, reasoning, guardrails_applied)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            RETURNING id
            """,
            (
                contact_id, decision_id, goal_id,
                result.get("chosen_action"), result.get("chosen_channel"),
                result.get("reasoning"), json.dumps(result.get("guardrails_applied", [])),
            ),
        )
        return cur.fetchone()["id"]


def _enqueue_action(contact_id: int, orch_log_id: int, action: str, payload: dict) -> None:
    if action == "none":
        return
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO action_queue (contact_id, orchestrator_log_id, action_type, payload)
            VALUES (%s, %s, %s, %s::jsonb)
            """,
            (contact_id, orch_log_id, action, json.dumps(payload)),
        )


# ---------------------------------------------------------------------------
# Full agent cycle for a single contact
# ---------------------------------------------------------------------------

def _run_full_cycle(contact_id: int) -> dict:
    client = _claude()

    contact = _fetch_contact(contact_id)
    events = _fetch_recent_events(contact_id, days=30)
    comms = _fetch_communications(contact_id, days=30)
    goal = _fetch_active_goal(contact_id)
    recent_actions = _fetch_recent_actions(contact_id, hours=48)
    goal_id = goal["id"] if goal else None

    # Extract latest delivery event to give orchestrator direct delivery context
    delivery_event_types = {
        "delivered", "out_for_delivery", "driver_assigned", "delivery_arrived",
        "delivery_failed", "delivery_returned", "order_accepted",
    }
    latest_delivery = next(
        (e for e in events if e.get("event_type") in delivery_event_types), None
    )

    # Layer 1 — inference (run all 3)
    sentiment = _run_sentiment_agent(client, contact, comms)
    intent = _run_intent_agent(client, contact, events, comms)
    engagement = _run_engagement_agent(client, contact, events)
    inference_id = _store_inference(contact_id, goal_id, sentiment, intent, engagement)

    inference_summary = {
        **sentiment,
        **intent,
        **engagement,
        "inference_id": inference_id,
    }

    # Layer 2 — decisions (run all 4)
    stage = _run_stage_agent(client, contact, inference_summary)
    channel = _run_channel_agent(client, contact, inference_summary, recent_actions)
    offer = _run_offer_agent(client, contact, inference_summary)
    escalation = _run_escalation_agent(client, contact, inference_summary, goal)
    decision_id = _store_decision(contact_id, inference_id, stage, channel, offer, escalation)

    decision_summary = {
        "stage": stage,
        "channel": channel,
        "offer": offer,
        "escalation": escalation,
        "decision_id": decision_id,
    }

    # Layer 3 — orchestrator (receives delivery context directly so it can apply delivery rules)
    orch_result = _run_orchestrator(client, contact, decision_summary, goal, recent_actions, latest_delivery)
    orch_log_id = _store_orchestration(contact_id, decision_id, goal_id, orch_result)

    chosen_action = orch_result.get("chosen_action", "none")
    action_payload = orch_result.get("action_payload", {})
    action_payload["contact_id"] = contact_id
    action_payload["email"] = contact.get("email")
    action_payload["phone"] = contact.get("phone")

    _enqueue_action(contact_id, orch_log_id, chosen_action, action_payload)

    return {
        "contact_id": contact_id,
        "contact_name": f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip(),
        "inference_id": inference_id,
        "decision_id": decision_id,
        "orchestrator_log_id": orch_log_id,
        "chosen_action": chosen_action,
        "chosen_channel": orch_result.get("chosen_channel"),
        "reasoning_snippet": (orch_result.get("reasoning", "") or "")[:200],
    }


# ---------------------------------------------------------------------------
# LAYER 4 — Report Agents
# ---------------------------------------------------------------------------

def _send_email_via_smtp(to: str, subject: str, html_body: str, csv_filename: str, csv_content: str) -> None:
    """Send report email with CSV attachment using SMTP.
    Works with Gmail (App Password), Outlook, or any SMTP relay.
    Required env vars: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, REPORT_EMAIL_FROM.
    """
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    from_email = os.environ.get("REPORT_EMAIL_FROM", "reports@dabbahwala.com")

    if not smtp_user or not smtp_password:
        raise HTTPException(status_code=500, detail="SMTP_USER and SMTP_PASSWORD not configured")

    msg = MIMEMultipart()
    msg["From"] = f"DabbahWala Reports <{from_email}>"
    msg["To"] = to
    msg["Subject"] = subject

    msg.attach(MIMEText(html_body, "html"))

    attachment = MIMEBase("text", "csv")
    attachment.set_payload(csv_content.encode("utf-8"))
    encoders.encode_base64(attachment)
    attachment.add_header("Content-Disposition", f'attachment; filename="{csv_filename}"')
    msg.attach(attachment)

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(from_email, to, msg.as_string())


def _run_activity_report_agent(client: anthropic.Anthropic, report_date: str, raw: dict) -> str:
    """Generate prose activity summary via Claude."""
    system = (
        "You are the Daily Activity Report Agent for DabbahWala food delivery. "
        "Generate a concise, professional HTML email body summarising today's operational activity. "
        "Use <h2> for section headers, <p> for paragraphs. No CSS, inline only. "
        "Be factual and data-driven. End with one sentence on what to watch tomorrow."
    )
    user = (
        f"Date: {report_date}\n\n"
        f"Today's activity data:\n{json.dumps(raw, indent=2, default=str)}"
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text if response.content else "<p>No data available.</p>"


def _run_outcome_report_agent(client: anthropic.Anthropic, report_date: str, raw: dict) -> str:
    """Generate prose outcome summary via Claude."""
    system = (
        "You are the Daily Outcome Report Agent for DabbahWala food delivery. "
        "Generate a concise, professional HTML email body summarising what was achieved today — "
        "orders detected, customers who responded, emails opened, goals achieved. "
        "Use <h2> for section headers, <p> for paragraphs. No CSS, inline only. "
        "Highlight wins. If there were no orders, say so clearly without sugarcoating."
    )
    user = (
        f"Date: {report_date}\n\n"
        f"Today's outcome data:\n{json.dumps(raw, indent=2, default=str)}"
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text if response.content else "<p>No outcome data available.</p>"


def _fetch_activity_data(report_date: str) -> tuple[dict, list]:
    """Returns (summary_dict, detail_rows) for activity report."""
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT event_type, COUNT(*) AS count
            FROM events
            WHERE DATE(occurred_at) = %s::date
            GROUP BY event_type ORDER BY count DESC
            """,
            (report_date,),
        )
        event_counts = {r["event_type"]: r["count"] for r in cur.fetchall()}

        cur.execute(
            """
            SELECT direction, COUNT(*) AS count
            FROM telnyx_tracking
            WHERE DATE(created_at) = %s::date
            GROUP BY direction
            """,
            (report_date,),
        )
        telnyx_counts = {f"telnyx_{r['direction']}": r["count"] for r in cur.fetchall()}

        cur.execute(
            "SELECT COUNT(*) AS c FROM action_queue WHERE DATE(created_at) = %s::date",
            (report_date,),
        )
        actions_queued = cur.fetchone()["c"]

        cur.execute(
            "SELECT COUNT(*) AS c FROM orchestrator_log WHERE DATE(run_at) = %s::date",
            (report_date,),
        )
        orch_runs = cur.fetchone()["c"]

        cur.execute(
            "SELECT COUNT(*) AS c FROM inference_results WHERE DATE(run_at) = %s::date",
            (report_date,),
        )
        inference_runs = cur.fetchone()["c"]

        # Detail rows
        cur.execute(
            """
            SELECT c.first_name, c.last_name, c.email, al.chosen_action, al.chosen_channel,
                   al.run_at, LEFT(al.reasoning, 120) AS reasoning_snippet
            FROM orchestrator_log al
            JOIN contacts c ON c.id = al.contact_id
            WHERE DATE(al.run_at) = %s::date
            ORDER BY al.run_at DESC
            LIMIT 200
            """,
            (report_date,),
        )
        detail_rows = [dict(r) for r in cur.fetchall()]

    summary = {
        **event_counts,
        **telnyx_counts,
        "actions_queued": actions_queued,
        "orchestrator_runs": orch_runs,
        "inference_runs": inference_runs,
    }
    return summary, detail_rows


def _fetch_outcome_data(report_date: str) -> tuple[dict, list]:
    """Returns (summary_dict, detail_rows) for outcome report."""
    with get_cursor(commit=False) as cur:
        # Order signals detected today
        cur.execute(
            """
            SELECT COUNT(*) AS c FROM events
            WHERE DATE(occurred_at) = %s::date AND event_type = 'order_placed'
            """,
            (report_date,),
        )
        orders = cur.fetchone()["c"]

        # Email opens
        cur.execute(
            """
            SELECT COUNT(*) AS c FROM events
            WHERE DATE(occurred_at) = %s::date AND event_type = 'email_open'
            """,
            (report_date,),
        )
        email_opens = cur.fetchone()["c"]

        # Email clicks
        cur.execute(
            """
            SELECT COUNT(*) AS c FROM events
            WHERE DATE(occurred_at) = %s::date AND event_type = 'email_click'
            """,
            (report_date,),
        )
        email_clicks = cur.fetchone()["c"]

        # Inbound SMS (responses)
        cur.execute(
            """
            SELECT COUNT(*) AS c FROM telnyx_tracking
            WHERE DATE(created_at) = %s::date AND direction = 'inbound'
            """,
            (report_date,),
        )
        sms_replies = cur.fetchone()["c"]

        # Goals achieved today
        cur.execute(
            """
            SELECT COUNT(*) AS c FROM customer_goals
            WHERE DATE(updated_at) = %s::date AND status = 'achieved'
            """,
            (report_date,),
        )
        goals_achieved = cur.fetchone()["c"]

        # Detail: customers who ordered today
        cur.execute(
            """
            SELECT DISTINCT c.first_name, c.last_name, c.email, c.phone,
                   c.lifecycle_segment, c.total_orders
            FROM events e
            JOIN contacts c ON c.id = e.contact_id
            WHERE DATE(e.occurred_at) = %s::date AND e.event_type = 'order_placed'
            ORDER BY c.last_name
            LIMIT 200
            """,
            (report_date,),
        )
        order_customers = [dict(r) for r in cur.fetchall()]

    summary = {
        "orders_detected": orders,
        "email_opens": email_opens,
        "email_clicks": email_clicks,
        "inbound_sms_replies": sms_replies,
        "goals_achieved": goals_achieved,
    }
    return summary, order_customers


def _rows_to_csv(rows: list) -> str:
    if not rows:
        return "no_data\n"
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# FastAPI endpoints
# ---------------------------------------------------------------------------

class CycleRequest(BaseModel):
    contact_ids: list[int]


class SingleContactRequest(BaseModel):
    contact_id: int


class ContactEventRequest(BaseModel):
    phone: Optional[str] = None
    email: Optional[str] = None


class ReportRequest(BaseModel):
    report_date: Optional[str] = None  # defaults to today


def _lookup_contact_id(phone: Optional[str] = None, email: Optional[str] = None) -> Optional[int]:
    """Find a contact by phone or email. Returns None if not found."""
    with get_cursor(commit=False) as cur:
        if phone:
            # Normalise: strip spaces/dashes, keep leading +
            normalized = "".join(c for c in phone if c.isdigit() or c == "+")
            cur.execute(
                "SELECT id FROM contacts WHERE phone = %s OR phone = %s LIMIT 1",
                (phone, normalized),
            )
        elif email:
            cur.execute("SELECT id FROM contacts WHERE email = %s LIMIT 1", (email,))
        else:
            return None
        row = cur.fetchone()
        return row["id"] if row else None


@router.post("/cycle/run")
def run_agent_cycle(req: CycleRequest):
    """Run full inference → decision → orchestrator cycle for a list of contacts."""
    results = []
    errors = []
    for cid in req.contact_ids:
        try:
            r = _run_full_cycle(cid)
            results.append(r)
        except HTTPException:
            raise
        except Exception as e:
            errors.append({"contact_id": cid, "error": str(e)})
    return {"processed": len(results), "errors": errors, "results": results}


@router.post("/cycle/run-for-contact")
def run_cycle_for_contact(req: ContactEventRequest):
    """
    Run the full agent cycle for a single contact identified by phone or email.
    Called by the Telnyx inbound collector immediately after every new event,
    so every piece of evidence is evaluated for opportunity in real time.
    """
    contact_id = _lookup_contact_id(phone=req.phone, email=req.email)
    if not contact_id:
        return {"status": "skipped", "reason": "Contact not found", "phone": req.phone, "email": req.email}
    try:
        result = _run_full_cycle(contact_id)
        return {"status": "ok", **result}
    except Exception as e:
        return {"status": "error", "contact_id": contact_id, "error": str(e)}


@router.post("/cycle/run-all")
def run_agent_cycle_all():
    """Run full agent cycle for all contacts with an active goal or high engagement."""
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT DISTINCT c.id FROM contacts c
            LEFT JOIN customer_goals g ON g.contact_id = c.id AND g.status = 'active'
            LEFT JOIN engagement_rollups er ON er.contact_id = c.id
            WHERE g.id IS NOT NULL
               OR (er.opens_30d > 0 AND c.lifecycle_segment != 'churned')
            LIMIT 500
            """
        )
        contact_ids = [r["id"] for r in cur.fetchall()]

    results = []
    errors = []
    for cid in contact_ids:
        try:
            r = _run_full_cycle(cid)
            results.append(r)
        except Exception as e:
            errors.append({"contact_id": cid, "error": str(e)})

    return {"processed": len(results), "errors": errors, "results": results}


@router.post("/report/activity")
def send_activity_report(req: ReportRequest):
    """Generate and email the daily activity report."""
    report_date = req.report_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    to_email = os.environ.get("REPORT_EMAIL_TO", "core@dabbahwala.com")

    summary, detail_rows = _fetch_activity_data(report_date)
    client = _claude()
    html_body = _run_activity_report_agent(client, report_date, summary)
    csv_content = _rows_to_csv(detail_rows)

    _send_email_via_smtp(
        to=to_email,
        subject=f"DabbahWala Activity Report — {report_date}",
        html_body=html_body,
        csv_filename=f"dabbahwala_activity_{report_date}.csv",
        csv_content=csv_content,
    )
    return {"status": "sent", "report_date": report_date, "to": to_email, "summary": summary}


@router.post("/report/outcome")
def send_outcome_report(req: ReportRequest):
    """Generate and email the daily outcome report."""
    report_date = req.report_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    to_email = os.environ.get("REPORT_EMAIL_TO", "core@dabbahwala.com")

    summary, detail_rows = _fetch_outcome_data(report_date)
    client = _claude()
    html_body = _run_outcome_report_agent(client, report_date, summary)
    csv_content = _rows_to_csv(detail_rows)

    _send_email_via_smtp(
        to=to_email,
        subject=f"DabbahWala Results Report — {report_date}",
        html_body=html_body,
        csv_filename=f"dabbahwala_outcomes_{report_date}.csv",
        csv_content=csv_content,
    )
    return {"status": "sent", "report_date": report_date, "to": to_email, "summary": summary}


# --- Action queue management ---

@router.get("/action-queue/pending")
def get_pending_actions():
    """Return all pending actions for n8n executors to drain."""
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT aq.id, aq.contact_id, aq.action_type, aq.payload,
                   aq.created_at, c.email, c.phone, c.first_name, c.last_name
            FROM action_queue aq
            JOIN contacts c ON c.id = aq.contact_id
            WHERE aq.status = 'pending'
            ORDER BY aq.created_at ASC
            LIMIT 100
            """
        )
        return [dict(r) for r in cur.fetchall()]


@router.post("/action-queue/{action_id}/done")
def mark_action_done(action_id: int):
    with get_cursor() as cur:
        cur.execute(
            "UPDATE action_queue SET status='done', executed_at=NOW() WHERE id=%s",
            (action_id,),
        )
    return {"status": "ok", "action_id": action_id}


@router.post("/action-queue/{action_id}/failed")
def mark_action_failed(action_id: int):
    with get_cursor() as cur:
        cur.execute(
            "UPDATE action_queue SET status='failed', executed_at=NOW() WHERE id=%s",
            (action_id,),
        )
    return {"status": "ok", "action_id": action_id}


# --- Goal management ---

class GoalCreate(BaseModel):
    contact_id: int
    goal: str
    deadline: Optional[str] = None


@router.post("/goals")
def create_goal(req: GoalCreate):
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO customer_goals (contact_id, goal, deadline)
            VALUES (%s, %s, %s::date)
            RETURNING id
            """,
            (req.contact_id, req.goal, req.deadline),
        )
        return {"id": cur.fetchone()["id"]}


@router.post("/goals/{goal_id}/achieved")
def mark_goal_achieved(goal_id: int):
    with get_cursor() as cur:
        cur.execute(
            "UPDATE customer_goals SET status='achieved', converted=TRUE WHERE id=%s",
            (goal_id,),
        )
    return {"status": "ok"}
