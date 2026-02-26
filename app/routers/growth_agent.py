"""
Growth Hacker Agent — Goal-oriented order acquisition through continuous experimentation.

Unlike the Intelligence Engine (which reacts to known signals) and the Claude Agent
(which personalises outreach for flagged contacts), this agent:

  1. INVENTS experiments — Claude generates novel hypotheses: timing tricks, offer hooks,
     message angles, channel sequences that haven't been tried.
  2. SELECTS cohorts — picks an appropriately sized group of untouched contacts.
  3. DISPATCHES — creates opportunities the existing action queue will execute.
  4. MEASURES — after 7 days, checks whether contacted contacts ordered.
  5. LEARNS — Claude analyses results, updates the knowledge base, and informs
     the next round of hypotheses.

This agent runs on two n8n triggers:
  - Weekly (Monday): design + launch a new experiment
  - Weekly (Monday+7d): measure the previous experiment and generate learnings

Endpoints:
  POST /api/growth/run-cycle   — Design + launch a new experiment
  POST /api/growth/measure     — Score completed experiments
  GET  /api/growth/experiments — List all experiments
  GET  /api/growth/insights    — Distilled learnings Claude can act on
"""
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import anthropic
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_cursor

logger = logging.getLogger(__name__)
router = APIRouter()

MODEL = "claude-sonnet-4-5-20250929"
COHORT_MIN = 15
COHORT_MAX = 60


# ---------------------------------------------------------------------------
# Anthropic helper
# ---------------------------------------------------------------------------

def _claude() -> anthropic.Anthropic:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")
    return anthropic.Anthropic(api_key=key)


def _tool_call(client: anthropic.Anthropic, system: str, user: str, tool: dict) -> dict:
    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
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
# DB context builders
# ---------------------------------------------------------------------------

def _get_past_experiments(cur) -> list[dict]:
    cur.execute(
        """SELECT name, experiment_type, hypothesis, cohort_size, conversion_rate,
                  is_winner, learnings, status, started_at
           FROM experiments
           ORDER BY started_at DESC LIMIT 20"""
    )
    return [dict(r) for r in cur.fetchall()]


def _get_baseline(cur) -> dict:
    """Most recent baseline conversion rate."""
    cur.execute(
        "SELECT baseline_conv_rate, measured_at FROM growth_baseline "
        "ORDER BY measured_at DESC LIMIT 1"
    )
    row = cur.fetchone()
    return dict(row) if row else {"baseline_conv_rate": 0.05, "measured_at": None}


def _get_current_menu(cur) -> list[str]:
    cur.execute(
        "SELECT item_name, category, is_veg FROM menu_catalog WHERE active = TRUE ORDER BY category NULLS LAST, item_name",
    )
    rows = cur.fetchall()
    return [f"{r['item_name']} ({'veg' if r['is_veg'] else 'non-veg'}, {r['category']})" for r in rows]


def _get_available_cohort(cur, size: int) -> list[dict]:
    """
    Find contacts eligible for a growth experiment:
    - Not opted out / cooling
    - Not already in a running experiment
    - Not in an active opportunity
    - Has an email or phone
    Sorted to prefer contacts with at least some history (more signal).
    """
    cur.execute(
        """SELECT c.id, c.first_name, c.last_name, c.email, c.phone,
                  c.lifecycle_segment, c.total_orders, c.last_order_at,
                  c.primary_source,
                  r.opens_7d, r.orders_7d
           FROM contacts c
           LEFT JOIN engagement_rollups r ON r.contact_id = c.id
           WHERE c.lifecycle_segment NOT IN ('optout', 'cooling')
             AND (c.email IS NOT NULL OR c.phone IS NOT NULL)
             -- Not already in a running experiment
             AND NOT EXISTS (
                 SELECT 1 FROM experiment_contacts ec
                 JOIN experiments e ON e.id = ec.experiment_id
                 WHERE ec.contact_id = c.id AND e.status IN ('running', 'measuring')
             )
             -- Not in a pending opportunity
             AND NOT EXISTS (
                 SELECT 1 FROM opportunities o
                 WHERE o.contact_id = c.id AND o.status IN ('pending', 'dispatched')
             )
           ORDER BY
             -- Preference: lapsed but has history, then new, then cold
             CASE
               WHEN c.total_orders >= 2 AND c.last_order_at < now() - interval '14 days' THEN 1
               WHEN c.total_orders = 1 THEN 2
               WHEN COALESCE(r.opens_7d, 0) >= 1 THEN 3
               ELSE 4
             END,
             c.last_order_at DESC NULLS LAST
           LIMIT %s""",
        (size,),
    )
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Phase 1: Design an experiment
# ---------------------------------------------------------------------------

DESIGN_SYSTEM = """You are the DabbahWala growth hacker agent. Your job is to invent novel
marketing experiments that find NEW ways to generate orders — not just react to existing
signals, but actively CREATE new signals.

DabbahWala sells fresh home-style Indian food in Atlanta. Channels: SMS, email, phone call.

You have access to:
- Past experiments and their results (wins/losses and learnings)
- The current week's menu
- The baseline 7-day conversion rate

Design ONE experiment that:
1. Is meaningfully different from past experiments
2. Has a clear, falsifiable hypothesis (IF we do X, THEN Y will happen BECAUSE Z)
3. Specifies the exact message/offer/sequence the cohort will receive
4. Specifies who to target (which lifecycle_segment, order history characteristics, etc.)
5. Explains what a "win" looks like (order within 7 days)

Experiment types to rotate through:
- timing: unusual send time (Sunday morning, Friday 4pm, lunch hour burst)
- offer: tangible incentive (free dessert, free roti add-on, $5 credit)
- message_angle: psychological hook (scarcity, social proof, nostalgia, identity)
- channel_sequence: multi-step (SMS → email 48h later, call → SMS follow-up)

Avoid repeating experiments that already ran (check past_experiments).
Be creative — think like a growth hacker, not a marketer."""

DESIGN_TOOL = {
    "name": "design_experiment",
    "description": "Design a growth experiment for DabbahWala",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Short experiment name (e.g., 'Sunday Morning Nostalgia SMS')"},
            "experiment_type": {"type": "string", "enum": ["timing", "offer", "message_angle", "channel_sequence"]},
            "hypothesis": {"type": "string", "description": "Full IF-THEN-BECAUSE hypothesis"},
            "target_segment": {
                "type": "string",
                "description": "Which contacts to target (lifecycle segment, order count, etc.)"
            },
            "cohort_size": {"type": "integer", "description": "How many contacts (15-60)"},
            "channel": {"type": "string", "enum": ["sms", "email", "phone", "sequence"]},
            "message_template": {
                "type": "string",
                "description": "Exact message to send (keep SMS under 160 chars; for sequence, describe each step)"
            },
            "offer_detail": {
                "type": "string",
                "description": "If there's an offer/incentive, describe it precisely. Empty string if none."
            },
            "win_condition": {
                "type": "string",
                "description": "What counts as a win (e.g., 'places any order within 7 days')"
            },
            "rationale": {
                "type": "string",
                "description": "Why this hasn't been tried or why it's worth testing now given the current menu/season"
            },
        },
        "required": [
            "name", "experiment_type", "hypothesis", "target_segment",
            "cohort_size", "channel", "message_template", "win_condition", "rationale"
        ],
    },
}


def _design_experiment(past_experiments: list, baseline: dict, menu_items: list) -> dict:
    client = _claude()
    user_prompt = json.dumps({
        "past_experiments": past_experiments,
        "baseline_7d_conversion_rate": baseline.get("baseline_conv_rate", 0.05),
        "current_week_menu": menu_items,
        "today": date.today().isoformat(),
    }, default=str, indent=2)
    result = _tool_call(client, DESIGN_SYSTEM, user_prompt, DESIGN_TOOL)
    return result


# ---------------------------------------------------------------------------
# Phase 2: Dispatch — create opportunities for the cohort
# ---------------------------------------------------------------------------

def _dispatch_experiment(cur, experiment_id: int, design: dict, cohort: list[dict]) -> int:
    """Create an opportunity for each cohort member and record them in experiment_contacts."""
    action_map = {
        "sms": "send_sms",
        "email": "send_email",
        "phone": "field_sales_call",
        "sequence": "send_sms",  # first step; follow-up handled by measure phase
    }
    opp_action = action_map.get(design.get("channel", "sms"), "send_sms")
    message = design.get("message_template", "")
    dispatched = 0

    for contact in cohort:
        cid = contact["id"]
        first_name = contact.get("first_name", "there") or "there"
        # Personalise the message with the contact's first name
        personal_msg = message.replace("{{first_name}}", first_name).replace("{first_name}", first_name)

        # Create opportunity
        cur.execute(
            "SELECT create_opportunity(%s, %s::opportunity_action, 'warm', %s, %s, 0.70)",
            (
                cid,
                opp_action,
                f"[Growth Experiment #{experiment_id}] {design.get('name', '')}",
                personal_msg,
            ),
        )
        opp_row = cur.fetchone()
        opp_id = list(opp_row.values())[0] if opp_row else None

        # Log to experiment_contacts
        cur.execute(
            """INSERT INTO experiment_contacts
                (experiment_id, contact_id, opportunity_id, action_sent, message_sent, sent_at)
               VALUES (%s, %s, %s, %s, %s, now())
               ON CONFLICT (experiment_id, contact_id) DO NOTHING""",
            (experiment_id, cid, opp_id, design.get("channel", "sms"), personal_msg),
        )
        dispatched += 1

    return dispatched


# ---------------------------------------------------------------------------
# Phase 3: Measure — score a completed experiment
# ---------------------------------------------------------------------------

MEASURE_SYSTEM = """You are the DabbahWala growth hacker agent analyzing the results of a marketing experiment.

Given the experiment design, cohort stats, and order data, produce:
1. A clear verdict: did the experiment beat the baseline conversion rate?
2. Key learnings in plain language
3. Specific recommendations for what to try next based on these results

Be analytical and honest — a failed experiment is still valuable learning."""

MEASURE_TOOL = {
    "name": "analyze_experiment",
    "description": "Analyze results of a growth experiment",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_winner": {"type": "boolean", "description": "Did conversion rate beat the baseline?"},
            "learnings": {
                "type": "string",
                "description": "2-4 sentences of plain-English learnings from this experiment"
            },
            "next_hypothesis": {
                "type": "string",
                "description": "One specific follow-up experiment to try based on these results"
            },
        },
        "required": ["is_winner", "learnings", "next_hypothesis"],
    },
}


def _measure_experiment(cur, experiment: dict) -> dict:
    """Check which cohort members ordered and compute conversion rate."""
    exp_id = experiment["id"]

    # Check orders placed by cohort members since the experiment started
    cur.execute(
        """UPDATE experiment_contacts ec
           SET ordered = true,
               order_id = o.id,
               ordered_at = o.created_at,
               days_to_order = EXTRACT(DAY FROM o.created_at - ec.sent_at)::int
           FROM orders o
           WHERE ec.experiment_id = %s
             AND o.contact_id = ec.contact_id
             AND o.created_at >= ec.sent_at
             AND o.created_at <= ec.sent_at + interval '7 days'
             AND ec.ordered = false""",
        (exp_id,),
    )

    # Totals
    cur.execute(
        """SELECT
             COUNT(*) as total,
             COUNT(*) FILTER (WHERE ordered) as ordered,
             AVG(days_to_order) FILTER (WHERE ordered) as avg_days
           FROM experiment_contacts WHERE experiment_id = %s""",
        (exp_id,),
    )
    stats = dict(cur.fetchone())

    total = stats.get("total", 0) or 1  # avoid /0
    orders_won = stats.get("ordered", 0)
    conv_rate = orders_won / total

    baseline = _get_baseline(cur)

    # Ask Claude to analyse
    client = _claude()
    user_prompt = json.dumps({
        "experiment": {
            "name": experiment.get("name"),
            "hypothesis": experiment.get("hypothesis"),
            "experiment_type": experiment.get("experiment_type"),
            "channel": experiment.get("channel"),
            "message_template": experiment.get("message_template"),
            "offer_detail": experiment.get("offer_detail"),
        },
        "results": {
            "cohort_size": total,
            "orders_won": orders_won,
            "conversion_rate": round(conv_rate, 4),
            "avg_days_to_order": round(stats.get("avg_days") or 0, 1),
        },
        "baseline_conv_rate": baseline.get("baseline_conv_rate", 0.05),
    }, default=str, indent=2)

    analysis = _tool_call(client, MEASURE_SYSTEM, user_prompt, MEASURE_TOOL)
    return {
        "cohort_size": total,
        "orders_won": orders_won,
        "conversion_rate": round(conv_rate, 4),
        "is_winner": analysis.get("is_winner", conv_rate > float(baseline.get("baseline_conv_rate", 0.05))),
        "learnings": analysis.get("learnings", ""),
        "next_hypothesis": analysis.get("next_hypothesis", ""),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/run-cycle")
def run_growth_cycle():
    """
    Design and launch a new experiment.
    Called weekly by n8n (Monday morning).
    """
    with get_cursor(commit=True) as cur:
        past = _get_past_experiments(cur)
        baseline = _get_baseline(cur)
        menu = _get_current_menu(cur)

        # Design
        design = _design_experiment(past, baseline, menu)
        if not design:
            return {"status": "skipped", "reason": "Claude returned empty experiment design"}

        cohort_size = max(COHORT_MIN, min(COHORT_MAX, int(design.get("cohort_size", 20))))
        cohort = _get_available_cohort(cur, cohort_size)

        if len(cohort) < 5:
            return {
                "status": "skipped",
                "reason": f"Not enough eligible contacts (found {len(cohort)}, need at least 5)",
                "design": design,
            }

        # Persist experiment
        measure_at = datetime.now(tz=timezone.utc) + timedelta(days=7)
        cur.execute(
            """INSERT INTO experiments
                (name, hypothesis, experiment_type, cohort_size, channel,
                 message_template, offer_detail, metadata, status, measure_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, 'running', %s)
               RETURNING id""",
            (
                design.get("name"),
                design.get("hypothesis"),
                design.get("experiment_type"),
                len(cohort),
                design.get("channel"),
                design.get("message_template"),
                design.get("offer_detail", ""),
                json.dumps({
                    "target_segment": design.get("target_segment"),
                    "win_condition": design.get("win_condition"),
                    "rationale": design.get("rationale"),
                }),
                measure_at,
            ),
        )
        exp_id = cur.fetchone()["id"]

        # Dispatch
        dispatched = _dispatch_experiment(cur, exp_id, design, cohort)

        # Update cohort_size to actual dispatched count
        cur.execute(
            "UPDATE experiments SET cohort_size = %s WHERE id = %s",
            (dispatched, exp_id),
        )

    logger.info(
        "growth_cycle: new experiment #%d '%s' launched — cohort=%d measure_at=%s",
        exp_id, design.get("name"), dispatched, measure_at.isoformat(),
    )
    return {
        "status": "launched",
        "experiment_id": exp_id,
        "name": design.get("name"),
        "experiment_type": design.get("experiment_type"),
        "channel": design.get("channel"),
        "cohort_size": dispatched,
        "measure_at": measure_at.isoformat(),
        "hypothesis": design.get("hypothesis"),
    }


@router.post("/measure")
def measure_experiments():
    """
    Score all experiments whose measure_at time has passed.
    Called weekly by n8n (7 days after launch).
    """
    measured = []

    with get_cursor(commit=False) as cur:
        cur.execute(
            """SELECT id, name, hypothesis, experiment_type, channel,
                      message_template, offer_detail, cohort_size,
                      started_at, measure_at
               FROM experiments
               WHERE status = 'running'
                 AND measure_at <= now()
               ORDER BY measure_at""",
        )
        due = [dict(r) for r in cur.fetchall()]

    for exp in due:
        with get_cursor(commit=True) as cur:
            try:
                result = _measure_experiment(cur, exp)

                cur.execute(
                    """UPDATE experiments SET
                        status          = 'complete',
                        measured_at     = now(),
                        contacts_reached = %s,
                        orders_won      = %s,
                        conversion_rate = %s,
                        is_winner       = %s,
                        learnings       = %s
                       WHERE id = %s""",
                    (
                        result["cohort_size"],
                        result["orders_won"],
                        result["conversion_rate"],
                        result["is_winner"],
                        result["learnings"] + "\n\nNext: " + result.get("next_hypothesis", ""),
                        exp["id"],
                    ),
                )

                measured.append({
                    "experiment_id": exp["id"],
                    "name": exp["name"],
                    **result,
                })
                logger.info(
                    "growth_measure: #%d '%s' — conv=%.1f%% winner=%s",
                    exp["id"], exp["name"],
                    result["conversion_rate"] * 100,
                    result["is_winner"],
                )
            except Exception as e:
                logger.exception("growth_measure: failed to measure experiment #%d", exp["id"])
                with get_cursor(commit=True) as cur2:
                    cur2.execute(
                        "UPDATE experiments SET status = 'failed' WHERE id = %s", (exp["id"],)
                    )

    return {"measured": len(measured), "results": measured}


@router.get("/experiments")
def list_experiments(limit: int = 20, status: Optional[str] = None):
    """List growth experiments with results."""
    with get_cursor(commit=False) as cur:
        if status:
            cur.execute(
                """SELECT id, name, experiment_type, channel, hypothesis, cohort_size,
                          orders_won, conversion_rate, is_winner, learnings,
                          status, started_at, measure_at, measured_at
                   FROM experiments WHERE status = %s
                   ORDER BY started_at DESC LIMIT %s""",
                (status, limit),
            )
        else:
            cur.execute(
                """SELECT id, name, experiment_type, channel, hypothesis, cohort_size,
                          orders_won, conversion_rate, is_winner, learnings,
                          status, started_at, measure_at, measured_at
                   FROM experiments
                   ORDER BY started_at DESC LIMIT %s""",
                (limit,),
            )
        return {"experiments": [dict(r) for r in cur.fetchall()]}


@router.get("/insights")
def get_insights():
    """
    Return distilled learnings from all completed experiments,
    plus a Claude-generated summary of what's working.
    """
    with get_cursor(commit=False) as cur:
        cur.execute(
            """SELECT name, experiment_type, channel, hypothesis, conversion_rate,
                      is_winner, learnings, started_at
               FROM experiments
               WHERE status = 'complete' AND learnings IS NOT NULL
               ORDER BY started_at DESC LIMIT 30"""
        )
        completed = [dict(r) for r in cur.fetchall()]
        baseline = _get_baseline(cur)

    if not completed:
        return {"insights": "No completed experiments yet.", "experiments": []}

    # Ask Claude to synthesise
    client = _claude()
    system = """You are the DabbahWala growth analyst. Synthesise experiment results into
actionable insights. Be specific: which message angles work, which channels convert,
what timing seems effective, what offers resonate. Keep it to 5-7 bullet points."""

    user = json.dumps({
        "baseline_conv_rate": baseline.get("baseline_conv_rate"),
        "completed_experiments": completed,
    }, default=str, indent=2)

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        summary = resp.content[0].text if resp.content else "Unable to generate insights."
    except Exception as e:
        logger.error("insights: Claude call failed: %s", e)
        summary = "Insights unavailable — see raw experiment results below."

    return {
        "baseline_conv_rate": baseline.get("baseline_conv_rate"),
        "experiments_completed": len(completed),
        "winners": sum(1 for e in completed if e.get("is_winner")),
        "insights_summary": summary,
        "experiments": completed,
    }


@router.post("/baseline/update")
def update_baseline():
    """
    Recalculate and store the 7-day baseline conversion rate
    (contacts who received any outreach → placed an order within 7 days).
    Call this before measuring experiments for accurate comparison.
    """
    with get_cursor(commit=True) as cur:
        cur.execute(
            """WITH outreached AS (
                SELECT DISTINCT o.contact_id, MIN(o.created_at) as first_outreach
                FROM opportunities o
                WHERE o.created_at > now() - interval '30 days'
                  AND o.status IN ('dispatched', 'completed')
                GROUP BY o.contact_id
            ),
            converters AS (
                SELECT ou.contact_id
                FROM outreached ou
                JOIN orders ord ON ord.contact_id = ou.contact_id
                  AND ord.created_at BETWEEN ou.first_outreach
                    AND ou.first_outreach + interval '7 days'
            )
            SELECT
              COUNT(DISTINCT ou.contact_id) as total_outreached,
              COUNT(DISTINCT c.contact_id) as total_converted
            FROM outreached ou
            LEFT JOIN converters c ON c.contact_id = ou.contact_id"""
        )
        row = cur.fetchone()
        total = row["total_outreached"] or 1
        converted = row["total_converted"] or 0
        rate = converted / total

        cur.execute(
            """INSERT INTO growth_baseline (period_days, total_contacts, total_orders, baseline_conv_rate)
               VALUES (7, %s, %s, %s)""",
            (total, converted, rate),
        )

    return {
        "baseline_conv_rate": round(rate, 4),
        "total_outreached_30d": total,
        "total_converted_7d": converted,
    }
