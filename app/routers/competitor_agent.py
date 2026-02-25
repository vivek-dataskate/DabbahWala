"""
Competitor Research Agent — DabbahWala

Runs weekly (Monday 6:30 AM, before goal agent at 9 AM) to research competitor
marketing tactics and auto-inject new experiment hypotheses into the goal_experiments
queue. Goal: drive 100% repeat order rate across all four customer segments.

Three-phase cycle:
  1. RESEARCH     — Parse .eml samples from data/cookunitysamples/ +
                    scrape competitor websites live +
                    apply Claude's knowledge of food-subscription best practices
  2. GAP ANALYSIS — Compare competitor angles to what DabbahWala has already tested
  3. INJECT       — Generate and auto-insert hypotheses into goal_experiments (pending)

The existing goal agent (daily 9 AM) picks up pending experiments and runs them.
No new execution infrastructure needed.

Competitor sources:
  - data/cookunitysamples/*.eml  (add new files weekly — auto-detected)
  - cookunity.com, hellofresh.com, factor75.com, freshly.com, sunbasket.com
  - Claude's training knowledge of food subscription retention psychology
"""

import email as _email_module
import glob
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

import anthropic
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_cursor

logger = logging.getLogger(__name__)

router = APIRouter()

MODEL = "claude-sonnet-4-5-20250929"

# Path to competitor email samples (relative to this file → project root)
SAMPLE_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "cookunitysamples")
)

COMPETITOR_URLS = [
    {"name": "cookunity",  "url": "https://www.cookunity.com"},
    {"name": "hellofresh", "url": "https://www.hellofresh.com"},
    {"name": "factor",     "url": "https://www.factor75.com"},
    {"name": "freshly",    "url": "https://www.freshly.com"},
    {"name": "sunbasket",  "url": "https://www.sunbasket.com"},
]

HYPOTHESES_PER_RUN = 8   # total new experiments to queue each week


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------

class CompetitorAgentResult(BaseModel):
    timestamp: str
    email_samples_parsed: int = 0
    websites_scraped: int = 0
    sources_processed: int = 0
    hypotheses_queued: int = 0
    details: dict = {}


# ---------------------------------------------------------------------------
# Anthropic client + shared tool-call helper (mirrors goal_agent.py pattern)
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
            max_tokens=4096,
            system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": user}],
        )
        elapsed = time.time() - t0
        logger.debug("CompetitorAgent tool=%s %.2fs", tool_name, elapsed)
        for block in response.content:
            if block.type == "tool_use":
                return block.input
        return {}
    except Exception as e:
        logger.error("CompetitorAgent tool=%s error: %s", tool_name, e, exc_info=True)
        return {}


# ---------------------------------------------------------------------------
# HTML stripping helper (no extra dependency — avoids beautifulsoup requirement)
# ---------------------------------------------------------------------------

def _strip_html(html: str) -> str:
    """Remove script/style blocks and HTML tags, normalize whitespace."""
    text = re.sub(r'(?s)<(script|style)[^>]*>.*?</\1>', ' ', html)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&[a-zA-Z#0-9]+;', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


# ---------------------------------------------------------------------------
# Phase 1a: Parse competitor email samples from data/cookunitysamples/
# ---------------------------------------------------------------------------

def _parse_email_samples() -> list[dict]:
    """Parse all .eml files from the samples directory — auto-detects new files."""
    results = []
    sample_dir = os.path.abspath(SAMPLE_DIR)
    if not os.path.isdir(sample_dir):
        logger.info(
            "CompetitorAgent: sample dir not found at %s — skipping email parsing",
            sample_dir,
        )
        return results

    eml_paths = sorted(glob.glob(os.path.join(sample_dir, "*.eml")))
    for eml_path in eml_paths:
        try:
            with open(eml_path, "rb") as f:
                msg = _email_module.message_from_bytes(f.read())

            subject  = msg.get("Subject", "").strip()
            from_hdr = msg.get("From", "").strip()
            date_str = msg.get("Date", "").strip()

            # Extract readable body (prefer plain text, fall back to HTML)
            body_text = ""
            if msg.is_multipart():
                for part in msg.walk():
                    ct = part.get_content_type()
                    if ct == "text/plain":
                        raw = part.get_payload(decode=True)
                        if raw:
                            body_text = raw.decode("utf-8", errors="ignore")
                            break
                    elif ct == "text/html" and not body_text:
                        raw = part.get_payload(decode=True)
                        if raw:
                            body_text = _strip_html(raw.decode("utf-8", errors="ignore"))
            else:
                raw = msg.get_payload(decode=True)
                if raw:
                    body_text = raw.decode("utf-8", errors="ignore")

            results.append({
                "source":       "email_sample",
                "competitor":   "cookunity",
                "subject":      subject,
                "from":         from_hdr,
                "date":         date_str,
                "body_preview": body_text[:600].strip(),
                "filename":     os.path.basename(eml_path),
            })
        except Exception as e:
            logger.warning("CompetitorAgent: failed to parse %s: %s", eml_path, e)

    logger.info("CompetitorAgent: parsed %d email samples from %s", len(results), sample_dir)
    return results


# ---------------------------------------------------------------------------
# Phase 1b: Scrape competitor websites
# ---------------------------------------------------------------------------

def _scrape_competitor_websites() -> list[dict]:
    """Fetch competitor homepages and extract readable text content."""
    results = []
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    for comp in COMPETITOR_URLS:
        try:
            resp = httpx.get(
                comp["url"],
                headers=headers,
                follow_redirects=True,
                timeout=15.0,
            )
            if resp.status_code == 200:
                text = _strip_html(resp.text)[:2500]
                results.append({
                    "source":          "website",
                    "competitor":      comp["name"],
                    "url":             comp["url"],
                    "content_preview": text,
                })
                logger.info(
                    "CompetitorAgent: scraped %s (%d chars)",
                    comp["name"], len(text),
                )
            else:
                logger.warning(
                    "CompetitorAgent: %s returned HTTP %d",
                    comp["url"], resp.status_code,
                )
        except Exception as e:
            logger.warning("CompetitorAgent: failed to scrape %s: %s", comp["url"], e)

    return results


# ---------------------------------------------------------------------------
# Phase 2: Gap analysis — what has DabbahWala already tried?
# ---------------------------------------------------------------------------

def _get_existing_experiments() -> list[dict]:
    with get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT hypothesis, experiment_type, status, conclusion,
                   result_conversion_rate, cohort_description
            FROM goal_experiments
            WHERE created_at >= now() - interval '60 days'
            ORDER BY created_at DESC
            LIMIT 30
        """)
        return [dict(r) for r in cur.fetchall()]


def _get_proven_signals() -> list[dict]:
    with get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT signal_name, signal_description, confidence
            FROM discovered_signals
            WHERE is_active = TRUE
            ORDER BY confidence DESC
        """)
        return [dict(r) for r in cur.fetchall()]


def _get_customer_segments() -> dict:
    """Pool sizes for the four retention segments we must target every week."""
    with get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT
                count(*) FILTER (WHERE total_orders = 0)
                    AS never_ordered,
                count(*) FILTER (WHERE total_orders = 1
                    AND last_order_at < now() - interval '14 days')
                    AS one_and_done,
                count(*) FILTER (WHERE total_orders BETWEEN 2 AND 5
                    AND last_order_at < now() - interval '21 days')
                    AS lapsing_regular,
                count(*) FILTER (WHERE total_orders > 5
                    AND last_order_at < now() - interval '30 days')
                    AS high_value_at_risk,
                count(*) FILTER (WHERE total_orders >= 2
                    AND last_order_at >= now() - interval '21 days')
                    AS active_repeaters
            FROM contacts
            WHERE (priority_override IS NULL OR priority_override != 'do_not_contact')
              AND lifecycle_segment NOT IN ('optout', 'churned')
        """)
        row = cur.fetchone()
        return dict(row) if row else {}


# ---------------------------------------------------------------------------
# Phase 3: Generate hypotheses via Claude
# ---------------------------------------------------------------------------

def _run_competitor_hypothesis_generator(
    client: anthropic.Anthropic,
    email_samples: list[dict],
    website_data: list[dict],
    existing_experiments: list[dict],
    proven_signals: list[dict],
    customer_segments: dict,
) -> tuple[list[dict], str]:
    """
    Claude analyzes competitor tactics + food-subscription psychology and returns
    novel hypotheses covering all four customer segments.
    """
    system = (
        "You are the Competitor Intelligence & Growth Hypothesis Generator for DabbahWala, "
        "an Indian home-cooked meal delivery business. "
        "Your mission: achieve 100% repeat order rate — every customer should reorder. "
        "\n\n"
        "You have three intelligence sources:\n"
        "  1. Competitor marketing emails (CookUnity) — subject lines, offer structures, hooks\n"
        "  2. Competitor website copy — current promotions, value propositions, pricing angles\n"
        "  3. Your deep knowledge of food subscription retention psychology and industry best practices\n"
        "\n"
        "The four customer segments you MUST cover every week:\n"
        "  A. never_ordered       — signed up but never placed a first order\n"
        "  B. one_and_done        — ordered exactly once, never returned (14+ days)\n"
        "  C. lapsing_regular     — ordered 2-5x but silent 21+ days\n"
        "  D. high_value_at_risk  — ordered 5+ times but silent 30+ days\n"
        "\n"
        "For each hypothesis:\n"
        "  - Adapt competitor tactics to DabbahWala's Indian home-cooked, authentic positioning\n"
        "  - Draw on food-subscription psychology: habit loops, social proof, nostalgia, convenience,\n"
        "    guilt-free indulgence, identity ('you're the kind of person who...'), scarcity,\n"
        "    reciprocity, curiosity gaps\n"
        "  - SMS messages must be under 160 characters and use {first_name}\n"
        "  - Be warm and personal — never sound like mass marketing\n"
        "  - PRIORITISE NOVELTY — never duplicate what's in 'Already Tried'\n"
        "  - At least 2 hypotheses per segment across the batch\n"
    )

    # Compact email summary for prompt efficiency
    email_summary = [
        {"subject": e.get("subject", ""), "body_preview": e.get("body_preview", "")[:250]}
        for e in email_samples[:15]
    ]

    # Compact website summary
    website_summary = [
        {"competitor": w.get("competitor", ""), "content": w.get("content_preview", "")[:400]}
        for w in website_data
    ]

    user = (
        f"## Competitor Email Campaigns (all samples on file):\n"
        f"{json.dumps(email_summary, indent=2)}\n\n"
        f"## Competitor Website Copy (live scraped today):\n"
        f"{json.dumps(website_summary, indent=2)}\n\n"
        f"## DabbahWala Customer Segments (current pool sizes):\n"
        f"{json.dumps(customer_segments, indent=2)}\n\n"
        f"## Already Tried (last 60 days — DO NOT duplicate):\n"
        f"{json.dumps(existing_experiments, indent=2)}\n\n"
        f"## Proven Signals (what we know already works):\n"
        f"{json.dumps(proven_signals, indent=2)}\n\n"
        f"Generate exactly {HYPOTHESES_PER_RUN} fresh hypotheses. "
        f"Ensure coverage of all four segments (A/B/C/D). "
        f"Draw on competitor tactics AND food-subscription retention psychology. "
        f"Each hypothesis must be genuinely novel versus 'Already Tried'."
    )

    tool = {
        "name": "generate_competitor_hypotheses",
        "description": "Generate weekly hypotheses from competitor analysis and food-subscription psychology",
        "input_schema": {
            "type": "object",
            "properties": {
                "hypotheses": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "hypothesis": {
                                "type": "string",
                                "description": "Clear, testable statement of what we're testing"
                            },
                            "experiment_type": {
                                "type": "string",
                                "enum": ["cohort_message", "timing_test", "offer_test",
                                         "reactivation", "loyalty_nudge"]
                            },
                            "customer_segment": {
                                "type": "string",
                                "enum": ["never_ordered", "one_and_done",
                                         "lapsing_regular", "high_value_at_risk"],
                                "description": "Which of the four retention segments this targets"
                            },
                            "cohort_description": {
                                "type": "string",
                                "description": "Plain-English description of who to target"
                            },
                            "cohort_filter": {
                                "type": "object",
                                "description": (
                                    "Structured filter for cohort SQL generation. "
                                    "Keys: min_orders, max_orders, days_since_last_order_min, "
                                    "days_since_last_order_max, lifecycle_segment, never_ordered (bool)"
                                )
                            },
                            "message_template": {
                                "type": "string",
                                "description": "SMS template — max 160 chars, use {first_name}"
                            },
                            "success_threshold": {
                                "type": "number",
                                "description": "Minimum conversion rate to consider proven (0.0–1.0)"
                            },
                            "inspiration_source": {
                                "type": "string",
                                "description": (
                                    "Where this idea came from: 'cookunity_email', "
                                    "'competitor_website', 'food_subscription_psychology', "
                                    "or 'timing_pattern'"
                                )
                            },
                            "rationale": {
                                "type": "string",
                                "description": (
                                    "Why this angle should work for DabbahWala's "
                                    "Indian home-cooked brand and this specific segment"
                                )
                            }
                        },
                        "required": [
                            "hypothesis", "experiment_type", "customer_segment",
                            "cohort_description", "cohort_filter", "message_template",
                            "success_threshold", "inspiration_source", "rationale"
                        ]
                    },
                    "minItems": 4,
                    "maxItems": 10
                },
                "competitor_insights_summary": {
                    "type": "string",
                    "description": "2-3 sentence summary of the most actionable competitor patterns observed"
                }
            },
            "required": ["hypotheses", "competitor_insights_summary"]
        }
    }

    result = _tool_call(client, system, user, tool)
    hypotheses = result.get("hypotheses", [])
    insights_summary = result.get("competitor_insights_summary", "")
    logger.info("CompetitorAgent: Claude generated %d hypotheses", len(hypotheses))
    return hypotheses, insights_summary


# ---------------------------------------------------------------------------
# Phase 3: Auto-inject into goal_experiments
# ---------------------------------------------------------------------------

def _inject_hypotheses(hypotheses: list[dict]) -> int:
    """Insert competitor-sourced hypotheses into goal_experiments as pending."""
    injected = 0
    for h in hypotheses:
        try:
            with get_cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO goal_experiments
                        (hypothesis, experiment_type, cohort_description, cohort_filter,
                         message_template, success_threshold, status, source)
                    VALUES (%s, %s, %s, %s::jsonb, %s, %s, 'pending', 'competitor_agent')
                    """,
                    (
                        h.get("hypothesis", ""),
                        h.get("experiment_type", "cohort_message"),
                        h.get("cohort_description", ""),
                        json.dumps(h.get("cohort_filter", {})),
                        h.get("message_template", ""),
                        h.get("success_threshold", 0.10),
                    ),
                )
            injected += 1
        except Exception as e:
            logger.warning("CompetitorAgent: failed to inject hypothesis: %s | h=%s", e, h)
    logger.info("CompetitorAgent: injected %d/%d hypotheses", injected, len(hypotheses))
    return injected


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def _log_run(
    emails: int,
    websites: int,
    hypotheses: int,
    status: str,
    summary: str = "",
    error: str = "",
) -> None:
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO competitor_agent_runs
                    (sources_processed, email_samples_parsed, websites_scraped,
                     hypotheses_queued, status, summary, error_detail)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    emails + websites,
                    emails,
                    websites,
                    hypotheses,
                    status,
                    summary,
                    error or None,
                ),
            )
    except Exception as e:
        logger.error("CompetitorAgent: failed to write audit log: %s", e)


# ---------------------------------------------------------------------------
# Main cycle orchestrator
# ---------------------------------------------------------------------------

def _run_cycle() -> dict:
    client = _claude()

    # Phase 1: Research
    email_samples = _parse_email_samples()
    website_data  = _scrape_competitor_websites()

    # Phase 2: Gap analysis
    existing_experiments = _get_existing_experiments()
    proven_signals       = _get_proven_signals()
    customer_segments    = _get_customer_segments()

    # Phase 3: Generate + inject
    hypotheses, insights_summary = _run_competitor_hypothesis_generator(
        client,
        email_samples,
        website_data,
        existing_experiments,
        proven_signals,
        customer_segments,
    )
    injected = _inject_hypotheses(hypotheses)

    summary = (
        f"Parsed {len(email_samples)} competitor emails, "
        f"scraped {len(website_data)} websites. "
        f"Queued {injected} new experiments. "
        f"Insights: {insights_summary[:300]}"
    )

    _log_run(
        emails=len(email_samples),
        websites=len(website_data),
        hypotheses=injected,
        status="completed",
        summary=summary,
    )

    return {
        "email_samples_parsed": len(email_samples),
        "websites_scraped":     len(website_data),
        "sources_processed":    len(email_samples) + len(website_data),
        "hypotheses_queued":    injected,
        "insights_summary":     insights_summary,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/run", response_model=CompetitorAgentResult)
def run_full_cycle():
    """
    Run the full competitor research cycle:
      1. Parse .eml samples from data/cookunitysamples/ (new files auto-detected)
      2. Scrape competitor websites live
      3. Claude generates novel hypotheses covering all 4 customer segments
      4. Auto-inject into goal_experiments (picked up by goal agent at 9 AM)
    """
    ts = datetime.now(timezone.utc).isoformat()
    try:
        result = _run_cycle()
        return CompetitorAgentResult(timestamp=ts, **result)
    except Exception as e:
        logger.error("CompetitorAgent cycle failed: %s", e, exc_info=True)
        _log_run(0, 0, 0, "failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runs")
def list_runs(limit: int = 20):
    """List recent competitor agent run logs."""
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT id, sources_processed, email_samples_parsed, websites_scraped,
                   hypotheses_queued, status, summary, created_at
            FROM competitor_agent_runs
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    return {"runs": rows, "count": len(rows)}


@router.get("/experiments")
def list_competitor_experiments(limit: int = 50):
    """List all experiments sourced from the competitor agent."""
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT id, hypothesis, experiment_type, status, enrolled_count,
                   result_conversion_rate, conclusion, cohort_description, created_at
            FROM goal_experiments
            WHERE source = 'competitor_agent'
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    return {"experiments": rows, "count": len(rows)}
