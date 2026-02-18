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
            cur.execute("SELECT * FROM detect_engaged_no_order()")
            for r in cur.fetchall():
                candidates.append({
                    **{k: str(v) if v is not None else None for k, v in dict(r).items()},
                    "signal": "multiple_opens_no_order",
                    "suggested_action": "send_sms",
                    "suggested_priority": "warm",
                })

            # Signal 2: New customer, no second order after 5 days
            cur.execute("SELECT * FROM detect_new_customer_no_repeat()")
            for r in cur.fetchall():
                candidates.append({
                    **{k: str(v) if v is not None else None for k, v in dict(r).items()},
                    "signal": "new_customer_no_repeat",
                    "suggested_action": "send_sms",
                    "suggested_priority": "warm",
                })

            # Signal 3: Lapsed customer with recent engagement
            cur.execute("SELECT * FROM detect_lapsed_reengaged()")
            for r in cur.fetchall():
                candidates.append({
                    **{k: str(v) if v is not None else None for k, v in dict(r).items()},
                    "signal": "lapsed_reengaged",
                    "suggested_action": "field_sales_call",
                    "suggested_priority": "hot",
                })

            # Signal 4: Reorder intent in call transcripts
            cur.execute("SELECT * FROM detect_reorder_intent()")
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
                "SELECT create_opportunity(%s, %s::opportunity_action, %s, %s, %s, %s)",
                (contact_id, action, priority, reason, suggested_message, confidence_score),
            )
            opp_id = cur.fetchone()["create_opportunity"]
            return json.dumps({"opportunity_id": opp_id, "status": "pending"})

    @mcp.tool()
    def get_opportunity_outcomes(days: int = 30) -> str:
        """Review past opportunity outcomes to understand what signals convert.
        Useful for calibrating future predictions.

        Args:
            days: Lookback period (default 30)
        """
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT get_opportunity_outcomes(%s)", (days,))
            result = cur.fetchone()["get_opportunity_outcomes"]
            return json.dumps(result, indent=2)

    @mcp.tool()
    def get_high_intent_signals(contact_id: int) -> str:
        """Get all available signals for a specific contact to help decide on outreach.
        Returns engagement data, transcripts, delivery notes, and SMS history.

        Args:
            contact_id: The contact ID to analyze
        """
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT get_high_intent_signals(%s)", (contact_id,))
            result = cur.fetchone()["get_high_intent_signals"]
            return json.dumps(result, indent=2)
