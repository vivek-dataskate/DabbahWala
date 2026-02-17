"""MCP tools for agent-driven opportunity detection and creation."""

import json

from app.db import get_cursor


def register_opportunities_tools(mcp):
    @mcp.tool()
    def detect_opportunities() -> str:
        """Analyze all contacts and return high-intent opportunity candidates.
        Checks for: multiple opens without orders, lapsed customers with recent engagement,
        new customers without second order, and contacts with positive delivery feedback.
        """
        with get_cursor(commit=False) as cur:
            candidates = []

            # Signal 1: Multiple opens, no order in 3+ days
            cur.execute(
                """
                SELECT c.id, c.email, c.first_name, c.last_name, c.phone,
                       c.lifecycle_segment, c.total_orders, c.last_order_at,
                       r.opens_7d, r.clicks_7d
                FROM contacts c
                JOIN engagement_rollups r ON r.contact_id = c.id
                WHERE r.opens_7d >= 2
                  AND r.orders_7d = 0
                  AND c.lifecycle_segment NOT IN ('optout', 'cooling', 'new_customer')
                  AND NOT EXISTS (
                      SELECT 1 FROM opportunities o
                      WHERE o.contact_id = c.id AND o.status IN ('pending', 'dispatched')
                  )
                """
            )
            for r in cur.fetchall():
                candidates.append({
                    **{k: str(v) if v is not None else None for k, v in dict(r).items()},
                    "signal": "multiple_opens_no_order",
                    "suggested_action": "send_sms",
                    "suggested_priority": "warm",
                })

            # Signal 2: New customer, no second order after 5 days
            cur.execute(
                """
                SELECT c.id, c.email, c.first_name, c.last_name, c.phone,
                       c.lifecycle_segment, c.total_orders, c.last_order_at
                FROM contacts c
                WHERE c.lifecycle_segment = 'new_customer'
                  AND c.total_orders = 1
                  AND c.last_order_at < now() - interval '5 days'
                  AND NOT EXISTS (
                      SELECT 1 FROM opportunities o
                      WHERE o.contact_id = c.id AND o.status IN ('pending', 'dispatched')
                  )
                """
            )
            for r in cur.fetchall():
                candidates.append({
                    **{k: str(v) if v is not None else None for k, v in dict(r).items()},
                    "signal": "new_customer_no_repeat",
                    "suggested_action": "send_sms",
                    "suggested_priority": "warm",
                })

            # Signal 3: Lapsed customer with recent engagement (lifecycle transition in last 3 days)
            cur.execute(
                """
                SELECT DISTINCT c.id, c.email, c.first_name, c.last_name, c.phone,
                       c.lifecycle_segment, c.total_orders, c.last_order_at
                FROM contacts c
                JOIN decision_log dl ON dl.contact_id = c.id
                WHERE dl.prev_lifecycle IN ('lapsed_customer', 'reactivation_candidate')
                  AND dl.new_lifecycle = 'engaged'
                  AND dl.decided_at > now() - interval '3 days'
                  AND NOT EXISTS (
                      SELECT 1 FROM opportunities o
                      WHERE o.contact_id = c.id AND o.status IN ('pending', 'dispatched')
                  )
                """
            )
            for r in cur.fetchall():
                candidates.append({
                    **{k: str(v) if v is not None else None for k, v in dict(r).items()},
                    "signal": "lapsed_reengaged",
                    "suggested_action": "field_sales_call",
                    "suggested_priority": "hot",
                })

            # Signal 4: Contacts with recent call transcripts mentioning reorder keywords
            cur.execute(
                """
                SELECT DISTINCT c.id, c.email, c.first_name, c.last_name, c.phone,
                       c.lifecycle_segment, c.total_orders, c.last_order_at,
                       tc.transcript
                FROM contacts c
                JOIN telnyx_calls tc ON tc.contact_id = c.id
                WHERE tc.started_at > now() - interval '3 days'
                  AND tc.transcript IS NOT NULL
                  AND (tc.transcript ILIKE '%reorder%' OR tc.transcript ILIKE '%order again%'
                       OR tc.transcript ILIKE '%next delivery%' OR tc.transcript ILIKE '%loved it%')
                  AND NOT EXISTS (
                      SELECT 1 FROM opportunities o
                      WHERE o.contact_id = c.id AND o.status IN ('pending', 'dispatched')
                  )
                """
            )
            for r in cur.fetchall():
                candidates.append({
                    **{k: str(v) if v is not None else None for k, v in dict(r).items()},
                    "signal": "reorder_intent_in_transcript",
                    "suggested_action": "field_sales_call",
                    "suggested_priority": "hot",
                })

            return json.dumps({"count": len(candidates), "candidates": candidates}, indent=2)

    @mcp.tool()
    def create_opportunity(
        contact_id: int,
        action: str,
        priority: str,
        reason: str,
        suggested_message: str | None = None,
        confidence_score: float | None = None,
    ) -> str:
        """Create an opportunity record in Postgres for n8n to dispatch.

        Args:
            contact_id: The contact ID
            action: One of: send_sms, field_sales_call, send_email
            priority: One of: hot, warm, cold
            reason: Agent's explanation of why this opportunity was identified
            suggested_message: For SMS: the message text. For calls: talking points.
            confidence_score: Agent's confidence 0.0-1.0
        """
        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO opportunities
                    (contact_id, action, priority, reason, suggested_message, confidence_score)
                VALUES (%s, %s::opportunity_action, %s, %s, %s, %s)
                RETURNING id
                """,
                (contact_id, action, priority, reason, suggested_message, confidence_score),
            )
            row = cur.fetchone()
            return json.dumps({"opportunity_id": row["id"], "status": "pending"})

    @mcp.tool()
    def get_opportunity_outcomes(days: int = 30) -> str:
        """Review past opportunity outcomes to understand what signals convert.
        Useful for calibrating future predictions.

        Args:
            days: Lookback period (default 30)
        """
        with get_cursor(commit=False) as cur:
            # Outcome summary
            cur.execute(
                """
                SELECT action, priority, outcome, count(*) AS count
                FROM opportunities
                WHERE completed_at > now() - (%s || ' days')::interval
                  AND outcome IS NOT NULL
                GROUP BY action, priority, outcome
                ORDER BY count DESC
                """,
                (str(days),),
            )
            summary = [{k: str(v) for k, v in dict(r).items()} for r in cur.fetchall()]

            # Conversion rate
            cur.execute(
                """
                SELECT
                    count(*) AS total,
                    count(*) FILTER (WHERE outcome = 'ordered') AS converted,
                    count(*) FILTER (WHERE outcome = 'not_interested') AS not_interested,
                    count(*) FILTER (WHERE outcome = 'no_answer') AS no_answer
                FROM opportunities
                WHERE completed_at > now() - (%s || ' days')::interval
                """,
                (str(days),),
            )
            rates = dict(cur.fetchone())

            return json.dumps({"days": days, "outcome_summary": summary, "rates": rates}, indent=2)

    @mcp.tool()
    def get_high_intent_signals(contact_id: int) -> str:
        """Get all available signals for a specific contact to help decide on outreach.
        Returns engagement data, transcripts, delivery notes, and SMS history.

        Args:
            contact_id: The contact ID to analyze
        """
        with get_cursor(commit=False) as cur:
            # Contact + rollups
            cur.execute(
                """
                SELECT c.*, r.opens_7d, r.clicks_7d, r.sms_sent_7d, r.sms_clicks_7d, r.orders_7d
                FROM contacts c
                LEFT JOIN engagement_rollups r ON r.contact_id = c.id
                WHERE c.id = %s
                """,
                (contact_id,),
            )
            contact = cur.fetchone()
            if not contact:
                return json.dumps({"error": f"Contact not found: {contact_id}"})

            # Recent SMS conversations
            cur.execute(
                """
                SELECT direction, body, is_delivery_staff, sent_at
                FROM telnyx_messages
                WHERE contact_id = %s
                ORDER BY sent_at DESC LIMIT 10
                """,
                (contact_id,),
            )
            sms = [{k: str(v) if v is not None else None for k, v in dict(r).items()} for r in cur.fetchall()]

            # Call transcripts
            cur.execute(
                """
                SELECT transcript, summary, is_delivery_staff, duration_sec, started_at
                FROM telnyx_calls
                WHERE contact_id = %s AND transcript IS NOT NULL
                ORDER BY started_at DESC LIMIT 5
                """,
                (contact_id,),
            )
            calls = [{k: str(v) if v is not None else None for k, v in dict(r).items()} for r in cur.fetchall()]

            # Delivery notes
            cur.execute(
                """
                SELECT status, notes, updated_by, occurred_at
                FROM delivery_status
                WHERE contact_id = %s
                ORDER BY occurred_at DESC LIMIT 10
                """,
                (contact_id,),
            )
            deliveries = [{k: str(v) if v is not None else None for k, v in dict(r).items()} for r in cur.fetchall()]

            result = {
                "contact": {k: str(v) if v is not None else None for k, v in dict(contact).items()},
                "recent_sms": sms,
                "call_transcripts": calls,
                "delivery_notes": deliveries,
            }
            return json.dumps(result, indent=2)
