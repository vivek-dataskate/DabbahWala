"""
Goal-Oriented Agent — DabbahWala

The orchestration agent is reactive: it waits for signals and then decides what to do.
This agent is proactive: it sets a goal (get more orders), generates hypotheses about
what *might* work, tests them against real contact cohorts, measures results, and turns
proven ideas into new permanent signals the rest of the system can use.

Four-phase loop (runs daily):
  1. HYPOTHESIZE — Claude generates 3-5 new experiment ideas based on data patterns
  2. EXPERIMENT  — Select cohorts, craft messages, enqueue actions via action_queue
  3. MEASURE     — After 72h, count conversions and conclude each running experiment
  4. HARVEST     — Proven experiments become reusable discovered_signals entries

This creates a continuous improvement loop: ideas → test → signal → system learns.
"""

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import anthropic
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_cursor

logger = logging.getLogger(__name__)

router = APIRouter()

MODEL = "claude-sonnet-4-5-20250929"

MAX_COHORT_SIZE = 20          # contacts per experiment (keep tests manageable)
MAX_EXPERIMENTS_PER_RUN = 3   # new experiments generated per hypothesize phase
MEASUREMENT_WINDOW_HOURS = 72 # hours before we check conversion


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class GoalAgentResult(BaseModel):
    timestamp: str
    phase: str
    experiments_created: int = 0
    experiments_started: int = 0
    contacts_enrolled: int = 0
    experiments_concluded: int = 0
    signals_discovered: int = 0
    orders_attributed: int = 0
    details: dict = {}


# ---------------------------------------------------------------------------
# Anthropic client + shared tool-call helper (mirrors agents.py pattern)
# ---------------------------------------------------------------------------

def _claude() -> anthropic.Anthropic:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")
    return anthropic.Anthropic(api_key=key)


def _tool_call(client: anthropic.Anthropic, system: str, user: str, tool: dict) -> dict:
    tool_name = tool.get("name", "unknown")
    t0 = time.time()
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": user}],
        )
        elapsed = time.time() - t0
        logger.debug("GoalAgent tool=%s %.2fs", tool_name, elapsed)
        for block in response.content:
            if block.type == "tool_use":
                return block.input
        return {}
    except Exception as e:
        logger.error("GoalAgent tool=%s error: %s", tool_name, e, exc_info=True)
        return {}


# ---------------------------------------------------------------------------
# DB helpers — context gathering for agent prompts
# ---------------------------------------------------------------------------

def _get_system_snapshot() -> dict:
    """Collect stats the hypothesis generator needs to understand the current state."""
    with get_cursor(commit=False) as cur:
        # Total contacts and segments
        cur.execute("""
            SELECT lifecycle_segment, count(*) as cnt
            FROM contacts
            GROUP BY lifecycle_segment
        """)
        segments = {r["lifecycle_segment"] or "unknown": r["cnt"] for r in cur.fetchall()}

        # Recent order stats (last 30 days)
        cur.execute("""
            SELECT
                count(DISTINCT o.id)                   AS orders_30d,
                count(DISTINCT o.contact_id)            AS ordering_customers_30d,
                round(avg(o.total_amount)::numeric, 2)  AS avg_order_value,
                (SELECT count(*) FROM contacts)         AS total_contacts
            FROM orders o
            WHERE o.order_date >= now() - interval '30 days'
        """)
        order_stats = dict(cur.fetchone() or {})

        # Baseline conversion rate (orders in last 7d / total reachable contacts)
        cur.execute("""
            SELECT
                count(DISTINCT contact_id) AS ordered_7d
            FROM orders
            WHERE order_date >= now() - interval '7 days'
        """)
        ordered_7d = (cur.fetchone() or {}).get("ordered_7d", 0)
        total_contacts = order_stats.get("total_contacts", 1)
        baseline_7d_rate = round(float(ordered_7d) / max(total_contacts, 1), 4)

        # What's been tried: active experiment types and statuses
        cur.execute("""
            SELECT experiment_type, status, count(*) as cnt
            FROM goal_experiments
            WHERE created_at >= now() - interval '30 days'
            GROUP BY experiment_type, status
        """)
        recent_experiments = [dict(r) for r in cur.fetchall()]

        # Proven signals discovered so far
        cur.execute("""
            SELECT signal_name, confidence, activation_count
            FROM discovered_signals
            WHERE is_active = TRUE
            ORDER BY confidence DESC
            LIMIT 10
        """)
        active_signals = [dict(r) for r in cur.fetchall()]

        # Contacts who haven't ordered in 14–90 days (lapsed pool size)
        cur.execute("""
            SELECT count(*) AS lapsed_count
            FROM contacts
            WHERE last_order_at IS NOT NULL
              AND last_order_at < now() - interval '14 days'
              AND last_order_at > now() - interval '90 days'
              AND (priority_override IS NULL OR priority_override != 'do_not_contact')
        """)
        lapsed_count = (cur.fetchone() or {}).get("lapsed_count", 0)

        # Contacts who have never ordered
        cur.execute("""
            SELECT count(*) AS never_ordered
            FROM contacts
            WHERE total_orders = 0
              AND (priority_override IS NULL OR priority_override != 'do_not_contact')
        """)
        never_ordered = (cur.fetchone() or {}).get("never_ordered", 0)

        # Top ordered items this month
        cur.execute("""
            SELECT oi.item_name, sum(oi.quantity) AS qty
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            WHERE o.order_date >= now() - interval '30 days'
            GROUP BY oi.item_name
            ORDER BY qty DESC
            LIMIT 5
        """)
        top_items = [{"item": r["item_name"], "qty": r["qty"]} for r in cur.fetchall()]

        # Day-of-week order distribution
        cur.execute("""
            SELECT to_char(order_date, 'Day') AS dow, count(*) AS cnt
            FROM orders
            WHERE order_date >= now() - interval '60 days'
            GROUP BY dow
            ORDER BY cnt DESC
        """)
        orders_by_dow = [{"day": r["dow"].strip(), "count": r["cnt"]} for r in cur.fetchall()]

    return {
        "contact_segments": segments,
        "order_stats_30d": order_stats,
        "baseline_7d_conversion_rate": baseline_7d_rate,
        "lapsed_contacts_14_90d": lapsed_count,
        "never_ordered_contacts": never_ordered,
        "top_menu_items_30d": top_items,
        "orders_by_day_of_week": orders_by_dow,
        "recent_experiment_summary": recent_experiments,
        "active_discovered_signals": active_signals,
    }


def _get_pending_experiments() -> list[dict]:
    with get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT id, hypothesis, experiment_type, cohort_description,
                   cohort_filter, message_template, success_threshold
            FROM goal_experiments
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT 3
        """)
        return [dict(r) for r in cur.fetchall()]


def _get_experiments_ready_to_measure() -> list[dict]:
    with get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT id, hypothesis, experiment_type, enrolled_count,
                   success_threshold, measurement_window_hours, started_at
            FROM goal_experiments
            WHERE status = 'running'
              AND measurement_due_at <= now()
            ORDER BY measurement_due_at ASC
        """)
        return [dict(r) for r in cur.fetchall()]


def _get_proven_experiments_without_signal() -> list[dict]:
    with get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT id, hypothesis, experiment_type, cohort_description,
                   cohort_sql, result_conversion_rate, result_sample_size,
                   conclusion_notes
            FROM goal_experiments
            WHERE conclusion = 'proven'
              AND generated_signal_id IS NULL
            ORDER BY concluded_at ASC
        """)
        return [dict(r) for r in cur.fetchall()]


def _get_recently_contacted_contact_ids() -> set:
    """Return contact IDs contacted (any channel) in the last 24 hours to avoid spam."""
    with get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT DISTINCT contact_id
            FROM action_queue
            WHERE created_at >= now() - interval '24 hours'
              AND action_type = 'send_sms'
        """)
        rows = cur.fetchall()
        return {r["contact_id"] for r in rows}


def _get_active_experiment_contact_ids() -> set:
    """Return contact IDs currently enrolled in a running experiment."""
    with get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT DISTINCT gec.contact_id
            FROM goal_experiment_contacts gec
            JOIN goal_experiments ge ON ge.id = gec.experiment_id
            WHERE ge.status = 'running'
        """)
        return {r["contact_id"] for r in cur.fetchall()}


# ---------------------------------------------------------------------------
# Phase 1: HYPOTHESIZE — Claude generates new experiment ideas
# ---------------------------------------------------------------------------

def _run_hypothesis_generator(client: anthropic.Anthropic, snapshot: dict) -> list[dict]:
    """Ask Claude to generate fresh experiment hypotheses based on the system snapshot."""
    system = (
        "You are the Growth Hypothesis Generator for DabbahWala, a food delivery business. "
        "Your sole job is to generate creative, testable experiment ideas that could increase orders. "
        "\n\n"
        "You think beyond existing signals. You ask: 'What have we NOT tried yet?' "
        "You look at the data for patterns — days of the week, lapsed customers, never-ordered contacts, "
        "menu items people love, segments that are engaged but not converting. "
        "\n\n"
        "Each experiment must be:\n"
        "  1. SPECIFIC — target a well-defined cohort (e.g. 'contacts who ordered 2+ times but not in 21+ days')\n"
        "  2. ACTIONABLE — send them an SMS with a concrete message angle\n"
        "  3. MEASURABLE — we track orders placed within 72 hours\n"
        "  4. NOVEL — do NOT duplicate existing active signals shown in the snapshot\n"
        "\n"
        "Experiment types you can propose:\n"
        "  - cohort_message: target a specific customer segment with a tailored message angle\n"
        "  - timing_test: contact a cohort at a specific time/day pattern\n"
        "  - offer_test: test a specific discount/promo angle vs control\n"
        "  - reactivation: reach never-ordered leads with a first-time incentive\n"
        "  - loyalty_nudge: nudge customers close to a 'regular' threshold\n"
        "\n"
        "The message_template must be SMS-ready (under 160 characters). "
        "Use {first_name} as the only variable. Be warm, specific, and not pushy."
    )

    user = (
        f"Here is the current system snapshot:\n{json.dumps(snapshot, indent=2, default=str)}\n\n"
        f"Generate {MAX_EXPERIMENTS_PER_RUN} new experiment hypotheses that have NOT been tried yet. "
        "Focus on ideas that could uncover new patterns. Avoid any experiment type already represented "
        "in recent_experiment_summary with status=running or pending."
    )

    tool = {
        "name": "generate_hypotheses",
        "description": "Generate a list of experiment hypotheses to test",
        "input_schema": {
            "type": "object",
            "properties": {
                "hypotheses": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "hypothesis": {"type": "string", "description": "Clear statement of what we're testing"},
                            "experiment_type": {
                                "type": "string",
                                "enum": ["cohort_message", "timing_test", "offer_test", "reactivation", "loyalty_nudge"]
                            },
                            "cohort_description": {
                                "type": "string",
                                "description": "Plain-English description of which contacts to target"
                            },
                            "cohort_filter": {
                                "type": "object",
                                "description": "Structured filter with keys like: min_orders, max_orders, days_since_last_order_min, days_since_last_order_max, lifecycle_segment, never_ordered"
                            },
                            "message_template": {
                                "type": "string",
                                "description": "SMS message template under 160 chars. Use {first_name} variable."
                            },
                            "success_threshold": {
                                "type": "number",
                                "description": "Minimum conversion rate (0.0-1.0) to consider proven (e.g. 0.10 = 10%)"
                            },
                            "rationale": {
                                "type": "string",
                                "description": "Why you believe this experiment might work, based on the data"
                            }
                        },
                        "required": ["hypothesis", "experiment_type", "cohort_description",
                                     "cohort_filter", "message_template", "success_threshold", "rationale"]
                    },
                    "minItems": 1,
                    "maxItems": 5
                },
                "overall_reasoning": {
                    "type": "string",
                    "description": "Brief explanation of the strategy behind these experiments"
                }
            },
            "required": ["hypotheses", "overall_reasoning"]
        }
    }

    result = _tool_call(client, system, user, tool)
    hypotheses = result.get("hypotheses", [])
    logger.info("GoalAgent HYPOTHESIZE: generated %d hypotheses", len(hypotheses))
    return hypotheses


def _hypothesis_hash(h: dict) -> str:
    """Return a 16-char hex fingerprint of experiment_type + hypothesis text.

    Used as a dedup key so the same idea is never inserted twice, even across
    multiple daily runs of the hypothesis generator.
    """
    key = f"{h.get('experiment_type', '')}|{h.get('hypothesis', '').lower().strip()}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _save_experiments(hypotheses: list[dict]) -> list[int]:
    """Persist hypothesis list to goal_experiments, skipping duplicates by hash."""
    ids = []
    for h in hypotheses:
        h_hash = _hypothesis_hash(h)
        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO goal_experiments
                    (hypothesis, experiment_type, cohort_description, cohort_filter,
                     message_template, success_threshold, status, hypothesis_hash)
                VALUES (%s, %s, %s, %s::jsonb, %s, %s, 'pending', %s)
                ON CONFLICT (hypothesis_hash) DO NOTHING
                RETURNING id
                """,
                (
                    h.get("hypothesis", ""),
                    h.get("experiment_type", "cohort_message"),
                    h.get("cohort_description", ""),
                    json.dumps(h.get("cohort_filter", {})),
                    h.get("message_template", ""),
                    h.get("success_threshold", 0.10),
                    h_hash,
                ),
            )
            row = cur.fetchone()
            if row:
                ids.append(row["id"])
            else:
                logger.info(
                    "GoalAgent HYPOTHESIZE: skipped duplicate hypothesis hash=%s type=%s",
                    h_hash, h.get("experiment_type"),
                )
    return ids


# ---------------------------------------------------------------------------
# Phase 2: EXPERIMENT — build cohort SQL, enqueue actions
# ---------------------------------------------------------------------------

def _run_cohort_builder(
    client: anthropic.Anthropic,
    experiment: dict,
) -> dict:
    """Ask Claude to translate the cohort_filter into safe SQL and refine the message."""
    system = (
        "You are a SQL expert for the DabbahWala PostgreSQL database. "
        "Your job is to translate a contact filter description into a safe, read-only SQL query "
        "that returns contact_id, first_name, phone from the contacts table. "
        "\n\n"
        "Schema (relevant parts):\n"
        "  contacts(id, first_name, last_name, phone, email, lifecycle_segment, total_orders,\n"
        "           last_order_at, created_at, priority_override, sms_level)\n"
        "  orders(id, contact_id, order_date, total_amount)\n"
        "\n"
        "RULES:\n"
        "  - Always exclude contacts where priority_override = 'do_not_contact'\n"
        "  - Always exclude contacts where phone IS NULL\n"
        "  - Always exclude contacts where lifecycle_segment IN ('optout', 'churned')\n"
        "  - Add LIMIT 20 to keep cohort sizes testable\n"
        "  - Return only: contact_id, first_name, phone\n"
        "  - Use parameterless SQL only (no %s placeholders)\n"
        "  - Use only SELECT — no INSERT/UPDATE/DELETE\n"
    )

    user = (
        f"Experiment hypothesis: {experiment['hypothesis']}\n\n"
        f"Cohort description: {experiment['cohort_description']}\n\n"
        f"Structured filter: {json.dumps(experiment.get('cohort_filter', {}), indent=2)}\n\n"
        "Write a SQL query that selects the target cohort, staying within the rules above."
    )

    tool = {
        "name": "build_cohort",
        "description": "Return the SQL query and a refined message template",
        "input_schema": {
            "type": "object",
            "properties": {
                "cohort_sql": {
                    "type": "string",
                    "description": "Complete read-only SQL query returning contact_id, first_name, phone"
                },
                "message_template": {
                    "type": "string",
                    "description": "Final SMS template (160 chars max, {first_name} variable)"
                },
                "cohort_logic_explanation": {
                    "type": "string",
                    "description": "Plain-English explanation of what the SQL does"
                }
            },
            "required": ["cohort_sql", "message_template", "cohort_logic_explanation"]
        }
    }

    return _tool_call(client, system, user, tool)


def _execute_cohort_sql(cohort_sql: str) -> list[dict]:
    """Run the cohort SQL (read-only) and return the matching contacts."""
    # Safety gate: block any mutation statements
    sql_upper = cohort_sql.upper().strip()
    forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER", "CREATE", "GRANT"]
    for kw in forbidden:
        if kw in sql_upper:
            logger.warning("GoalAgent blocked unsafe SQL (contains %s)", kw)
            return []

    try:
        with get_cursor(commit=False) as cur:
            cur.execute(cohort_sql)
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error("GoalAgent cohort SQL error: %s | SQL: %.200s", e, cohort_sql)
        return []


def _enqueue_experiment_actions(
    experiment_id: int,
    contacts: list[dict],
    message_template: str,
) -> int:
    """Insert action_queue rows for each contact and enroll them in the experiment."""
    enrolled = 0
    for c in contacts:
        contact_id = c.get("contact_id") or c.get("id")
        first_name = c.get("first_name") or "there"
        phone = c.get("phone", "")
        if not phone:
            continue

        message_body = message_template.replace("{first_name}", first_name)

        try:
            with get_cursor() as cur:
                # Insert into action_queue
                cur.execute(
                    """
                    INSERT INTO action_queue (contact_id, action_type, payload)
                    VALUES (%s, 'send_sms', %s::jsonb)
                    RETURNING id
                    """,
                    (
                        contact_id,
                        json.dumps({
                            "phone": phone,
                            "message_body": message_body,
                            "experiment_id": experiment_id,
                            "source": "goal_agent",
                        }),
                    ),
                )
                action_queue_id = cur.fetchone()["id"]

                # Enroll in experiment
                cur.execute(
                    """
                    INSERT INTO goal_experiment_contacts
                        (experiment_id, contact_id, action_queue_id, message_sent)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (experiment_id, contact_id) DO NOTHING
                    """,
                    (experiment_id, contact_id, action_queue_id, message_body),
                )
            enrolled += 1
        except Exception as e:
            logger.warning("GoalAgent enqueue error contact_id=%s: %s", contact_id, e)

    return enrolled


def _start_experiment(experiment_id: int, cohort_sql: str, enrolled_count: int) -> None:
    with get_cursor() as cur:
        cur.execute(
            """
            UPDATE goal_experiments
            SET status = 'running',
                cohort_sql = %s,
                enrolled_count = %s,
                started_at = now(),
                measurement_due_at = now() + (%s * interval '1 hour'),
                updated_at = now()
            WHERE id = %s
            """,
            (cohort_sql, enrolled_count, MEASUREMENT_WINDOW_HOURS, experiment_id),
        )


# ---------------------------------------------------------------------------
# Phase 3: MEASURE — check conversions and conclude experiments
# ---------------------------------------------------------------------------

def _count_experiment_conversions(experiment_id: int) -> dict:
    """Count how many enrolled contacts placed an order after enrollment."""
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT
                count(*)                                          AS total_enrolled,
                count(*) FILTER (WHERE converted = TRUE)         AS converted,
                count(*) FILTER (WHERE converted IS NULL)        AS not_yet_checked
            FROM goal_experiment_contacts
            WHERE experiment_id = %s
            """,
            (experiment_id,),
        )
        return dict(cur.fetchone())


def _check_and_mark_conversions(experiment_id: int) -> int:
    """
    For each enrolled contact, check if they placed an order after enrollment.
    Mark converted=TRUE/FALSE and return count of new conversions found.
    """
    new_conversions = 0
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT gec.id, gec.contact_id, gec.enrolled_at
            FROM goal_experiment_contacts gec
            WHERE gec.experiment_id = %s
              AND gec.converted IS NULL
            """,
            (experiment_id,),
        )
        unchecked = cur.fetchall()

    for row in unchecked:
        enrolled_at = row["enrolled_at"]
        contact_id = row["contact_id"]
        with get_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT id FROM orders
                WHERE contact_id = %s
                  AND order_date >= %s::date
                LIMIT 1
                """,
                (contact_id, enrolled_at),
            )
            ordered = cur.fetchone() is not None

        with get_cursor() as cur:
            cur.execute(
                """
                UPDATE goal_experiment_contacts
                SET converted = %s,
                    conversion_at = CASE WHEN %s THEN now() ELSE NULL END,
                    outcome_checked_at = now()
                WHERE id = %s
                """,
                (ordered, ordered, row["id"]),
            )
        if ordered:
            new_conversions += 1

    return new_conversions


def _run_conclusion_agent(
    client: anthropic.Anthropic,
    experiment: dict,
    conversion_stats: dict,
    baseline_rate: float,
) -> dict:
    system = (
        "You are the Experiment Conclusion Agent for DabbahWala. "
        "You receive an experiment's results and decide whether the hypothesis is proven, disproven, or inconclusive. "
        "\n\n"
        "Rules for conclusion:\n"
        "  - 'proven': conversion_rate >= success_threshold AND sample_size >= 5\n"
        "  - 'disproven': conversion_rate < (baseline_rate * 0.5) AND sample_size >= 5 "
        "(i.e., performing significantly below baseline)\n"
        "  - 'inconclusive': sample too small OR rate falls between baseline and threshold\n"
        "\n"
        "Your notes should be actionable — what should be done with this result?"
    )

    user = (
        f"Experiment: {experiment['hypothesis']}\n"
        f"Type: {experiment['experiment_type']}\n"
        f"Success threshold: {experiment['success_threshold']}\n\n"
        f"Results:\n"
        f"  Enrolled: {conversion_stats['total_enrolled']}\n"
        f"  Converted (ordered within window): {conversion_stats['converted']}\n"
        f"  Conversion rate: {round(conversion_stats['converted'] / max(conversion_stats['total_enrolled'], 1), 4)}\n"
        f"  Baseline 7-day conversion rate (system-wide): {baseline_rate}\n\n"
        "Conclude this experiment."
    )

    tool = {
        "name": "conclude_experiment",
        "description": "Return the experiment conclusion",
        "input_schema": {
            "type": "object",
            "properties": {
                "conclusion": {
                    "type": "string",
                    "enum": ["proven", "disproven", "inconclusive"]
                },
                "conclusion_notes": {
                    "type": "string",
                    "description": "Actionable interpretation of the result"
                }
            },
            "required": ["conclusion", "conclusion_notes"]
        }
    }

    return _tool_call(client, system, user, tool)


def _conclude_experiment(experiment_id: int, conclusion: str, notes: str, conversions: int, sample: int) -> None:
    rate = round(conversions / max(sample, 1), 4)
    with get_cursor() as cur:
        cur.execute(
            """
            UPDATE goal_experiments
            SET status = 'concluded',
                conclusion = %s,
                conclusion_notes = %s,
                result_conversion_rate = %s,
                result_success_count = %s,
                result_sample_size = %s,
                concluded_at = now(),
                updated_at = now()
            WHERE id = %s
            """,
            (conclusion, notes, rate, conversions, sample, experiment_id),
        )


# ---------------------------------------------------------------------------
# Phase 4: HARVEST — proven experiments → discovered_signals
# ---------------------------------------------------------------------------

def _run_signal_architect(
    client: anthropic.Anthropic,
    experiment: dict,
) -> dict:
    """Ask Claude to define a reusable SQL-based signal from a proven experiment."""
    system = (
        "You are the Signal Architect for DabbahWala. "
        "A contact targeting experiment has been proven — its message angle worked above the success threshold. "
        "Your job is to codify this experiment as a reusable signal: a named SQL query that can identify "
        "future contacts matching the same pattern. "
        "\n\n"
        "Schema (relevant):\n"
        "  contacts(id, first_name, phone, lifecycle_segment, total_orders, last_order_at, created_at, priority_override)\n"
        "  orders(id, contact_id, order_date, total_amount)\n"
        "\n"
        "Rules for the detection SQL:\n"
        "  - Must return: contact_id (from contacts.id)\n"
        "  - Must exclude do_not_contact and optout/churned contacts\n"
        "  - Must be parameterless and read-only\n"
        "  - Should be reusable over time (use relative dates like 'now() - interval ...')\n"
    )

    user = (
        f"Proven experiment:\n"
        f"  Hypothesis: {experiment['hypothesis']}\n"
        f"  Type: {experiment['experiment_type']}\n"
        f"  Cohort: {experiment['cohort_description']}\n"
        f"  Original SQL: {experiment.get('cohort_sql', 'not available')}\n"
        f"  Conversion rate: {experiment['result_conversion_rate']}\n"
        f"  Notes: {experiment.get('conclusion_notes', '')}\n\n"
        "Define a reusable named signal for the intelligence engine."
    )

    tool = {
        "name": "architect_signal",
        "description": "Define a reusable signal from a proven experiment",
        "input_schema": {
            "type": "object",
            "properties": {
                "signal_name": {
                    "type": "string",
                    "description": "Short snake_case name like 'lapsed_2x_orderer_3_weeks' (max 80 chars)"
                },
                "signal_description": {
                    "type": "string",
                    "description": "Human-readable description of what this signal detects"
                },
                "detection_sql": {
                    "type": "string",
                    "description": "SQL query returning contact_id for contacts currently matching this signal"
                }
            },
            "required": ["signal_name", "signal_description", "detection_sql"]
        }
    }

    return _tool_call(client, system, user, tool)


def _save_discovered_signal(experiment_id: int, signal: dict, confidence: float) -> Optional[int]:
    """Persist a discovered signal and link it back to the source experiment."""
    # Safety gate: block mutation SQL in the detection query
    sql_upper = (signal.get("detection_sql") or "").upper()
    for kw in ["INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER"]:
        if kw in sql_upper:
            logger.warning("GoalAgent blocked unsafe signal SQL for experiment_id=%s", experiment_id)
            return None

    try:
        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO discovered_signals
                    (signal_name, signal_description, source_experiment_id, detection_sql, confidence)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (signal_name) DO UPDATE
                    SET signal_description = EXCLUDED.signal_description,
                        detection_sql = EXCLUDED.detection_sql,
                        confidence = EXCLUDED.confidence,
                        updated_at = now()
                RETURNING id
                """,
                (
                    signal.get("signal_name", f"experiment_{experiment_id}"),
                    signal.get("signal_description", ""),
                    experiment_id,
                    signal.get("detection_sql", ""),
                    confidence,
                ),
            )
            signal_id = cur.fetchone()["id"]
            # Link back
            cur.execute(
                "UPDATE goal_experiments SET generated_signal_id = %s WHERE id = %s",
                (signal_id, experiment_id),
            )
            return signal_id
    except Exception as e:
        logger.error("GoalAgent save_signal error for experiment_id=%s: %s", experiment_id, e)
        return None


# ---------------------------------------------------------------------------
# Phase runners — called from the endpoint
# ---------------------------------------------------------------------------

def _phase_hypothesize(client: anthropic.Anthropic) -> dict:
    """Generate new experiment ideas and persist them."""
    snapshot = _get_system_snapshot()
    hypotheses = _run_hypothesis_generator(client, snapshot)
    if not hypotheses:
        return {"experiments_created": 0, "reasoning": "Claude returned no hypotheses"}
    ids = _save_experiments(hypotheses)
    logger.info("GoalAgent HYPOTHESIZE: created experiment IDs %s", ids)
    return {
        "experiments_created": len(ids),
        "experiment_ids": ids,
        "snapshot_summary": {
            "total_contacts": snapshot["order_stats_30d"].get("total_contacts", 0),
            "baseline_7d_rate": snapshot["baseline_7d_conversion_rate"],
            "lapsed_contacts": snapshot["lapsed_contacts_14_90d"],
        }
    }


def _phase_experiment(client: anthropic.Anthropic) -> dict:
    """Take pending experiments, build cohorts, enqueue actions."""
    recently_contacted = _get_recently_contacted_contact_ids()
    active_experiment_contacts = _get_active_experiment_contact_ids()
    blocked_ids = recently_contacted | active_experiment_contacts

    pending = _get_pending_experiments()
    if not pending:
        return {"experiments_started": 0, "contacts_enrolled": 0}

    started = 0
    total_enrolled = 0

    for exp in pending:
        exp_id = exp["id"]
        logger.info("GoalAgent EXPERIMENT: building cohort for experiment_id=%s", exp_id)

        # Get Claude to write the SQL
        cohort_result = _run_cohort_builder(client, exp)
        cohort_sql = cohort_result.get("cohort_sql", "")
        message_template = cohort_result.get("message_template") or exp.get("message_template", "")

        if not cohort_sql or not message_template:
            logger.warning("GoalAgent: no cohort SQL returned for experiment_id=%s", exp_id)
            continue

        # Execute cohort SQL
        contacts = _execute_cohort_sql(cohort_sql)

        # Filter out blocked contacts
        contacts = [
            c for c in contacts
            if (c.get("contact_id") or c.get("id")) not in blocked_ids
        ]

        # Cap cohort size
        contacts = contacts[:MAX_COHORT_SIZE]

        if not contacts:
            logger.info("GoalAgent: empty cohort for experiment_id=%s after filtering", exp_id)
            continue

        # Enqueue actions
        enrolled = _enqueue_experiment_actions(exp_id, contacts, message_template)

        # Mark experiment as running
        _start_experiment(exp_id, cohort_sql, enrolled)

        started += 1
        total_enrolled += enrolled
        logger.info("GoalAgent EXPERIMENT: started experiment_id=%s enrolled=%d", exp_id, enrolled)

    return {"experiments_started": started, "contacts_enrolled": total_enrolled}


def _phase_measure(client: anthropic.Anthropic) -> dict:
    """Check conversions for running experiments past their measurement window."""
    ready = _get_experiments_ready_to_measure()
    if not ready:
        return {"experiments_concluded": 0, "orders_attributed": 0}

    snapshot = _get_system_snapshot()
    baseline_rate = snapshot["baseline_7d_conversion_rate"]

    concluded = 0
    orders_attributed = 0

    for exp in ready:
        exp_id = exp["id"]
        logger.info("GoalAgent MEASURE: checking conversions for experiment_id=%s", exp_id)

        # Mark individual contacts as converted or not
        new_conversions = _check_and_mark_conversions(exp_id)

        # Get final stats
        stats = _count_experiment_conversions(exp_id)

        # Ask Claude to conclude
        conclusion_result = _run_conclusion_agent(client, exp, stats, baseline_rate)
        conclusion = conclusion_result.get("conclusion", "inconclusive")
        notes = conclusion_result.get("conclusion_notes", "")

        _conclude_experiment(
            exp_id,
            conclusion,
            notes,
            stats["converted"],
            stats["total_enrolled"],
        )

        concluded += 1
        orders_attributed += stats["converted"]
        logger.info(
            "GoalAgent MEASURE: experiment_id=%s conclusion=%s conversions=%d/%d",
            exp_id, conclusion, stats["converted"], stats["total_enrolled"]
        )

    return {"experiments_concluded": concluded, "orders_attributed": orders_attributed}


def _phase_harvest(client: anthropic.Anthropic) -> dict:
    """Turn proven experiments into reusable discovered signals."""
    proven = _get_proven_experiments_without_signal()
    if not proven:
        return {"signals_discovered": 0}

    discovered = 0

    for exp in proven:
        exp_id = exp["id"]
        logger.info("GoalAgent HARVEST: architecting signal for experiment_id=%s", exp_id)

        signal = _run_signal_architect(client, exp)
        if not signal:
            continue

        signal_id = _save_discovered_signal(exp_id, signal, exp.get("result_conversion_rate", 0.0))
        if signal_id:
            discovered += 1
            logger.info(
                "GoalAgent HARVEST: signal '%s' (id=%s) created from experiment_id=%s",
                signal.get("signal_name"), signal_id, exp_id
            )

    return {"signals_discovered": discovered}


def _log_run(run_type: str, result: dict, error: Optional[str] = None) -> None:
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO goal_agent_runs
                (run_type, experiments_created, experiments_started, contacts_enrolled,
                 experiments_concluded, signals_discovered, orders_attributed, reasoning, error_detail)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                run_type,
                result.get("experiments_created", 0),
                result.get("experiments_started", 0),
                result.get("contacts_enrolled", 0),
                result.get("experiments_concluded", 0),
                result.get("signals_discovered", 0),
                result.get("orders_attributed", 0),
                result.get("reasoning", ""),
                error,
            ),
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/run", response_model=GoalAgentResult)
def run_full_cycle():
    """
    Run all four phases in sequence:
      1. Hypothesize (generate new experiments)
      2. Experiment (build cohorts, enqueue actions)
      3. Measure (check conversions on mature experiments)
      4. Harvest (convert proven experiments to signals)
    """
    client = _claude()
    ts = datetime.now(timezone.utc).isoformat()
    agg = {
        "experiments_created": 0,
        "experiments_started": 0,
        "contacts_enrolled": 0,
        "experiments_concluded": 0,
        "signals_discovered": 0,
        "orders_attributed": 0,
    }
    details = {}

    for phase_name, phase_fn in [
        ("hypothesize", _phase_hypothesize),
        ("experiment", _phase_experiment),
        ("measure", _phase_measure),
        ("harvest", _phase_harvest),
    ]:
        try:
            result = phase_fn(client)
            details[phase_name] = result
            for k in agg:
                agg[k] += result.get(k, 0)
            logger.info("GoalAgent phase=%s result=%s", phase_name, result)
        except Exception as e:
            logger.error("GoalAgent phase=%s failed: %s", phase_name, e, exc_info=True)
            details[phase_name] = {"error": str(e)}

    _log_run("full", agg)

    return GoalAgentResult(
        timestamp=ts,
        phase="full",
        **agg,
        details=details,
    )


@router.post("/hypothesize", response_model=GoalAgentResult)
def run_hypothesize_only():
    """Generate new experiment hypotheses without running or measuring anything."""
    client = _claude()
    ts = datetime.now(timezone.utc).isoformat()
    result = _phase_hypothesize(client)
    _log_run("hypothesize", result)
    return GoalAgentResult(timestamp=ts, phase="hypothesize", **result)


@router.post("/experiment", response_model=GoalAgentResult)
def run_experiment_only():
    """Launch pending experiments: build cohorts and enqueue SMS actions."""
    client = _claude()
    ts = datetime.now(timezone.utc).isoformat()
    result = _phase_experiment(client)
    _log_run("experiment", result)
    return GoalAgentResult(timestamp=ts, phase="experiment", **result)


@router.post("/measure", response_model=GoalAgentResult)
def run_measure_only():
    """Measure conversion outcomes for experiments past their measurement window."""
    client = _claude()
    ts = datetime.now(timezone.utc).isoformat()
    result = _phase_measure(client)
    _log_run("measure", result)
    return GoalAgentResult(timestamp=ts, phase="measure", **result)


@router.post("/harvest", response_model=GoalAgentResult)
def run_harvest_only():
    """Convert proven experiments into reusable discovered signals."""
    client = _claude()
    ts = datetime.now(timezone.utc).isoformat()
    result = _phase_harvest(client)
    _log_run("harvest", result)
    return GoalAgentResult(timestamp=ts, phase="harvest", **result)


@router.get("/experiments")
def list_experiments(status: Optional[str] = None, limit: int = 20):
    """List goal experiments, optionally filtered by status."""
    with get_cursor(commit=False) as cur:
        if status:
            cur.execute(
                """
                SELECT id, hypothesis, experiment_type, status,
                       cohort_description, enrolled_count,
                       result_conversion_rate, conclusion,
                       started_at, concluded_at, created_at
                FROM goal_experiments
                WHERE status = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (status, limit),
            )
        else:
            cur.execute(
                """
                SELECT id, hypothesis, experiment_type, status,
                       cohort_description, enrolled_count,
                       result_conversion_rate, conclusion,
                       started_at, concluded_at, created_at
                FROM goal_experiments
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
        rows = [dict(r) for r in cur.fetchall()]
    return {"experiments": rows, "count": len(rows)}


@router.get("/signals")
def list_signals():
    """List all discovered signals."""
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT id, signal_name, signal_description,
                   confidence, activation_count, is_active,
                   created_at
            FROM discovered_signals
            ORDER BY confidence DESC
            """
        )
        rows = [dict(r) for r in cur.fetchall()]
    return {"signals": rows, "count": len(rows)}


@router.get("/runs")
def list_runs(limit: int = 20):
    """List recent goal agent run logs."""
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT id, run_type, experiments_created, experiments_started,
                   contacts_enrolled, experiments_concluded, signals_discovered,
                   orders_attributed, created_at
            FROM goal_agent_runs
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    return {"runs": rows, "count": len(rows)}
