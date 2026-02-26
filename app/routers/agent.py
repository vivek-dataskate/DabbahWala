"""
Claude Agent — Smart opportunity finder for DabbahWala.

Instead of hard-coded rules, Claude reasons about each contact's full profile
(order history, engagement, SMS/call transcripts, delivery notes) and decides:
- Should we reach out? How? (SMS, email, sales call)
- What message would resonate with THIS specific person?
- What's the best time and channel?
- Should we change their campaign?
- Is this person a subscription candidate?
- Should we convert them from app to direct?

This agent runs after the rule-based intelligence cycle as a second pass,
finding nuanced opportunities that rules miss.
"""
import json
import os

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_cursor

router = APIRouter()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-5-20250929"

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class AgentResult(BaseModel):
    contacts_analyzed: int
    opportunities_created: int
    actions: list[dict]
    reasoning_samples: list[dict]


class SingleContactAnalysis(BaseModel):
    contact_id: int
    should_act: bool
    action_type: str | None = None
    priority: str | None = None
    reason: str | None = None
    suggested_message: str | None = None
    channel: str | None = None
    confidence: float = 0.0
    reasoning: str = ""


# ---------------------------------------------------------------------------
# Claude API call
# ---------------------------------------------------------------------------
async def call_claude(system_prompt: str, user_prompt: str) -> str:
    """Call Claude API and return the response text."""
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 2000,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            },
        )
        if resp.status_code != 200:
            return f"API error: {resp.status_code}"
        data = resp.json()
        return data.get("content", [{}])[0].get("text", "")


# ---------------------------------------------------------------------------
# Build contact profile for Claude
# ---------------------------------------------------------------------------
def build_contact_profile(cur, contact_id: int) -> dict:
    """Gather everything we know about a contact for Claude to analyze."""
    # Basic info
    cur.execute("""
        SELECT c.*, r.opens_7d, r.clicks_7d, r.sms_sent_7d, r.sms_clicks_7d, r.orders_7d
        FROM contacts c
        LEFT JOIN engagement_rollups r ON r.contact_id = c.id
        WHERE c.id = %s
    """, (contact_id,))
    contact = dict(cur.fetchone()) if cur.rowcount else None
    if not contact:
        return None

    # Recent orders
    cur.execute("""
        SELECT order_date, source, total_amount, order_type, delivery_slot, customer_name_raw
        FROM orders WHERE contact_id = %s
        ORDER BY order_date DESC LIMIT 10
    """, (contact_id,))
    orders = [dict(r) for r in cur.fetchall()]

    # Favorite items
    cur.execute("""
        SELECT oi.item_name, count(*) as times_ordered, sum(oi.quantity) as total_qty
        FROM order_items oi JOIN orders o ON o.id = oi.order_id
        WHERE o.contact_id = %s
        GROUP BY oi.item_name ORDER BY times_ordered DESC LIMIT 5
    """, (contact_id,))
    fav_items = [dict(r) for r in cur.fetchall()]

    # Recent SMS
    cur.execute("""
        SELECT direction, body, sent_at FROM telnyx_messages
        WHERE contact_id = %s ORDER BY sent_at DESC LIMIT 5
    """, (contact_id,))
    sms_history = [dict(r) for r in cur.fetchall()]

    # Recent calls
    cur.execute("""
        SELECT transcript, summary, duration_sec, started_at FROM telnyx_calls
        WHERE contact_id = %s AND transcript IS NOT NULL
        ORDER BY started_at DESC LIMIT 3
    """, (contact_id,))
    call_history = [dict(r) for r in cur.fetchall()]

    # Decision log
    cur.execute("""
        SELECT prev_lifecycle, new_lifecycle, changes_applied, decided_at
        FROM decision_log WHERE contact_id = %s
        ORDER BY decided_at DESC LIMIT 5
    """, (contact_id,))
    decisions = [dict(r) for r in cur.fetchall()]

    # Pending opportunities (don't create duplicates)
    cur.execute("""
        SELECT action, reason, status FROM opportunities
        WHERE contact_id = %s AND status IN ('pending', 'dispatched')
    """, (contact_id,))
    pending_opps = [dict(r) for r in cur.fetchall()]

    # ── Current active menu ────────────────────────────────────────────────
    cur.execute(
        """SELECT item_name, category, is_veg, price, false AS is_featured
           FROM menu_catalog
           WHERE active = TRUE
           ORDER BY category NULLS LAST, item_name""",
    )
    current_menu = [dict(r) for r in cur.fetchall()]

    # Items on this week's menu the customer has NEVER ordered before
    ordered_names = {i['item_name'].lower() for i in fav_items}
    new_to_customer = [
        m for m in current_menu
        if m['item_name'].lower() not in ordered_names
    ]

    return {
        'contact': contact,
        'orders': orders,
        'favorite_items': fav_items,
        'sms_history': sms_history,
        'call_history': call_history,
        'decisions': decisions,
        'pending_opportunities': pending_opps,
        'current_menu': current_menu,
        'new_to_customer_this_week': new_to_customer,
    }


# ---------------------------------------------------------------------------
# System prompt for Claude (base — playbook rules appended dynamically)
# ---------------------------------------------------------------------------
BASE_SYSTEM_PROMPT = """You are the DabbahWala marketing intelligence agent. You analyze customer profiles
and decide if/how to reach out to convert them into new orders or subscriptions.

DabbahWala is a fresh, home-style Indian food delivery service in Atlanta. We cook food
fresh daily (not restaurant food — lighter oils, no MSG, small batches). We offer:
- One-time orders
- Weekly subscriptions (save 15-20%, daily delivery, zero planning)
- Free delivery on orders over $35

## Menu-aware personalisation
The customer profile includes two menu fields you MUST use when crafting messages:

1. `current_menu` — every item available THIS week. Always mention real items by name.
2. `new_to_customer_this_week` — items on this week's menu the customer has NEVER ordered.
   Use these to create curiosity: "We have something new you haven't tried yet…"

Rules for menu references:
- If the customer has favourite items, anchor the message there first, then bridge to something new.
- If `new_to_customer_this_week` has veg items and the customer only orders veg, highlight those.
- Never make up item names. Only reference items that appear in `current_menu`.
- If `current_menu` is empty, do not fabricate menu items — just be generic about "today's menu".

Our business goals (in priority order):
1. Get existing customers to REORDER
2. Convert one-time buyers to SUBSCRIBERS
3. Convert app customers (DoorDash/UberEats) to order DIRECT (saves them fees, better for us)
4. Win back LAPSED customers
5. Get REFERRALS from happy customers

For each contact, you must output valid JSON with these fields:
{
  "should_act": true/false,
  "action_type": "send_sms" | "send_email" | "sales_call" | "campaign_move" | null,
  "priority": "hot" | "warm" | "cold" | null,
  "reason": "why this action, be specific",
  "suggested_message": "the actual personalized message to send",
  "channel": "sms" | "email" | "phone" | null,
  "confidence": 0.0-1.0,
  "reasoning": "your step-by-step thinking about this contact"
}

Default rules:
- Do NOT create an action if the contact already has a pending opportunity
- Do NOT suggest contacting opted-out or cooling contacts
- Personalize messages using the contact's name, favorite items, and order history
- For SMS: keep under 160 chars. Be conversational, not salesy
- For sales calls: write a brief call script (what to mention)
- Be conservative with confidence — only high confidence (>0.8) for hot priority
- Consider timing: don't nudge someone who ordered yesterday
- Output ONLY the JSON object, no other text"""


def get_full_system_prompt() -> str:
    """
    Build the full system prompt by combining the base prompt with
    user-configured playbook rules from the database.

    Flow: Airtable -> n8n -> Python API -> DB -> here -> Claude
    """
    with get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT category, rule_name, instruction, priority
            FROM agent_playbook
            WHERE is_active = true
            ORDER BY
                CASE category
                    WHEN 'exclusion' THEN 1
                    WHEN 'priority' THEN 2
                    WHEN 'inference' THEN 3
                    WHEN 'decision' THEN 4
                    WHEN 'messaging' THEN 5
                    ELSE 6
                END,
                priority DESC
        """)
        rules = cur.fetchall()

    if not rules:
        return BASE_SYSTEM_PROMPT

    # Group by category
    categories = {}
    for r in rules:
        cat = r['category']
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(r)

    category_labels = {
        'exclusion': 'EXCLUSION RULES (Must Follow — these override everything)',
        'priority': 'PRIORITY RULES',
        'inference': 'INFERENCE RULES (What to Look For)',
        'decision': 'DECISION RULES (What to Do)',
        'messaging': 'MESSAGING RULES (How to Communicate)',
        'general': 'GENERAL RULES',
    }

    lines = ["\n\n## Your Playbook (Configured by the DabbahWala Team)\n"]
    lines.append("These rules come from the team and OVERRIDE the default rules above when they conflict.\n")

    for cat in ['exclusion', 'priority', 'inference', 'decision', 'messaging', 'general']:
        if cat in categories:
            lines.append(f"\n### {category_labels.get(cat, cat.upper())}")
            for r in categories[cat]:
                lines.append(f"- **{r['rule_name']}**: {r['instruction']}")

    return BASE_SYSTEM_PROMPT + "\n".join(lines)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("/analyze-contacts", response_model=AgentResult)
async def analyze_contacts(limit: int = 30):
    """
    Claude agent analyzes contacts and creates smart opportunities.
    Targets contacts who might be ready for outreach based on their full profile.
    """
    # Find contacts worth analyzing
    with get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT c.id, c.first_name, c.last_name, c.lifecycle_segment,
                   c.total_orders, c.last_order_at, c.primary_source,
                   r.opens_7d, r.clicks_7d, r.orders_7d
            FROM contacts c
            LEFT JOIN engagement_rollups r ON r.contact_id = c.id
            WHERE c.lifecycle_segment NOT IN ('optout', 'cooling')
              AND c.email IS NOT NULL
              -- Prioritize contacts with some signal
              AND (
                  -- Engaged but no recent order
                  (COALESCE(r.opens_7d, 0) >= 1 AND COALESCE(r.orders_7d, 0) = 0)
                  -- Has orders but hasn't ordered recently
                  OR (c.total_orders >= 1 AND c.last_order_at < now() - interval '7 days')
                  -- App customer
                  OR c.primary_source IN ('Food Delivery Apps', 'BOTH')
                  -- New customer (potential subscription)
                  OR (c.lifecycle_segment = 'new_customer' AND c.total_orders = 1)
              )
              -- Skip if already has pending opportunity
              AND NOT EXISTS (
                  SELECT 1 FROM opportunities o
                  WHERE o.contact_id = c.id AND o.status IN ('pending', 'dispatched')
              )
            ORDER BY
                -- Prioritize: engaged > new customer > lapsed > app
                CASE
                    WHEN COALESCE(r.opens_7d, 0) >= 2 THEN 1
                    WHEN c.lifecycle_segment = 'new_customer' THEN 2
                    WHEN c.total_orders >= 5 AND c.last_order_at < now() - interval '14 days' THEN 3
                    WHEN c.primary_source IN ('Food Delivery Apps', 'BOTH') THEN 4
                    ELSE 5
                END,
                c.last_order_at DESC NULLS LAST
            LIMIT %s
        """, (limit,))
        candidates = [dict(r) for r in cur.fetchall()]

    if not candidates:
        return AgentResult(contacts_analyzed=0, opportunities_created=0, actions=[], reasoning_samples=[])

    actions = []
    reasoning_samples = []
    opps_created = 0

    for candidate in candidates:
        cid = candidate['id']

        # Build full profile
        with get_cursor(commit=False) as cur:
            profile = build_contact_profile(cur, cid)
        if not profile:
            continue

        # Ask Claude to analyze
        # Serialize dates for JSON
        profile_str = json.dumps(profile, default=str, indent=2)
        user_prompt = f"Analyze this DabbahWala customer and decide if we should reach out:\n\n{profile_str}"

        try:
            response = await call_claude(get_full_system_prompt(), user_prompt)
            # Parse JSON response
            # Strip markdown code blocks if present
            clean = response.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
                if clean.endswith("```"):
                    clean = clean[:-3]
            analysis = json.loads(clean.strip())
        except (json.JSONDecodeError, Exception) as e:
            reasoning_samples.append({
                'contact_id': cid, 'error': str(e), 'raw_response': response[:500] if 'response' in locals() else ''
            })
            continue

        # Store reasoning sample
        if len(reasoning_samples) < 5:
            reasoning_samples.append({
                'contact_id': cid,
                'name': f"{candidate.get('first_name', '')} {candidate.get('last_name', '')}".strip(),
                'analysis': analysis,
            })

        # Create opportunity if Claude says to act
        if analysis.get('should_act') and analysis.get('action_type'):
            action_type = analysis['action_type']
            # Map to our enum
            opp_action = {
                'send_sms': 'send_sms',
                'send_email': 'send_email',
                'sales_call': 'field_sales_call',
                'campaign_move': 'send_email',
            }.get(action_type, 'send_email')

            priority = analysis.get('priority', 'warm')
            reason = analysis.get('reason', 'Claude agent recommendation')
            message = analysis.get('suggested_message', '')
            confidence = analysis.get('confidence', 0.7)

            with get_cursor(commit=True) as cur:
                cur.execute(
                    "SELECT create_opportunity(%s, %s::opportunity_action, %s, %s, %s, %s)",
                    (cid, opp_action, priority, f"[Claude Agent] {reason}", message, confidence)
                )
                opps_created += 1

            actions.append({
                'contact_id': cid,
                'name': f"{candidate.get('first_name', '')} {candidate.get('last_name', '')}".strip(),
                'action_type': action_type,
                'priority': priority,
                'reason': reason,
                'message': message[:100] + '...' if len(message) > 100 else message,
                'confidence': confidence,
            })

    return AgentResult(
        contacts_analyzed=len(candidates),
        opportunities_created=opps_created,
        actions=actions,
        reasoning_samples=reasoning_samples,
    )


@router.post("/analyze-single/{contact_id}")
async def analyze_single_contact(contact_id: int):
    """
    Analyze a single contact with Claude. Useful for testing or manual deep-dives.
    Returns full reasoning.
    """
    with get_cursor(commit=False) as cur:
        profile = build_contact_profile(cur, contact_id)

    if not profile:
        raise HTTPException(status_code=404, detail="Contact not found")

    profile_str = json.dumps(profile, default=str, indent=2)
    user_prompt = f"Analyze this DabbahWala customer and decide if we should reach out:\n\n{profile_str}"

    response = await call_claude(get_full_system_prompt(), user_prompt)

    try:
        clean = response.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
            if clean.endswith("```"):
                clean = clean[:-3]
        analysis = json.loads(clean.strip())
    except json.JSONDecodeError:
        analysis = {"raw_response": response, "parse_error": True}

    return {
        "contact_id": contact_id,
        "profile_summary": {
            "name": f"{profile['contact'].get('first_name', '')} {profile['contact'].get('last_name', '')}".strip(),
            "lifecycle": profile['contact'].get('lifecycle_segment'),
            "total_orders": profile['contact'].get('total_orders'),
            "last_order": str(profile['contact'].get('last_order_at', '')),
            "source": profile['contact'].get('primary_source'),
            "orders_count": len(profile['orders']),
            "favorite_items": [i['item_name'] for i in profile['favorite_items']],
        },
        "analysis": analysis,
    }
