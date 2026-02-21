"""
DabbahWala — Claude Agent Stack
Inference → Decision → Orchestrator → Action Queue
Plus daily Activity and Outcome reporting agents.
"""

import csv
import io
import json
import logging
import os
import smtplib
import time
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
from app.routers.campaigns import push_lead_to_instantly
from app.services.airtable_sync import create_field_sales_task
from app.services.drive import upload_csv as _drive_upload_csv

logger = logging.getLogger(__name__)

router = APIRouter()

MODEL = "claude-sonnet-4-5-20250929"

# ---------------------------------------------------------------------------
# Anthropic client
# ---------------------------------------------------------------------------

def _claude() -> anthropic.Anthropic:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        logger.error("ANTHROPIC_API_KEY is not set — cannot create Claude client")
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")
    logger.debug("Claude client instantiated with model=%s", MODEL)
    return anthropic.Anthropic(api_key=key)


def _tool_call(client: anthropic.Anthropic, system: str, user: str, tool: dict) -> dict:
    """Call Claude with a single forced tool and return its input dict."""
    tool_name = tool.get("name", "unknown")
    logger.debug("Calling Claude tool=%s model=%s", tool_name, MODEL)
    t0 = time.time()
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": user}],
        )
        elapsed = time.time() - t0
        logger.debug(
            "Claude tool=%s completed in %.2fs (stop_reason=%s input_tokens=%s output_tokens=%s)",
            tool_name,
            elapsed,
            response.stop_reason,
            response.usage.input_tokens if response.usage else "?",
            response.usage.output_tokens if response.usage else "?",
        )
        for block in response.content:
            if block.type == "tool_use":
                logger.debug("Tool=%s returned input keys: %s", tool_name, list(block.input.keys()))
                return block.input
        logger.warning(
            "Tool=%s: Claude returned no tool_use block (stop_reason=%s) — using fallback",
            tool_name,
            response.stop_reason,
        )
        return {}
    except anthropic.RateLimitError as e:
        logger.error("Claude rate limit hit for tool=%s: %s", tool_name, e)
        raise
    except anthropic.APIError as e:
        logger.error("Claude API error for tool=%s: %s (status=%s)", tool_name, e, getattr(e, "status_code", "?"), exc_info=True)
        raise
    except Exception as e:
        logger.error("Unexpected error calling Claude tool=%s: %s", tool_name, e, exc_info=True)
        raise


# ---------------------------------------------------------------------------
# DB helpers — gather context for each agent type
# ---------------------------------------------------------------------------

def _fetch_contact(contact_id: int) -> dict:
    logger.debug("Fetching contact id=%s with engagement rollups", contact_id)
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT c.id, c.first_name, c.last_name, c.email, c.phone,
                   c.lifecycle_segment, c.total_orders, c.sms_level,
                   c.last_order_at, c.created_at,
                   c.priority_override, c.sales_notes,
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
            logger.warning("Contact id=%s not found", contact_id)
            raise HTTPException(status_code=404, detail=f"Contact {contact_id} not found")
        contact = dict(row)
        logger.info(
            "Contact fetched: id=%s name=%s %s segment=%s orders=%s opens_7d=%s",
            contact_id,
            contact.get("first_name", ""),
            contact.get("last_name", ""),
            contact.get("lifecycle_segment"),
            contact.get("total_orders"),
            contact.get("opens_7d"),
        )
        return contact


def _fetch_recent_events(contact_id: int, days: int = 30) -> list:
    logger.debug("Fetching events for contact_id=%s last %d days", contact_id, days)
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
        events = [dict(r) for r in cur.fetchall()]
        logger.debug("Found %d events for contact_id=%s", len(events), contact_id)
        return events


def _fetch_communications(contact_id: int, days: int = 30) -> list:
    """Fetch SMS messages and call transcripts for this contact.

    NOTE: telnyx_messages and telnyx_calls are separate tables (migration 008).
    We query both and merge them into a unified list ordered by recency.
    """
    logger.debug("Fetching comms for contact_id=%s last %d days", contact_id, days)
    with get_cursor(commit=False) as cur:
        # SMS messages
        cur.execute(
            """
            SELECT 'sms' AS comm_type, direction, body, NULL AS transcript,
                   NULL AS summary, sent_at AS occurred_at, is_delivery_staff
            FROM telnyx_messages
            WHERE contact_id = %s AND sent_at >= NOW() - (%s || ' days')::INTERVAL
            ORDER BY sent_at DESC
            LIMIT 15
            """,
            (contact_id, days),
        )
        sms_rows = [dict(r) for r in cur.fetchall()]

        # Voice calls
        cur.execute(
            """
            SELECT 'call' AS comm_type, direction, NULL AS body, transcript,
                   summary, started_at AS occurred_at, is_delivery_staff
            FROM telnyx_calls
            WHERE contact_id = %s AND started_at >= NOW() - (%s || ' days')::INTERVAL
            ORDER BY started_at DESC
            LIMIT 10
            """,
            (contact_id, days),
        )
        call_rows = [dict(r) for r in cur.fetchall()]

    # Merge and sort by recency
    comms = sorted(sms_rows + call_rows, key=lambda r: r.get("occurred_at") or "", reverse=True)[:20]
    logger.debug("Found %d comms for contact_id=%s (sms=%d calls=%d)", len(comms), contact_id, len(sms_rows), len(call_rows))
    return comms


def _fetch_recent_outcomes(contact_id: int, limit: int = 5) -> list:
    """Fetch recent opportunity outcomes for feedback loop.

    Returns the last N opportunity outcomes so agents can see whether
    previous actions (SMS, calls, emails) worked or not.  This is the
    primary feedback signal that closes the loop from human outcomes back
    into AI reasoning.
    """
    logger.debug("Fetching outcome feedback for contact_id=%s (limit=%d)", contact_id, limit)
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT action, priority, reason, suggested_message,
                   status, outcome, outcome_notes, created_at, dispatched_at
            FROM opportunities
            WHERE contact_id = %s AND outcome IS NOT NULL
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (contact_id, limit),
        )
        outcomes = [dict(r) for r in cur.fetchall()]
        if outcomes:
            logger.info(
                "Outcome feedback for contact_id=%s: %d records — latest outcome=%s",
                contact_id,
                len(outcomes),
                outcomes[0].get("outcome"),
            )
        else:
            logger.debug("No outcome feedback found for contact_id=%s", contact_id)
        return outcomes


def _fetch_playbook_rules() -> str:
    """Fetch active agent playbook rules and format them as a prompt section.

    This makes agents DYNAMIC — users can add rules in Airtable that change
    how every agent reasons, without redeploying code.
    Returns empty string if no rules are configured.
    """
    try:
        with get_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT category, rule_name, instruction, priority
                FROM agent_playbook
                WHERE is_active = true
                ORDER BY
                    CASE category
                        WHEN 'exclusion' THEN 1
                        WHEN 'priority'  THEN 2
                        WHEN 'inference' THEN 3
                        WHEN 'decision'  THEN 4
                        WHEN 'messaging' THEN 5
                        ELSE 6
                    END,
                    priority DESC
                """
            )
            rules = cur.fetchall()
        if not rules:
            logger.debug("No active playbook rules found — agents using defaults only")
            return ""
        logger.info("Injecting %d active playbook rules into agent prompts", len(rules))
        categories: dict = {}
        for r in rules:
            cat = r["category"]
            categories.setdefault(cat, []).append(r)
        label_map = {
            "exclusion": "EXCLUSION RULES (Must Follow — override everything)",
            "priority":  "PRIORITY RULES",
            "inference": "INFERENCE RULES (What signals to look for)",
            "decision":  "DECISION RULES (What actions to take)",
            "messaging": "MESSAGING RULES (Tone & copy style)",
            "general":   "GENERAL RULES",
        }
        lines = ["\n\n## Your Active Playbook (User-Configured Rules)\n",
                 "These rules override your defaults. Follow them strictly.\n"]
        for cat in ["exclusion", "priority", "inference", "decision", "messaging", "general"]:
            if cat in categories:
                lines.append(f"\n### {label_map.get(cat, cat.upper())}")
                for r in categories[cat]:
                    lines.append(
                        f"- **{r['rule_name']}** (priority {r['priority']}): {r['instruction']}"
                    )
        return "\n".join(lines)
    except Exception as e:
        logger.warning("Could not fetch playbook rules: %s — agents continuing without them", e)
        return ""


def _fetch_order_preferences(contact_id: int) -> dict:
    """Return this customer's top menu items and typical ordering day — fed to intent agent."""
    with get_cursor(commit=False) as cur:
        # Top 5 items this customer has ordered (by quantity)
        cur.execute(
            """
            SELECT oi.item_name, SUM(oi.quantity) AS qty
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            WHERE o.contact_id = %s
            GROUP BY oi.item_name
            ORDER BY qty DESC
            LIMIT 5
            """,
            (contact_id,),
        )
        top_items = [{"item": r["item_name"], "qty": r["qty"]} for r in cur.fetchall()]

        # Most common ordering day of week
        cur.execute(
            """
            SELECT TRIM(TO_CHAR(order_date, 'Day')) AS day_name,
                   COUNT(*) AS order_count
            FROM orders
            WHERE contact_id = %s
            GROUP BY day_name
            ORDER BY order_count DESC
            LIMIT 3
            """,
            (contact_id,),
        )
        preferred_days = [r["day_name"] for r in cur.fetchall()]

        # Subscription vs one-time split
        cur.execute(
            """
            SELECT order_type, COUNT(*) AS cnt
            FROM orders
            WHERE contact_id = %s AND order_type IS NOT NULL AND order_type != ''
            GROUP BY order_type
            """,
            (contact_id,),
        )
        order_types = {r["order_type"]: r["cnt"] for r in cur.fetchall()}

    return {
        "top_items": top_items,
        "preferred_order_days": preferred_days,
        "order_type_breakdown": order_types,
    }


def _fetch_latest_inference(contact_id: int) -> Optional[dict]:
    logger.debug("Fetching latest inference for contact_id=%s", contact_id)
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
        if row:
            inf = dict(row)
            logger.debug(
                "Latest inference contact_id=%s: sentiment=%s intent=%s engagement=%.2f",
                contact_id,
                inf.get("sentiment"),
                inf.get("intent"),
                inf.get("engagement_score") or 0,
            )
            return inf
        logger.debug("No previous inference found for contact_id=%s", contact_id)
        return None


def _fetch_latest_decision(contact_id: int) -> Optional[dict]:
    logger.debug("Fetching latest decision for contact_id=%s", contact_id)
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
        if row:
            dec = dict(row)
            logger.debug(
                "Latest decision contact_id=%s: channel=%s timing=%s escalate=%s",
                contact_id,
                dec.get("recommended_channel"),
                dec.get("channel_timing"),
                dec.get("should_escalate"),
            )
            return dec
        return None


def _fetch_active_goal(contact_id: int) -> Optional[dict]:
    logger.debug("Fetching active goal for contact_id=%s", contact_id)
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
        if row:
            goal = dict(row)
            logger.info("Active goal contact_id=%s: goal=%s deadline=%s", contact_id, goal.get("goal"), goal.get("deadline"))
            return goal
        logger.debug("No active goal for contact_id=%s", contact_id)
        return None


def _fetch_recent_actions(contact_id: int, hours: int = 48) -> list:
    logger.debug("Fetching recent actions for contact_id=%s last %dh", contact_id, hours)
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
        actions = [dict(r) for r in cur.fetchall()]
        if actions:
            logger.debug("Found %d recent actions for contact_id=%s", len(actions), contact_id)
        return actions


# ---------------------------------------------------------------------------
# LAYER 1 — Inference Agents
# (playbook rules and outcome feedback injected dynamically at call time)
# ---------------------------------------------------------------------------

def _run_sentiment_agent(
    client: anthropic.Anthropic,
    contact: dict,
    comms: list,
    outcomes: list,
    playbook: str,
) -> dict:
    """Assess customer sentiment.

    Args:
        outcomes: Recent opportunity outcomes — the feedback loop signal.
                  If the customer replied positively to past SMS/calls, that's
                  strong positive sentiment evidence even without new comms.
        playbook: User-configured rules from the agent_playbook table.
    """
    logger.info(
        "Running Sentiment Agent for contact_id=%s (%s %s) — comms=%d outcomes=%d",
        contact.get("id"),
        contact.get("first_name", ""),
        contact.get("last_name", ""),
        len(comms),
        len(outcomes),
    )
    system = (
        "You are the Sentiment Inference Agent for DabbahWala, a fresh, home-style Indian food delivery service based in Atlanta, GA (4015 Holcomb Bridge Rd, Norcross, GA 30071 — dabbahwala.com/contact-us). "
        "Your only job is to assess customer sentiment from their communication history. "
        "Be concise. Focus on tone, word choice, and responsiveness patterns.\n\n"
        "FEEDBACK LOOP — Previous Outcomes:\n"
        "If recent opportunity outcomes show 'ordered' or positive replies, that is STRONG evidence "
        "of positive sentiment. If outcomes show 'not_interested', 'declined', or repeated 'no_answer', "
        "weight sentiment toward negative/neutral accordingly."
        + playbook
    )
    user = (
        f"Customer: {contact.get('first_name', '')} {contact.get('last_name', '')} | "
        f"Lifecycle: {contact.get('lifecycle_segment', 'unknown')} | "
        f"Total orders: {contact.get('total_orders', 0)} | "
        f"Last order: {contact.get('last_order_at', 'never')}\n\n"
        + (f"Ground team sales note (treat as high-confidence): {contact['sales_notes']}\n\n" if contact.get("sales_notes") else "")
        + f"Recent communications ({len(comms)} records):\n{json.dumps(comms, indent=2, default=str)}\n\n"
        f"Previous action outcomes (feedback loop — {len(outcomes)} records):\n"
        f"{json.dumps(outcomes, indent=2, default=str)}"
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
    if not result:
        logger.warning(
            "Sentiment agent fallback for contact_id=%s — returning neutral/0.4", contact.get("id")
        )
        return {"sentiment": "neutral", "confidence": 0.4, "summary": "Insufficient data"}
    logger.info(
        "Sentiment result contact_id=%s: %s (confidence=%.2f)",
        contact.get("id"),
        result.get("sentiment"),
        result.get("confidence", 0),
    )
    return result


def _run_intent_agent(
    client: anthropic.Anthropic,
    contact: dict,
    events: list,
    comms: list,
    outcomes: list,
    playbook: str,
    order_prefs: Optional[dict] = None,
) -> dict:
    """Classify purchase intent.

    outcomes: Outcome feedback loop — if the customer 'ordered' after an SMS,
    that's strong evidence for 'ready_to_order' baseline even without fresh events.
    """
    logger.info(
        "Running Intent Agent for contact_id=%s events=%d comms=%d outcomes=%d",
        contact.get("id"),
        len(events),
        len(comms),
        len(outcomes),
    )
    # Build feedback section from outcomes
    outcome_summary = ""
    if outcomes:
        ordered = sum(1 for o in outcomes if o.get("outcome") == "ordered")
        declined = sum(1 for o in outcomes if o.get("outcome") in ("not_interested", "declined"))
        no_answer = sum(1 for o in outcomes if o.get("outcome") == "no_answer")
        outcome_summary = (
            f"\n\nFEEDBACK LOOP — Historical outcomes for this contact: "
            f"ordered={ordered} declined={declined} no_answer={no_answer}. "
            f"If ordered > 0, weight toward ready_to_order or repeat_buyer. "
            f"If declined > 2, weight toward not_interested."
        )
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
        + outcome_summary
        + playbook
    )
    prefs_section = ""
    if order_prefs:
        items = ", ".join(
            f"{p['item']} (×{p['qty']})" for p in (order_prefs.get("top_items") or [])
        )
        days = ", ".join(order_prefs.get("preferred_order_days") or [])
        types = order_prefs.get("order_type_breakdown") or {}
        prefs_section = (
            f"\n\nCustomer order preferences (lifetime data):\n"
            f"  Top items: {items or 'unknown'}\n"
            f"  Preferred order days: {days or 'unknown'}\n"
            f"  Order type breakdown: {json.dumps(types)}\n"
            "Use this to personalise intent assessment — a customer who always orders "
            "on Thursdays and hasn't ordered this Thursday yet is likely in buying mode."
        )
    user = (
        f"Customer: {contact.get('first_name', '')} {contact.get('last_name', '')} | "
        f"Lifecycle: {contact.get('lifecycle_segment', 'unknown')} | "
        f"Orders: {contact.get('total_orders', 0)} | "
        f"Opens (7d/30d): {contact.get('opens_7d', 0)}/{contact.get('opens_30d', 0)} | "
        f"Clicks (7d/30d): {contact.get('clicks_7d', 0)}/{contact.get('clicks_30d', 0)}"
        f"{prefs_section}\n\n"
        + (f"Ground team sales note (treat as high-confidence): {contact['sales_notes']}\n\n" if contact.get("sales_notes") else "")
        + f"Recent events ({len(events)} total, showing first 25):\n"
        f"{json.dumps(events[:25], indent=2, default=str)}\n\n"
        f"Recent communications ({len(comms)} total, showing first 10):\n"
        f"{json.dumps(comms[:10], indent=2, default=str)}\n\n"
        f"Outcome history ({len(outcomes)} records):\n"
        f"{json.dumps(outcomes, indent=2, default=str)}"
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
    if not result:
        logger.warning("Intent agent fallback for contact_id=%s", contact.get("id"))
        return {"intent": "unknown", "signals": [], "confidence": 0.3}
    logger.info(
        "Intent result contact_id=%s: %s (confidence=%.2f signals=%s)",
        contact.get("id"),
        result.get("intent"),
        result.get("confidence", 0),
        result.get("signals", []),
    )
    return result


def _run_engagement_agent(
    client: anthropic.Anthropic,
    contact: dict,
    events: list,
    playbook: str,
) -> dict:
    """Score engagement level and trend."""
    logger.info(
        "Running Engagement Agent for contact_id=%s events=%d",
        contact.get("id"),
        len(events),
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
                logger.debug(
                    "Last event for contact_id=%s was %dh ago", contact.get("id"), last_event_hours
                )
        except Exception as e:
            logger.warning(
                "Could not parse last event timestamp for contact_id=%s: %s", contact.get("id"), e
            )

    system = (
        "You are the Engagement Scoring Agent for DabbahWala food delivery. "
        "Score this customer's engagement level (0.0=completely cold, 1.0=highly active). "
        "Identify whether engagement is rising, flat, or falling based on recency and frequency of events."
        + playbook
    )
    user = (
        f"Customer profile:\n{json.dumps(contact, indent=2, default=str)}\n\n"
        f"Events last 30 days ({len(events)} total, showing first 30):\n"
        f"{json.dumps(events[:30], indent=2, default=str)}"
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
        logger.warning(
            "Engagement agent fallback for contact_id=%s (last_event=%dh ago)",
            contact.get("id"),
            last_event_hours,
        )
        result = {"engagement_score": 0.2, "trend": "flat", "last_touch_hours_ago": last_event_hours}
    logger.info(
        "Engagement result contact_id=%s: score=%.2f trend=%s last_touch=%dh",
        contact.get("id"),
        result.get("engagement_score", 0),
        result.get("trend"),
        result.get("last_touch_hours_ago", 999),
    )
    return result


def _store_inference(contact_id: int, goal_id: Optional[int], sentiment: dict, intent: dict, engagement: dict) -> int:
    logger.debug(
        "Storing inference for contact_id=%s sentiment=%s intent=%s engagement=%.2f",
        contact_id,
        sentiment.get("sentiment"),
        intent.get("intent"),
        engagement.get("engagement_score", 0),
    )
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
        inference_id = cur.fetchone()["id"]
        logger.info("Inference stored id=%s for contact_id=%s", inference_id, contact_id)
        return inference_id


# ---------------------------------------------------------------------------
# LAYER 2 — Decision Agents
# (playbook rules injected via _run_full_cycle before calling these)
# ---------------------------------------------------------------------------

def _run_stage_agent(
    client: anthropic.Anthropic,
    contact: dict,
    inference: dict,
    playbook: str,
) -> dict:
    """Recommend a lifecycle stage transition."""
    logger.info(
        "Running Stage Agent for contact_id=%s current_stage=%s intent=%s",
        contact.get("id"),
        contact.get("lifecycle_segment"),
        inference.get("intent"),
    )
    system = (
        "You are the Stage Transition Decision Agent for DabbahWala. "
        "Valid lifecycle stages: cold, engaged, new_customer, active_customer, lapsed_customer, churned. "
        "Recommend a lifecycle stage change only if the inference data clearly supports it. "
        "If the current stage is appropriate, recommend the same stage."
        + playbook
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
    if not result:
        logger.warning("Stage agent fallback for contact_id=%s", contact.get("id"))
        return {
            "recommended_stage": contact.get("lifecycle_segment", "cold"),
            "confidence": 0.5,
            "reason": "No change recommended",
        }
    logger.info(
        "Stage result contact_id=%s: %s→%s (confidence=%.2f)",
        contact.get("id"),
        contact.get("lifecycle_segment"),
        result.get("recommended_stage"),
        result.get("confidence", 0),
    )
    return result


def _run_channel_agent(
    client: anthropic.Anthropic,
    contact: dict,
    inference: dict,
    recent_actions: list,
    playbook: str,
) -> dict:
    """Recommend the best communication channel and timing."""
    logger.info(
        "Running Channel Agent for contact_id=%s engagement=%.2f trend=%s",
        contact.get("id"),
        inference.get("engagement_score", 0),
        inference.get("engagement_trend", "flat"),
    )
    system = (
        "You are the Channel Selection Decision Agent for DabbahWala. "
        "Choose the best next communication channel: sms, email, call, or none. "
        "Consider recency of last contact, engagement trend, and intent. "
        "Avoid recommending a channel if it was used in the last 24 hours. "
        "Timing options: immediate, tomorrow, 3days, none."
        + playbook
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
    if not result:
        logger.warning("Channel agent fallback for contact_id=%s", contact.get("id"))
        return {"recommended_channel": "none", "channel_timing": "none", "reason": "Insufficient data"}
    logger.info(
        "Channel result contact_id=%s: channel=%s timing=%s",
        contact.get("id"),
        result.get("recommended_channel"),
        result.get("channel_timing"),
    )
    return result


def _run_offer_agent(
    client: anthropic.Anthropic,
    contact: dict,
    inference: dict,
    outcomes: list,
    playbook: str,
) -> dict:
    """Recommend offer type and generate ready-to-send copy.

    outcomes: Feedback loop — if previous discount offers led to 'ordered',
    continue discounts. If they led to 'not_interested', try social_proof instead.
    """
    logger.info(
        "Running Offer Agent for contact_id=%s sentiment=%s intent=%s outcomes=%d",
        contact.get("id"),
        inference.get("sentiment"),
        inference.get("intent"),
        len(outcomes),
    )
    # Analyse what has worked before (feedback loop)
    outcome_insight = ""
    if outcomes:
        ordered_after_discount = any(
            o.get("outcome") == "ordered" and "discount" in (o.get("reason") or "").lower()
            for o in outcomes
        )
        rejected_offers = sum(1 for o in outcomes if o.get("outcome") in ("not_interested", "declined"))
        if ordered_after_discount:
            outcome_insight = "\n\nFEEDBACK: Discounts have worked for this customer — prefer discount offer type."
        elif rejected_offers >= 2:
            outcome_insight = (
                "\n\nFEEDBACK: Multiple past offers were declined. "
                "Try social_proof instead of discount this time."
            )

    system = (
        "You are the Offer Selection Decision Agent for DabbahWala food delivery. "
        "Select the right offer type for this customer: discount, reminder, social_proof, or none. "
        "Also draft a short suggested SMS/email copy (max 160 chars for SMS suitability). "
        "Base your choice on intent, sentiment, and what has worked historically."
        + outcome_insight
        + playbook
    )
    user = (
        f"Customer: {contact.get('first_name', '')} | Orders: {contact.get('total_orders', 0)} | "
        f"Sentiment: {inference.get('sentiment', 'neutral')} | Intent: {inference.get('intent', 'unknown')}\n"
        f"Engagement score: {inference.get('engagement_score', 0):.2f}\n\n"
        f"Previous outcomes ({len(outcomes)} records):\n{json.dumps(outcomes, indent=2, default=str)}"
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
    if not result:
        logger.warning("Offer agent fallback for contact_id=%s", contact.get("id"))
        return {"offer_type": "none", "suggested_copy": "", "reason": "No offer appropriate"}
    copy_len = len(result.get("suggested_copy", ""))
    if copy_len > 160:
        logger.warning(
            "Offer copy for contact_id=%s is %d chars (>160 SMS limit) — may be truncated on send",
            contact.get("id"),
            copy_len,
        )
    logger.info(
        "Offer result contact_id=%s: type=%s copy_len=%d",
        contact.get("id"),
        result.get("offer_type"),
        copy_len,
    )
    return result


def _run_escalation_agent(
    client: anthropic.Anthropic,
    contact: dict,
    inference: dict,
    goal: Optional[dict],
    outcomes: list,
    playbook: str,
) -> dict:
    """Decide whether to escalate to a human field sales agent.

    outcomes: Feedback loop — if multiple automated actions failed without conversion,
    escalation likelihood should increase.
    """
    logger.info(
        "Running Escalation Agent for contact_id=%s sentiment=%s intent=%s goal=%s",
        contact.get("id"),
        inference.get("sentiment"),
        inference.get("intent"),
        goal.get("goal") if goal else "none",
    )
    failed_attempts = sum(1 for o in outcomes if o.get("outcome") in ("no_answer", "not_interested"))
    escalation_context = ""
    if failed_attempts >= 3:
        escalation_context = (
            f"\n\nFEEDBACK: This contact has had {failed_attempts} failed automated attempts "
            f"(no_answer or not_interested). Strong case for human escalation."
        )
    system = (
        "You are the Escalation Decision Agent for DabbahWala. "
        "Determine if this customer needs a human sales team member to intervene. "
        "Escalate when: sentiment is very negative, intent is ready_to_order but stalled, "
        "goal deadline is approaching, or multiple automated attempts have failed. "
        "Urgency: high (act today), medium (act this week), none (no escalation)."
        + escalation_context
        + playbook
    )
    user = (
        f"Customer: {contact.get('first_name', '')} {contact.get('last_name', '')} | "
        f"Phone: {contact.get('phone', 'N/A')}\n"
        f"Inference: {json.dumps(inference, indent=2, default=str)}\n"
        f"Active goal: {json.dumps(goal, indent=2, default=str)}\n"
        f"Failed attempts (from feedback): {failed_attempts}"
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
    if not result:
        logger.warning("Escalation agent fallback for contact_id=%s", contact.get("id"))
        return {"should_escalate": False, "urgency": "none", "reason": "No escalation needed"}
    if result.get("should_escalate"):
        logger.info(
            "ESCALATION recommended for contact_id=%s: urgency=%s reason=%s",
            contact.get("id"),
            result.get("urgency"),
            (result.get("reason") or "")[:80],
        )
    else:
        logger.debug("No escalation for contact_id=%s", contact.get("id"))
    return result


def _store_decision(
    contact_id: int,
    inference_id: int,
    stage: dict,
    channel: dict,
    offer: dict,
    escalation: dict,
) -> int:
    logger.debug(
        "Storing decision for contact_id=%s channel=%s offer=%s escalate=%s",
        contact_id,
        channel.get("recommended_channel"),
        offer.get("offer_type"),
        escalation.get("should_escalate"),
    )
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
        decision_id = cur.fetchone()["id"]
        logger.info("Decision stored id=%s for contact_id=%s", decision_id, contact_id)
        return decision_id


# ---------------------------------------------------------------------------
# LAYER 3 — Orchestrator Agent
# ---------------------------------------------------------------------------

def _run_orchestrator(
    client: anthropic.Anthropic,
    contact: dict,
    decision: dict,
    goal: Optional[dict],
    recent_actions: list,
    playbook: str,
    delivery_context: Optional[dict] = None,
) -> dict:
    logger.info(
        "Running Orchestrator for contact_id=%s delivery=%s escalate=%s",
        contact.get("id"),
        delivery_context.get("event_type") if delivery_context else "none",
        decision.get("escalation", {}).get("should_escalate"),
    )
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
        "  - If intent is not_interested, action must be 'none' unless escalation is high urgency\n\n"
        "GROUND TEAM OVERRIDES (check after delivery rules):\n"
        "  - If priority_override is 'do_not_contact': action must be 'none' — no exceptions.\n"
        "  - If priority_override is 'high': treat as hot-priority customer; prefer escalate_airtable "
        "or send_sms over 'none' even when signals are weak.\n"
        "  - sales_notes (if present) are ground team observations pinned to this customer — "
        "treat them as high-confidence context that overrides AI inference.\n"
        "Choose ONE action: send_sms, move_campaign, escalate_airtable, or none. "
        "Explain your full chain of reasoning so it can be audited."
        + playbook
    )
    user = (
        f"Customer: {contact.get('first_name', '')} {contact.get('last_name', '')} | "
        f"Stage: {contact.get('lifecycle_segment', 'unknown')} | "
        f"Phone: {contact.get('phone', 'N/A')} | Email: {contact.get('email', 'N/A')}\n\n"
        f"Ground team overrides: priority_override={contact.get('priority_override', 'none')} | "
        f"sales_notes={contact.get('sales_notes') or 'none'}\n\n"
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
    if not result:
        logger.warning("Orchestrator fallback for contact_id=%s — no action taken", contact.get("id"))
        return {
            "chosen_action": "none",
            "chosen_channel": "none",
            "action_payload": {},
            "reasoning": "Orchestrator fallback — no action taken",
            "guardrails_applied": [],
        }
    logger.info(
        "Orchestrator result contact_id=%s: action=%s channel=%s guardrails=%s",
        contact.get("id"),
        result.get("chosen_action"),
        result.get("chosen_channel"),
        result.get("guardrails_applied", []),
    )
    return result


def _store_orchestration(contact_id: int, decision_id: int, goal_id: Optional[int], result: dict) -> int:
    logger.debug(
        "Storing orchestration for contact_id=%s action=%s",
        contact_id,
        result.get("chosen_action"),
    )
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
        orch_id = cur.fetchone()["id"]
        logger.info("Orchestrator log stored id=%s for contact_id=%s", orch_id, contact_id)
        return orch_id


def _enqueue_action(contact_id: int, orch_log_id: int, action: str, payload: dict) -> None:
    if action == "none":
        logger.debug("No action to enqueue for contact_id=%s", contact_id)
        return
    logger.info(
        "Enqueueing action=%s for contact_id=%s (orch_log_id=%s)",
        action,
        contact_id,
        orch_log_id,
    )
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
    """Run the complete 3-layer agent pipeline for a single contact.

    Fetches playbook rules and outcome feedback ONCE at the start,
    then injects both into all 7 agents so their reasoning reflects:
    - User-configured rules (dynamic, from Airtable/UI)
    - Historical outcome feedback (what worked / what didn't)
    """
    logger.info("=== Starting full agent cycle for contact_id=%s ===", contact_id)
    t_start = time.time()
    client = _claude()

    # Gather all context
    contact = _fetch_contact(contact_id)

    # Respect do_not_contact override — skip entire pipeline, no outreach
    if contact.get("priority_override") == "do_not_contact":
        logger.info(
            "Skipping agent cycle for contact_id=%s — priority_override=do_not_contact", contact_id
        )
        return {
            "contact_id": contact_id,
            "contact_name": f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip(),
            "chosen_action": "none",
            "chosen_channel": "none",
            "action_payload": {},
            "reasoning": "Skipped — ground team set priority_override=do_not_contact",
            "outcomes_used": 0,
            "playbook_rules_active": 0,
        }

    events = _fetch_recent_events(contact_id, days=30)
    comms = _fetch_communications(contact_id, days=30)
    goal = _fetch_active_goal(contact_id)
    recent_actions = _fetch_recent_actions(contact_id, hours=48)
    outcomes = _fetch_recent_outcomes(contact_id, limit=10)   # feedback loop
    playbook = _fetch_playbook_rules()                         # dynamic config
    goal_id = goal["id"] if goal else None

    logger.info(
        "Context gathered for contact_id=%s: events=%d comms=%d actions=%d outcomes=%d playbook_len=%d",
        contact_id,
        len(events),
        len(comms),
        len(recent_actions),
        len(outcomes),
        len(playbook),
    )

    # Extract latest delivery event to give orchestrator direct delivery context
    delivery_event_types = {
        "delivered", "out_for_delivery", "driver_assigned", "delivery_arrived",
        "delivery_failed", "delivery_returned", "order_accepted",
    }
    latest_delivery = next(
        (e for e in events if e.get("event_type") in delivery_event_types), None
    )
    if latest_delivery:
        logger.info(
            "Latest delivery event for contact_id=%s: %s at %s",
            contact_id,
            latest_delivery.get("event_type"),
            latest_delivery.get("occurred_at"),
        )

    # Layer 1 — inference (3 agents, outcome feedback + playbook injected)
    logger.info("--- Layer 1: Inference ---")
    order_prefs = _fetch_order_preferences(contact_id)
    sentiment = _run_sentiment_agent(client, contact, comms, outcomes, playbook)
    intent = _run_intent_agent(client, contact, events, comms, outcomes, playbook, order_prefs)
    engagement = _run_engagement_agent(client, contact, events, playbook)
    inference_id = _store_inference(contact_id, goal_id, sentiment, intent, engagement)

    inference_summary = {
        **sentiment,
        **intent,
        **engagement,
        "inference_id": inference_id,
    }

    # Layer 2 — decisions (4 agents, outcome feedback + playbook injected)
    logger.info("--- Layer 2: Decision ---")
    stage = _run_stage_agent(client, contact, inference_summary, playbook)
    channel = _run_channel_agent(client, contact, inference_summary, recent_actions, playbook)
    offer = _run_offer_agent(client, contact, inference_summary, outcomes, playbook)
    escalation = _run_escalation_agent(client, contact, inference_summary, goal, outcomes, playbook)
    decision_id = _store_decision(contact_id, inference_id, stage, channel, offer, escalation)

    decision_summary = {
        "stage": stage,
        "channel": channel,
        "offer": offer,
        "escalation": escalation,
        "decision_id": decision_id,
    }

    # Layer 3 — orchestrator (playbook injected; delivery context + goal guide final choice)
    logger.info("--- Layer 3: Orchestrator ---")
    orch_result = _run_orchestrator(
        client, contact, decision_summary, goal, recent_actions, playbook, latest_delivery
    )
    orch_log_id = _store_orchestration(contact_id, decision_id, goal_id, orch_result)

    chosen_action = orch_result.get("chosen_action", "none")
    action_payload = orch_result.get("action_payload", {})
    action_payload["contact_id"] = contact_id
    action_payload["email"] = contact.get("email")
    action_payload["phone"] = contact.get("phone")

    _enqueue_action(contact_id, orch_log_id, chosen_action, action_payload)

    elapsed = time.time() - t_start
    logger.info(
        "=== Agent cycle complete contact_id=%s in %.1fs: action=%s channel=%s ===",
        contact_id,
        elapsed,
        chosen_action,
        orch_result.get("chosen_channel"),
    )

    return {
        "contact_id": contact_id,
        "contact_name": f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip(),
        "contact": contact,
        "inference_id": inference_id,
        "decision_id": decision_id,
        "orchestrator_log_id": orch_log_id,
        "chosen_action": chosen_action,
        "chosen_channel": orch_result.get("chosen_channel"),
        "action_payload": action_payload,
        "reasoning_snippet": (orch_result.get("reasoning", "") or "")[:200],
        "outcomes_used": len(outcomes),
        "playbook_rules_active": len(playbook.split("\n")) if playbook else 0,
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
        logger.error("SMTP credentials not configured — cannot send report email to %s", to)
        raise HTTPException(status_code=500, detail="SMTP_USER and SMTP_PASSWORD not configured")

    logger.info("Sending report email to=%s subject='%s' via %s:%d", to, subject, smtp_host, smtp_port)
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

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(from_email, to, msg.as_string())
        logger.info("Report email sent to=%s attachment=%s", to, csv_filename)
    except smtplib.SMTPException as e:
        logger.error("SMTP error sending report email to %s: %s", to, e, exc_info=True)
        raise


def _run_activity_report_agent(client: anthropic.Anthropic, report_date: str, raw: dict) -> str:
    """Generate prose activity summary via Claude."""
    system = (
        "You are the Daily Activity Report Agent for DabbahWala food delivery in Atlanta, GA. "
        "Generate a concise, professional HTML email body summarising today's operational activity. "
        "Use <h2> for section headers, <p> for paragraphs. No CSS, inline only. "
        "Sections to cover:\n"
        "1. System Activity — agent runs, actions queued, SMS/calls sent\n"
        "2. Field Agent Performance — if field_agent_reviews data is present, give an honest "
        "   assessment of each agent: pitch quality score, did they ask for the order, "
        "   customer signals. Call out any agent who had a low score or never asked for the order. "
        "   Be direct — the business owner needs to know who is underperforming.\n"
        "3. What to Watch Tomorrow — one specific action item.\n"
        "Be factual, data-driven, and do not sugarcoat poor field agent performance."
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
        "You are the Daily Outcome Report Agent for DabbahWala food delivery in Atlanta, GA. "
        "Generate a concise, professional HTML email body. Use <h2> for section headers, "
        "<p> for paragraphs, <ul>/<li> for lists. No CSS, inline only.\n\n"
        "Sections to cover:\n"
        "1. Orders & Revenue — orders detected today, revenue if available.\n"
        "2. Order Patterns (last 30 days) — which day of the week drives the most orders, "
        "   which menu items are trending up or down, customer frequency breakdown "
        "   (weekly subscribers vs occasional buyers). Surface 2–3 actionable insights.\n"
        "3. Field Agent Results — if scorecard data is present, score each agent on: "
        "   calls made, orders closed, ask-for-order rate. Flag any agent who is consistently "
        "   NOT asking for the order. Be direct — 'Agent X made 5 calls but only asked for "
        "   the order once and closed zero orders. Needs coaching.'\n"
        "4. Suggestions — 2–3 specific improvements based on the pattern data "
        "   (e.g. 'Schedule more outreach on Thursdays — highest order day', "
        "   'Promote Thali Box — your #1 item by quantity, mention it in every call').\n"
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

        # Field agent call reviews today
        cur.execute(
            """
            SELECT r.agent_name, r.pitch_quality_score, r.asked_for_order,
                   r.customer_signal, r.honest_assessment, r.recommended_next_action,
                   c.first_name, c.last_name
            FROM field_agent_reviews r
            JOIN contacts c ON c.id = r.contact_id
            WHERE DATE(r.reviewed_at) = %s::date
            ORDER BY r.reviewed_at DESC
            """,
            (report_date,),
        )
        field_agent_reviews_today = [dict(r) for r in cur.fetchall()]

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
        "field_agent_reviews_today": field_agent_reviews_today,
    }
    return summary, detail_rows


def _fetch_outcome_data(report_date: str) -> tuple[dict, list]:
    """Returns (summary_dict, detail_rows) for outcome report."""
    with get_cursor(commit=False) as cur:
        # Orders placed today (one row per order, deduped via orders table)
        cur.execute(
            """
            SELECT COUNT(*) AS c FROM orders
            WHERE order_date = %s::date
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

        # Detail: unique customers who ordered today (from orders table — one row per order)
        cur.execute(
            """
            SELECT c.first_name, c.last_name, c.email, c.phone,
                   c.lifecycle_segment, c.total_orders,
                   COUNT(o.id) AS orders_today
            FROM orders o
            JOIN contacts c ON c.id = o.contact_id
            WHERE o.order_date = %s::date
            GROUP BY c.id, c.first_name, c.last_name, c.email, c.phone,
                     c.lifecycle_segment, c.total_orders
            ORDER BY c.last_name
            LIMIT 200
            """,
            (report_date,),
        )
        order_customers = [dict(r) for r in cur.fetchall()]

        # Order patterns — day of week breakdown (last 30 days)
        cur.execute("SELECT get_order_day_patterns(30)")
        raw_days = cur.fetchone()["get_order_day_patterns"]
        order_day_patterns = (
            json.loads(raw_days) if isinstance(raw_days, str) else (raw_days or [])
        )

        # Top menu items (last 30 days)
        cur.execute("SELECT get_top_menu_items(30, 10)")
        raw_menu = cur.fetchone()["get_top_menu_items"]
        top_menu_items = (
            json.loads(raw_menu) if isinstance(raw_menu, str) else (raw_menu or [])
        )

        # Customer frequency segments (last 30 days)
        cur.execute("SELECT get_customer_frequency_segments(30)")
        raw_seg = cur.fetchone()["get_customer_frequency_segments"]
        customer_segments = (
            json.loads(raw_seg) if isinstance(raw_seg, str) else (raw_seg or [])
        )

        # Field agent scorecard (last 7 days)
        cur.execute("SELECT get_field_agent_scorecard(7)")
        raw_scorecard = cur.fetchone()["get_field_agent_scorecard"]
        field_agent_scorecard = (
            json.loads(raw_scorecard) if isinstance(raw_scorecard, str) else (raw_scorecard or [])
        )

        # Recent field agent call reviews (last 7 days)
        cur.execute("SELECT get_recent_field_agent_reviews(7)")
        raw_reviews = cur.fetchone()["get_recent_field_agent_reviews"]
        field_agent_reviews = (
            json.loads(raw_reviews) if isinstance(raw_reviews, str) else (raw_reviews or [])
        )

    summary = {
        "orders_detected": orders,
        "email_opens": email_opens,
        "email_clicks": email_clicks,
        "inbound_sms_replies": sms_replies,
        "goals_achieved": goals_achieved,
        "order_day_patterns_30d": order_day_patterns,
        "top_menu_items_30d": top_menu_items,
        "customer_frequency_segments_30d": customer_segments,
        "field_agent_scorecard_7d": field_agent_scorecard,
        "field_agent_call_reviews_7d": field_agent_reviews,
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
    name: Optional[str] = None


class ReportRequest(BaseModel):
    report_date: Optional[str] = None  # defaults to today


def _lookup_contact_id(
    phone: Optional[str] = None,
    email: Optional[str] = None,
    name: Optional[str] = None,
) -> Optional[int]:
    """Find a contact by phone, email, or name. Returns None if not found."""
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
        elif name:
            parts = name.strip().split(None, 1)
            first = parts[0]
            last  = parts[1] if len(parts) > 1 else ""
            cur.execute(
                """SELECT id FROM contacts
                   WHERE (first_name ILIKE %s AND last_name ILIKE %s)
                      OR CONCAT(first_name, ' ', last_name) ILIKE %s
                   LIMIT 1""",
                (first, last if last else "%", f"%{name.strip()}%"),
            )
        else:
            return None
        row = cur.fetchone()
        return row["id"] if row else None


@router.post("/cycle/run")
def run_agent_cycle(req: CycleRequest):
    """Run full inference → decision → orchestrator cycle for a list of contacts."""
    logger.info("POST /cycle/run — %d contacts requested", len(req.contact_ids))
    results = []
    errors = []
    for cid in req.contact_ids:
        try:
            r = _run_full_cycle(cid)
            results.append(r)
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Cycle failed for contact_id=%s: %s", cid, e, exc_info=True)
            errors.append({"contact_id": cid, "error": str(e)})
    logger.info(
        "POST /cycle/run complete — processed=%d errors=%d",
        len(results),
        len(errors),
    )
    return {"processed": len(results), "errors": errors, "results": results}


@router.post("/cycle/run-for-contact")
def run_cycle_for_contact(req: ContactEventRequest):
    """
    Run the full agent cycle for a single contact identified by phone or email.
    Called by the Telnyx inbound collector immediately after every new event,
    so every piece of evidence is evaluated for opportunity in real time.
    """
    logger.info("POST /cycle/run-for-contact phone=%s email=%s name=%s", req.phone, req.email, req.name)
    contact_id = _lookup_contact_id(phone=req.phone, email=req.email, name=req.name)
    if not contact_id:
        logger.warning(
            "Contact not found for phone=%s email=%s name=%s — skipping cycle", req.phone, req.email, req.name
        )
        return {"status": "skipped", "reason": "Contact not found", "phone": req.phone, "email": req.email, "name": req.name}
    try:
        result = _run_full_cycle(contact_id)
        return {"status": "ok", **result}
    except Exception as e:
        logger.error("Cycle failed for contact_id=%s: %s", contact_id, e, exc_info=True)
        return {"status": "error", "contact_id": contact_id, "error": str(e)}


@router.post("/cycle/run-all")
def run_agent_cycle_all():
    """Run full agent cycle for all contacts eligible for nightly processing.

    Eligibility (any one condition, excluding churned/optout):
      - Has an active sales goal
      - Opened or clicked an email in the last 30 days
      - Had any event (delivery, order, SMS click) in the last 30 days
      - Placed an order in the last 60 days
    """
    logger.info("POST /cycle/run-all — querying eligible contacts")
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT DISTINCT c.id FROM contacts c
            LEFT JOIN customer_goals g
                   ON g.contact_id = c.id AND g.status = 'active'
            LEFT JOIN engagement_rollups er
                   ON er.contact_id = c.id
            LEFT JOIN events ev
                   ON ev.contact_id = c.id
                  AND ev.occurred_at > now() - interval '30 days'
            LEFT JOIN orders o
                   ON o.contact_id = c.id
                  AND o.order_date > CURRENT_DATE - 60
            WHERE c.lifecycle_segment NOT IN ('churned', 'optout')
              AND (
                  g.id IS NOT NULL      -- active sales goal
                  OR er.opens_30d > 0  -- email engaged in last 30 days
                  OR ev.id IS NOT NULL  -- any event in last 30 days (delivery, order, etc.)
                  OR o.id IS NOT NULL   -- ordered in last 60 days
              )
            LIMIT 500
            """
        )
        contact_ids = [r["id"] for r in cur.fetchall()]

    logger.info("run-all: found %d eligible contacts to process", len(contact_ids))
    if not contact_ids:
        logger.info("run-all: no eligible contacts — skipping")
        return {"processed": 0, "errors": [], "results": []}

    results = []
    errors = []
    t_batch = time.time()
    for i, cid in enumerate(contact_ids, 1):
        try:
            r = _run_full_cycle(cid)
            results.append(r)
            if i % 20 == 0:
                logger.info(
                    "run-all progress: %d/%d processed (%d errors) elapsed=%.0fs",
                    i,
                    len(contact_ids),
                    len(errors),
                    time.time() - t_batch,
                )
        except Exception as e:
            logger.error("Cycle failed for contact_id=%s in run-all: %s", cid, e, exc_info=True)
            errors.append({"contact_id": cid, "error": str(e)})

    logger.info(
        "run-all complete: %d processed %d errors in %.0fs",
        len(results),
        len(errors),
        time.time() - t_batch,
    )
    return {"processed": len(results), "errors": errors, "results": results}


@router.post("/cycle/run-all-contacts")
def run_agent_cycle_all_contacts(limit: int = 1000):
    """
    Run the full inference → decision → orchestrator cycle for ALL non-opted-out contacts,
    then immediately dispatch each decision:
      - move_campaign  → push lead to Instantly campaign right now
      - escalate_airtable → create Airtable task right now
    Skips the n8n polling delay entirely.
    """
    logger.info("POST /cycle/run-all-contacts — selecting up to %d contacts", limit)
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT id FROM contacts
            WHERE lifecycle_segment != 'optout'
              AND (email IS NOT NULL OR phone IS NOT NULL)
            ORDER BY last_order_at DESC NULLS LAST
            LIMIT %s
            """,
            (limit,),
        )
        contact_ids = [r["id"] for r in cur.fetchall()]

    logger.info("run-all-contacts: %d eligible contacts", len(contact_ids))
    if not contact_ids:
        return {"processed": 0, "campaigns_pushed": 0, "airtable_pushed": 0, "errors": [], "results": []}

    results = []
    errors = []
    campaigns_pushed = 0
    airtable_pushed = 0
    t_batch = time.time()

    for i, cid in enumerate(contact_ids, 1):
        try:
            r = _run_full_cycle(cid)
            chosen_action = r.get("chosen_action", "none")
            payload = r.get("action_payload", {})
            contact = r.get("contact", {})

            if chosen_action == "move_campaign":
                to_campaign = payload.get("to_campaign", "")
                email = payload.get("email") or contact.get("email", "")
                if email and to_campaign:
                    try:
                        pushed = push_lead_to_instantly(
                            email=email,
                            first_name=contact.get("first_name", ""),
                            last_name=contact.get("last_name", ""),
                            phone=payload.get("phone") or contact.get("phone", "") or "",
                            campaign_name=to_campaign,
                        )
                        if pushed:
                            campaigns_pushed += 1
                            r["instantly_pushed"] = to_campaign
                            logger.info(
                                "Instantly push: contact_id=%s → campaign=%s", cid, to_campaign
                            )
                    except Exception as e:
                        logger.warning("Instantly push failed contact_id=%s: %s", cid, e)

            elif chosen_action == "escalate_airtable":
                urgency = payload.get("urgency", "high")
                notes = payload.get("notes", "") or r.get("reasoning_snippet", "")
                try:
                    create_field_sales_task({
                        "first_name": contact.get("first_name", ""),
                        "last_name": contact.get("last_name", ""),
                        "phone": contact.get("phone", "") or "",
                        "email": contact.get("email", "") or "",
                        "priority": "Hot" if urgency == "high" else "Warm",
                        "reason": notes,
                        "suggested_message": notes,
                        "action_type": "escalate_airtable",
                        "lifecycle_segment": contact.get("lifecycle_segment", ""),
                        "total_orders": contact.get("total_orders", 0),
                        "last_order_at": contact.get("last_order_at"),
                    })
                    airtable_pushed += 1
                    r["airtable_pushed"] = True
                    logger.info("Airtable task created: contact_id=%s urgency=%s", cid, urgency)
                except Exception as e:
                    logger.warning("Airtable push failed contact_id=%s: %s", cid, e)

            # Strip the full contact dict from the result to keep response lean
            r.pop("contact", None)
            results.append(r)

            if i % 20 == 0:
                logger.info(
                    "run-all-contacts progress: %d/%d | campaigns=%d airtable=%d errors=%d elapsed=%.0fs",
                    i, len(contact_ids), campaigns_pushed, airtable_pushed, len(errors),
                    time.time() - t_batch,
                )

        except Exception as e:
            logger.error("Cycle failed contact_id=%s in run-all-contacts: %s", cid, e, exc_info=True)
            errors.append({"contact_id": cid, "error": str(e)})

    elapsed = time.time() - t_batch
    logger.info(
        "run-all-contacts complete: processed=%d campaigns_pushed=%d airtable_pushed=%d errors=%d in %.0fs",
        len(results), campaigns_pushed, airtable_pushed, len(errors), elapsed,
    )
    return {
        "processed": len(results),
        "campaigns_pushed": campaigns_pushed,
        "airtable_pushed": airtable_pushed,
        "errors": errors,
        "elapsed_seconds": round(elapsed),
        "results": results,
    }


@router.get("/report/activity-data")
def get_activity_data(report_date: Optional[str] = None):
    """Return activity data as JSON for dashboard display (does not send email)."""
    date = report_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    summary, detail_rows = _fetch_activity_data(date)
    return {
        "report_date": date,
        "summary": summary,
        "detail_rows": [
            {k: str(v) if hasattr(v, "isoformat") else v for k, v in r.items()}
            for r in detail_rows
        ],
    }


@router.get("/report/outcome-data")
def get_outcome_data(report_date: Optional[str] = None):
    """Return outcome data as JSON for dashboard display (does not send email)."""
    date = report_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    summary, detail_rows = _fetch_outcome_data(date)
    return {
        "report_date": date,
        "summary": summary,
        "detail_rows": [
            {k: str(v) if hasattr(v, "isoformat") else v for k, v in r.items()}
            for r in detail_rows
        ],
    }


@router.post("/report/activity")
def send_activity_report(req: ReportRequest):
    """Generate and email the daily activity report."""
    report_date = req.report_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    to_email = os.environ.get("REPORT_EMAIL_TO", "core@dabbahwala.com")

    summary, detail_rows = _fetch_activity_data(report_date)
    client = _claude()
    html_body = _run_activity_report_agent(client, report_date, summary)
    csv_filename = f"dabbahwala_activity_{report_date}.csv"
    csv_content = _rows_to_csv(detail_rows)

    _send_email_via_smtp(
        to=to_email,
        subject=f"DabbahWala Activity Report — {report_date}",
        html_body=html_body,
        csv_filename=csv_filename,
        csv_content=csv_content,
    )
    drive_url = _drive_upload_csv(csv_content, csv_filename)
    return {"status": "sent", "report_date": report_date, "to": to_email, "summary": summary, "drive_url": drive_url or None}


@router.post("/report/outcome")
def send_outcome_report(req: ReportRequest):
    """Generate and email the daily outcome report."""
    report_date = req.report_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    to_email = os.environ.get("REPORT_EMAIL_TO", "core@dabbahwala.com")

    summary, detail_rows = _fetch_outcome_data(report_date)
    client = _claude()
    html_body = _run_outcome_report_agent(client, report_date, summary)
    csv_filename = f"dabbahwala_outcomes_{report_date}.csv"
    csv_content = _rows_to_csv(detail_rows)

    _send_email_via_smtp(
        to=to_email,
        subject=f"DabbahWala Results Report — {report_date}",
        html_body=html_body,
        csv_filename=csv_filename,
        csv_content=csv_content,
    )
    drive_url = _drive_upload_csv(csv_content, csv_filename)
    return {"status": "sent", "report_date": report_date, "to": to_email, "summary": summary, "drive_url": drive_url or None}


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
    logger.info("Action id=%s marked done", action_id)
    with get_cursor() as cur:
        cur.execute(
            "UPDATE action_queue SET status='done', executed_at=NOW() WHERE id=%s",
            (action_id,),
        )
    return {"status": "ok", "action_id": action_id}


@router.post("/action-queue/{action_id}/failed")
def mark_action_failed(action_id: int):
    logger.warning("Action id=%s marked FAILED — n8n executor reported error", action_id)
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
    logger.info(
        "Creating goal for contact_id=%s goal=%s deadline=%s",
        req.contact_id,
        req.goal,
        req.deadline,
    )
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO customer_goals (contact_id, goal, deadline)
            VALUES (%s, %s, %s::date)
            RETURNING id
            """,
            (req.contact_id, req.goal, req.deadline),
        )
        goal_id = cur.fetchone()["id"]
        logger.info("Goal created id=%s for contact_id=%s", goal_id, req.contact_id)
        return {"id": goal_id}


@router.post("/goals/{goal_id}/achieved")
def mark_goal_achieved(goal_id: int):
    logger.info("Goal id=%s marked ACHIEVED — conversion confirmed", goal_id)
    with get_cursor() as cur:
        cur.execute(
            "UPDATE customer_goals SET status='achieved', converted=TRUE WHERE id=%s",
            (goal_id,),
        )
    return {"status": "ok"}
