"""
Intelligence Engine — The brain of DabbahWala marketing system.

Runs the full cycle: INTAKE -> EVIDENCE -> INFERENCE -> DECISION -> EXECUTION

Called hourly by n8n to:
1. INTAKE: Pull Instantly campaign events (opens/clicks/replies), poll recent Telnyx SMS/calls
2. EVIDENCE: Update engagement rollups, contact stats, order history
3. INFERENCE: Detect signals (high-intent, lapsed return, app->direct, sub potential)
4. DECISION: Create opportunities, move campaigns, trigger SMS, flag for sales calls
5. EXECUTION: Return actions for n8n to dispatch

This is the system that constantly finds opportunities to get new orders/subscriptions,
change campaigns, or ask sales agents to call.
"""
import json
from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.db import get_cursor

router = APIRouter()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------
class CycleResult(BaseModel):
    timestamp: str
    intake: dict
    evidence: dict
    inference: dict
    decisions: dict
    execution: dict


class ActionItem(BaseModel):
    action_type: str   # 'campaign_move', 'send_sms', 'sales_call', 'send_email'
    contact_id: int
    contact_email: str | None = None
    contact_phone: str | None = None
    contact_name: str | None = None
    priority: str
    reason: str
    suggested_message: str | None = None
    metadata: dict = {}


# ---------------------------------------------------------------------------
# PHASE 1: INTAKE — Collect new data from all sources
# ---------------------------------------------------------------------------
def _phase_intake(cur) -> dict:
    """Pull new events that haven't been processed yet."""
    stats = {
        'email_opens': 0, 'email_clicks': 0, 'sms_received': 0,
        'calls_completed': 0, 'orders_placed': 0, 'new_events_total': 0
    }

    # Count recent events (last 2 hours to catch anything since last run)
    cur.execute("""
        SELECT event_type, count(*) as cnt
        FROM events
        WHERE occurred_at > now() - interval '2 hours'
        GROUP BY event_type
    """)
    for row in cur.fetchall():
        et = row['event_type']
        cnt = row['cnt']
        if et == 'email_open':
            stats['email_opens'] = cnt
        elif et == 'email_click':
            stats['email_clicks'] = cnt
        elif et in ('sms_received', 'sms_sent'):
            stats['sms_received'] += cnt
        elif et == 'call_completed':
            stats['calls_completed'] = cnt
        elif et == 'order_placed':
            stats['orders_placed'] = cnt
        stats['new_events_total'] += cnt

    # Count recent Telnyx messages
    cur.execute("""
        SELECT count(*) as cnt FROM telnyx_messages
        WHERE sent_at > now() - interval '2 hours'
    """)
    stats['telnyx_messages'] = cur.fetchone()['cnt']

    # Count recent Telnyx calls
    cur.execute("""
        SELECT count(*) as cnt FROM telnyx_calls
        WHERE started_at > now() - interval '2 hours'
    """)
    stats['telnyx_calls'] = cur.fetchone()['cnt']

    return stats


# ---------------------------------------------------------------------------
# PHASE 2: EVIDENCE — Update engagement rollups and contact stats
# ---------------------------------------------------------------------------
def _phase_evidence(cur) -> dict:
    """Refresh materialized evidence: rollups, lifecycle, order counts."""
    stats = {}

    # Refresh 7-day engagement rollups
    cur.execute("SELECT refresh_engagement_rollups()")
    stats['rollups_refreshed'] = True

    # Count contacts with engagement activity
    cur.execute("""
        SELECT count(*) as active_contacts
        FROM engagement_rollups
        WHERE opens_7d > 0 OR clicks_7d > 0 OR orders_7d > 0 OR sms_clicks_7d > 0
    """)
    stats['active_contacts'] = cur.fetchone()['active_contacts']

    # Lifecycle distribution
    cur.execute("""
        SELECT lifecycle_segment, count(*) as cnt
        FROM contacts
        GROUP BY lifecycle_segment
        ORDER BY cnt DESC
    """)
    stats['lifecycle_distribution'] = {r['lifecycle_segment']: r['cnt'] for r in cur.fetchall()}

    return stats


# ---------------------------------------------------------------------------
# PHASE 3: INFERENCE — Detect signals and patterns
# ---------------------------------------------------------------------------
def _phase_inference(cur) -> dict:
    """Run all signal detection functions to find opportunities."""
    signals = {
        'engaged_no_order': [],
        'new_customer_no_repeat': [],
        'lapsed_reengaged': [],
        'reorder_intent': [],
        'app_customers_for_conversion': [],
        'subscription_candidates': [],
        'high_value_at_risk': [],
    }

    # Signal 1: Engaged (opens/clicks) but hasn't ordered
    try:
        cur.execute("SELECT * FROM detect_engaged_no_order()")
        signals['engaged_no_order'] = [dict(r) for r in cur.fetchall()]
    except Exception:
        pass

    # Signal 2: New customer, 1 order, no repeat
    try:
        cur.execute("SELECT * FROM detect_new_customer_no_repeat()")
        signals['new_customer_no_repeat'] = [dict(r) for r in cur.fetchall()]
    except Exception:
        pass

    # Signal 3: Lapsed customer re-engaged
    try:
        cur.execute("SELECT * FROM detect_lapsed_reengaged()")
        signals['lapsed_reengaged'] = [dict(r) for r in cur.fetchall()]
    except Exception:
        pass

    # Signal 4: Reorder intent in transcripts
    try:
        cur.execute("SELECT * FROM detect_reorder_intent()")
        signals['reorder_intent'] = [dict(r) for r in cur.fetchall()]
    except Exception:
        pass

    # Signal 5: App customers who could convert to direct
    try:
        cur.execute("""
            SELECT c.id, c.email, c.first_name, c.last_name, c.phone,
                   c.lifecycle_segment, c.total_orders, c.last_order_at, c.primary_source
            FROM contacts c
            WHERE c.primary_source IN ('Food Delivery Apps', 'BOTH')
              AND c.lifecycle_segment NOT IN ('optout', 'cooling')
              AND c.email IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM opportunities o
                  WHERE o.contact_id = c.id AND o.status IN ('pending', 'dispatched')
              )
              AND NOT EXISTS (
                  SELECT 1 FROM orders o
                  WHERE o.contact_id = c.id AND o.source = 'Website'
                  AND o.order_date > now() - interval '30 days'
              )
        """)
        signals['app_customers_for_conversion'] = [dict(r) for r in cur.fetchall()]
    except Exception:
        pass

    # Signal 6: Subscription candidates (3+ one-time orders, no subscription)
    try:
        cur.execute("""
            SELECT c.id, c.email, c.first_name, c.last_name, c.phone,
                   c.lifecycle_segment, c.total_orders, c.last_order_at
            FROM contacts c
            WHERE c.total_orders >= 3
              AND (c.subscription_type IS NULL OR c.subscription_type = '')
              AND c.lifecycle_segment NOT IN ('optout', 'cooling')
              AND NOT EXISTS (
                  SELECT 1 FROM opportunities o
                  WHERE o.contact_id = c.id AND o.status IN ('pending', 'dispatched')
                  AND o.reason ILIKE '%subscription%'
              )
        """)
        signals['subscription_candidates'] = [dict(r) for r in cur.fetchall()]
    except Exception:
        pass

    # Signal 7: High-value customers at risk (5+ orders, no order in 14+ days)
    try:
        cur.execute("""
            SELECT c.id, c.email, c.first_name, c.last_name, c.phone,
                   c.lifecycle_segment, c.total_orders, c.last_order_at
            FROM contacts c
            WHERE c.total_orders >= 5
              AND c.last_order_at < now() - interval '14 days'
              AND c.lifecycle_segment NOT IN ('optout', 'cooling', 'lapsed_customer')
              AND NOT EXISTS (
                  SELECT 1 FROM opportunities o
                  WHERE o.contact_id = c.id AND o.status IN ('pending', 'dispatched')
              )
        """)
        signals['high_value_at_risk'] = [dict(r) for r in cur.fetchall()]
    except Exception:
        pass

    return {k: len(v) for k, v in signals.items()}, signals


# ---------------------------------------------------------------------------
# PHASE 4: DECISION — Create opportunities and determine actions
# ---------------------------------------------------------------------------
def _phase_decision(cur, signals: dict) -> tuple:
    """Turn signals into concrete opportunities and actions."""
    actions = []
    opp_created = 0
    campaign_moves = 0
    sms_triggered = 0

    # Decision 1: Engaged contacts who haven't ordered -> send promo email
    for contact in signals.get('engaged_no_order', [])[:20]:
        cur.execute(
            "SELECT create_opportunity(%s, 'send_email'::opportunity_action, 'warm', %s, %s, 0.75)",
            (contact['id'],
             f"Engaged ({contact.get('opens_7d', 0)} opens, {contact.get('clicks_7d', 0)} clicks) but no order in 7 days",
             f"Hi {contact.get('first_name', 'there')}, we noticed you've been checking us out! "
             f"Today's menu is fresh and ready. Order at dabbahwala.com — free delivery over $35.")
        )
        opp_created += 1
        actions.append({
            'action_type': 'send_email', 'contact_id': contact['id'],
            'contact_email': contact.get('email'), 'contact_name': f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip(),
            'priority': 'warm', 'reason': 'Engaged but no order',
        })

    # Decision 2: New customer no repeat -> SMS nudge + subscription pitch
    for contact in signals.get('new_customer_no_repeat', [])[:15]:
        cur.execute(
            "SELECT create_opportunity(%s, 'send_sms'::opportunity_action, 'warm', %s, %s, 0.80)",
            (contact['id'],
             f"First-time customer hasn't reordered after 5+ days",
             f"Hi {contact.get('first_name', 'there')}, hope you enjoyed your first DabbahWala meal! "
             f"This week's menu has something new. Reply MENU to see options. "
             f"Subscribe weekly and save 15-20%!")
        )
        opp_created += 1
        sms_triggered += 1
        actions.append({
            'action_type': 'send_sms', 'contact_id': contact['id'],
            'contact_phone': contact.get('phone'), 'contact_name': f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip(),
            'priority': 'warm', 'reason': 'New customer no repeat',
        })

    # Decision 3: Lapsed re-engaged -> Sales call (high intent!)
    for contact in signals.get('lapsed_reengaged', []):
        cur.execute(
            "SELECT create_opportunity(%s, 'field_sales_call'::opportunity_action, 'hot', %s, %s, 0.90)",
            (contact['id'],
             f"Lapsed customer re-engaged! Was {contact.get('lifecycle_segment')}, now active again",
             f"Call {contact.get('first_name', 'customer')} to welcome them back. "
             f"Mention the improvements, offer subscription, and note we'll take extra care of their order.")
        )
        opp_created += 1
        actions.append({
            'action_type': 'sales_call', 'contact_id': contact['id'],
            'contact_phone': contact.get('phone'), 'contact_name': f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip(),
            'priority': 'hot', 'reason': 'Lapsed customer re-engaged',
        })

    # Decision 4: Reorder intent in transcripts -> Immediate SMS
    for contact in signals.get('reorder_intent', []):
        cur.execute(
            "SELECT create_opportunity(%s, 'send_sms'::opportunity_action, 'hot', %s, %s, 0.92)",
            (contact['id'],
             f"Reorder intent detected in call transcript",
             f"Hi {contact.get('first_name', 'there')}, we'd love to send your next order! "
             f"Reply MENU for today's fresh options or visit dabbahwala.com")
        )
        opp_created += 1
        sms_triggered += 1
        actions.append({
            'action_type': 'send_sms', 'contact_id': contact['id'],
            'contact_phone': contact.get('phone'), 'contact_name': f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip(),
            'priority': 'hot', 'reason': 'Reorder intent in transcript',
        })

    # Decision 5: App customers -> Campaign move to APP_TO_DIRECT + SMS
    for contact in signals.get('app_customers_for_conversion', [])[:25]:
        # Move to APP_TO_DIRECT Instantly campaign
        cur.execute(
            "INSERT INTO campaign_queue (contact_id, from_campaign, to_campaign) "
            "VALUES (%s, (SELECT current_campaign FROM contacts WHERE id = %s), 'APP_TO_DIRECT') "
            "ON CONFLICT DO NOTHING",
            (contact['id'], contact['id'])
        )
        campaign_moves += 1

        cur.execute(
            "SELECT create_opportunity(%s, 'send_sms'::opportunity_action, 'warm', %s, %s, 0.82)",
            (contact['id'],
             f"App customer eligible for direct conversion (source: {contact.get('primary_source', 'app')})",
             f"Hi {contact.get('first_name', 'there')}! Ordering DabbahWala direct saves you fees "
             f"and gets you priority delivery. Same great food! Visit dabbahwala.com or reply MENU.")
        )
        opp_created += 1
        sms_triggered += 1
        actions.append({
            'action_type': 'campaign_move', 'contact_id': contact['id'],
            'contact_email': contact.get('email'), 'contact_name': f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip(),
            'priority': 'warm', 'reason': f"App customer -> direct conversion",
        })

    # Decision 6: Subscription candidates -> SMS pitch
    for contact in signals.get('subscription_candidates', [])[:20]:
        cur.execute(
            "SELECT create_opportunity(%s, 'send_sms'::opportunity_action, 'warm', %s, %s, 0.78)",
            (contact['id'],
             f"Repeat customer ({contact.get('total_orders', 0)} orders) not yet on subscription",
             f"Hi {contact.get('first_name', 'there')}, you've ordered {contact.get('total_orders', 0)} times — "
             f"have you considered our weekly subscription? Save 15-20%, get priority delivery, "
             f"zero daily planning. Reply SUBSCRIBE for details!")
        )
        opp_created += 1
        sms_triggered += 1
        actions.append({
            'action_type': 'send_sms', 'contact_id': contact['id'],
            'contact_phone': contact.get('phone'), 'contact_name': f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip(),
            'priority': 'warm', 'reason': f"Subscription candidate ({contact.get('total_orders', 0)} orders)",
        })

    # Decision 7: High-value at risk -> Sales call
    for contact in signals.get('high_value_at_risk', [])[:10]:
        cur.execute(
            "SELECT create_opportunity(%s, 'field_sales_call'::opportunity_action, 'hot', %s, %s, 0.88)",
            (contact['id'],
             f"High-value customer ({contact.get('total_orders', 0)} orders) at risk — no order in 14+ days",
             f"Call {contact.get('first_name', 'customer')} to check in. They have {contact.get('total_orders', 0)} orders "
             f"but haven't ordered recently. Ask if everything is okay, mention new menu items, "
             f"offer to personally handle their next order.")
        )
        opp_created += 1
        actions.append({
            'action_type': 'sales_call', 'contact_id': contact['id'],
            'contact_phone': contact.get('phone'), 'contact_name': f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip(),
            'priority': 'hot', 'reason': f"High-value customer at risk ({contact.get('total_orders', 0)} orders)",
        })

    return {
        'opportunities_created': opp_created,
        'campaign_moves': campaign_moves,
        'sms_triggered': sms_triggered,
        'total_actions': len(actions),
    }, actions


# ---------------------------------------------------------------------------
# PHASE 5: EXECUTION — Run lifecycle engine and return actions for n8n
# ---------------------------------------------------------------------------
def _phase_execution(cur) -> dict:
    """Run lifecycle rules and prepare execution payload for n8n."""
    stats = {}

    # Run the full lifecycle rule engine
    cur.execute("SELECT * FROM run_lifecycle_cycle()")
    row = cur.fetchone()
    stats['lifecycle_contacts_updated'] = row.get('contacts_updated', 0)
    stats['lifecycle_campaigns_queued'] = row.get('campaigns_queued', 0)

    # Get pending actions for n8n
    cur.execute("SELECT count(*) as cnt FROM campaign_queue WHERE status = 'pending'")
    stats['pending_campaign_moves'] = cur.fetchone()['cnt']

    cur.execute("SELECT count(*) as cnt FROM opportunities WHERE status = 'pending'")
    stats['pending_opportunities'] = cur.fetchone()['cnt']

    return stats


# ---------------------------------------------------------------------------
# MAIN ENDPOINT — Full intelligence cycle
# ---------------------------------------------------------------------------
@router.post("/run-cycle", response_model=CycleResult)
def run_intelligence_cycle():
    """
    Run the complete intelligence cycle.
    Called hourly by n8n. Returns all actions that need execution.

    Flow: INTAKE -> EVIDENCE -> INFERENCE -> DECISION -> EXECUTION
    """
    with get_cursor(commit=True) as cur:
        # Phase 1: Intake
        intake_stats = _phase_intake(cur)

        # Phase 2: Evidence
        evidence_stats = _phase_evidence(cur)

        # Phase 3: Inference
        inference_counts, raw_signals = _phase_inference(cur)

        # Phase 4: Decision
        decision_stats, actions = _phase_decision(cur, raw_signals)

        # Phase 5: Execution
        execution_stats = _phase_execution(cur)

    return CycleResult(
        timestamp=datetime.utcnow().isoformat(),
        intake=intake_stats,
        evidence=evidence_stats,
        inference=inference_counts,
        decisions=decision_stats,
        execution=execution_stats,
    )


@router.get("/pending-actions")
def get_pending_actions():
    """
    Get all pending actions for n8n to execute.
    Returns campaign moves, SMS to send, and sales calls to make.
    """
    with get_cursor(commit=False) as cur:
        # Pending campaign moves
        cur.execute("""
            SELECT cq.id as queue_id, c.email, c.phone, c.first_name, c.last_name,
                   cq.from_campaign, cq.to_campaign,
                   cr.instantly_campaign_id, cr.instantly_campaign_name
            FROM campaign_queue cq
            JOIN contacts c ON c.id = cq.contact_id
            LEFT JOIN campaign_routing cr ON cr.lifecycle_segment = c.lifecycle_segment
            WHERE cq.status = 'pending'
            ORDER BY cq.created_at
        """)
        campaign_moves = [dict(r) for r in cur.fetchall()]

        # Pending opportunities (grouped by action type)
        cur.execute("""
            SELECT o.id, o.contact_id, o.action, o.priority, o.reason,
                   o.suggested_message, o.confidence_score,
                   c.email, c.phone, c.first_name, c.last_name,
                   c.lifecycle_segment, c.total_orders
            FROM opportunities o
            JOIN contacts c ON c.id = o.contact_id
            WHERE o.status = 'pending'
            ORDER BY
                CASE o.priority WHEN 'hot' THEN 1 WHEN 'warm' THEN 2 ELSE 3 END,
                o.created_at
        """)
        opportunities = [dict(r) for r in cur.fetchall()]

        # Split by action type
        sms_actions = [o for o in opportunities if o['action'] == 'send_sms']
        email_actions = [o for o in opportunities if o['action'] == 'send_email']
        call_actions = [o for o in opportunities if o['action'] == 'field_sales_call']

        # Get SMS templates for pending SMS
        sms_with_templates = []
        for sms in sms_actions:
            cur.execute("""
                SELECT template_key, body, variant
                FROM sms_templates
                WHERE is_active = true
                ORDER BY random() LIMIT 1
            """)
            template = cur.fetchone()
            sms_entry = {**sms}
            if template:
                # Replace variables in template
                body = template['body']
                body = body.replace('{{first_name}}', sms.get('first_name', 'there') or 'there')
                sms_entry['template_body'] = body
                sms_entry['template_key'] = template['template_key']
            sms_with_templates.append(sms_entry)

        return {
            'campaign_moves': campaign_moves,
            'sms_to_send': sms_with_templates,
            'emails_to_send': email_actions,
            'sales_calls': call_actions,
            'summary': {
                'total_pending': len(campaign_moves) + len(opportunities),
                'campaign_moves': len(campaign_moves),
                'sms': len(sms_actions),
                'emails': len(email_actions),
                'sales_calls': len(call_actions),
            }
        }


@router.post("/ingest-instantly-events")
async def ingest_instantly_events(request: Request):
    """
    Ingest Instantly campaign events (opens, clicks, replies).
    Called by n8n which polls Instantly API hourly.

    Expected body: {"events": [{"email": "...", "event_type": "open|click|reply", "campaign_id": "..."}]}
    """
    body = await request.json()
    events = body.get('events', [])

    ingested = 0
    errors = 0

    with get_cursor(commit=True) as cur:
        for event in events:
            email = event.get('email', '').strip().lower()
            event_type = event.get('event_type', '')
            campaign_id = event.get('campaign_id', '')

            if not email or not event_type:
                errors += 1
                continue

            # Map Instantly event types to our event types
            our_type = {
                'open': 'email_open',
                'click': 'email_click',
                'reply': 'email_click',  # treat replies as high-value clicks
            }.get(event_type, 'email_open')

            # Find contact
            cur.execute("SELECT id FROM contacts WHERE email = %s", (email,))
            row = cur.fetchone()
            if not row:
                errors += 1
                continue

            contact_id = row['id']
            metadata = json.dumps({
                'source': 'instantly',
                'campaign_id': campaign_id,
                'original_type': event_type,
            })

            cur.execute(
                "INSERT INTO events (contact_id, event_type, metadata, occurred_at) "
                "VALUES (%s, %s, %s::jsonb, now())",
                (contact_id, our_type, metadata)
            )
            ingested += 1

    return {'ingested': ingested, 'errors': errors, 'total': len(events)}
