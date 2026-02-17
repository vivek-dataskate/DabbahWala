"""MCP tools for recommendations — reactivation targets, content strategy."""

import json

from app.db import get_cursor


def register_recommendations_tools(mcp):
    @mcp.tool()
    def suggest_reactivation_targets(limit: int = 20) -> str:
        """Find contacts most likely to reactivate based on engagement history and order patterns.
        Prioritizes contacts who had recent engagement but haven't ordered.

        Args:
            limit: Max contacts to return (default 20)
        """
        with get_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT c.id, c.email, c.first_name, c.last_name, c.phone,
                       c.lifecycle_segment, c.total_orders, c.last_order_at,
                       c.sms_level, c.current_campaign,
                       r.opens_7d, r.clicks_7d, r.sms_clicks_7d,
                       -- Score: more engagement + more past orders = higher priority
                       (COALESCE(r.opens_7d, 0) * 1 + COALESCE(r.clicks_7d, 0) * 3
                        + COALESCE(r.sms_clicks_7d, 0) * 2 + c.total_orders * 2) AS reactivation_score
                FROM contacts c
                LEFT JOIN engagement_rollups r ON r.contact_id = c.id
                WHERE c.lifecycle_segment IN ('lapsed_customer', 'reactivation_candidate')
                  AND c.lifecycle_segment != 'optout'
                ORDER BY reactivation_score DESC, c.last_order_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
            result = [{k: str(v) if v is not None else None for k, v in dict(r).items()} for r in rows]
            return json.dumps({"count": len(result), "targets": result}, indent=2)

    @mcp.tool()
    def recommend_content_strategy(contact_id: int) -> str:
        """Analyze a contact's full history and suggest content/messaging strategy.
        Returns engagement data, communication history, and delivery feedback for agent analysis.

        Args:
            contact_id: The contact ID to analyze
        """
        with get_cursor(commit=False) as cur:
            # Contact details
            cur.execute("SELECT * FROM contacts WHERE id = %s", (contact_id,))
            contact = cur.fetchone()
            if not contact:
                return json.dumps({"error": f"Contact not found: {contact_id}"})

            # Engagement metrics
            cur.execute("SELECT * FROM engagement_rollups WHERE contact_id = %s", (contact_id,))
            rollups = dict(cur.fetchone()) if cur.rowcount else {}

            # Recent event types and counts
            cur.execute(
                """
                SELECT event_type, count(*) AS count
                FROM events WHERE contact_id = %s AND occurred_at > now() - interval '30 days'
                GROUP BY event_type
                """,
                (contact_id,),
            )
            event_summary = {r["event_type"]: r["count"] for r in cur.fetchall()}

            # Call transcripts (for sentiment/intent analysis)
            cur.execute(
                """
                SELECT transcript, summary, started_at
                FROM telnyx_calls
                WHERE contact_id = %s AND transcript IS NOT NULL
                ORDER BY started_at DESC LIMIT 5
                """,
                (contact_id,),
            )
            transcripts = [{k: str(v) for k, v in dict(r).items()} for r in cur.fetchall()]

            # Delivery feedback
            cur.execute(
                """
                SELECT status, notes, occurred_at
                FROM delivery_status
                WHERE contact_id = %s
                ORDER BY occurred_at DESC LIMIT 10
                """,
                (contact_id,),
            )
            delivery_notes = [{k: str(v) if v is not None else None for k, v in dict(r).items()} for r in cur.fetchall()]

            # Past opportunity outcomes
            cur.execute(
                """
                SELECT action, priority, reason, outcome, created_at
                FROM opportunities
                WHERE contact_id = %s AND outcome IS NOT NULL
                ORDER BY created_at DESC LIMIT 10
                """,
                (contact_id,),
            )
            past_outcomes = [{k: str(v) if v is not None else None for k, v in dict(r).items()} for r in cur.fetchall()]

            result = {
                "contact": {k: str(v) if v is not None else None for k, v in dict(contact).items()},
                "engagement_7d": {k: str(v) for k, v in rollups.items()} if rollups else {},
                "event_summary_30d": event_summary,
                "recent_transcripts": transcripts,
                "delivery_feedback": delivery_notes,
                "past_opportunity_outcomes": past_outcomes,
            }
            return json.dumps(result, indent=2)
