"""MCP tools for analytics, reporting, and campaign performance."""

import json

from app.db import get_cursor


def register_analytics_tools(mcp):
    @mcp.tool()
    def get_lifecycle_summary() -> str:
        """Get count of contacts per lifecycle segment. Returns a pipeline snapshot."""
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT * FROM get_lifecycle_summary()")
            rows = cur.fetchall()
            total = sum(r["count"] for r in rows)
            result = {
                "total_contacts": total,
                "segments": {r["lifecycle_segment"]: r["count"] for r in rows},
            }
            return json.dumps(result, indent=2)

    @mcp.tool()
    def get_campaign_performance(campaign: str, days: int = 7) -> str:
        """Get performance metrics for a specific campaign over a time period.

        Args:
            campaign: Campaign name (NURTURE_SLOW, PROMO_STANDARD, etc.)
            days: Lookback period in days (default 7)
        """
        with get_cursor(commit=False) as cur:
            cur.execute(
                "SELECT get_campaign_performance(%s::campaign_name, %s)",
                (campaign, days),
            )
            result = cur.fetchone()["get_campaign_performance"]
            return json.dumps(result, indent=2)

    @mcp.tool()
    def get_engagement_trends(days: int = 30) -> str:
        """Get aggregate engagement metrics over time, grouped by day.

        Args:
            days: Lookback period (default 30)
        """
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT * FROM get_engagement_trends(%s)", (days,))
            rows = cur.fetchall()

            # Reshape into day -> {event_type: count}
            trends = {}
            for r in rows:
                day = str(r["day"])
                if day not in trends:
                    trends[day] = {}
                trends[day][r["event_type"]] = r["count"]

            return json.dumps({"days": days, "trends": trends}, indent=2)

    @mcp.tool()
    def get_order_attribution(days_lookback: int = 7) -> str:
        """For each recent order, find the most recent marketing touch to attribute conversions.

        Args:
            days_lookback: How many days back to look for marketing touches before each order (default 7)
        """
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT get_order_attribution(%s)", (days_lookback,))
            result = cur.fetchone()["get_order_attribution"]
            return json.dumps(result, indent=2)

    @mcp.tool()
    def get_daily_report(report_date: str) -> str:
        """Retrieve a previously generated daily report.

        Args:
            report_date: Date in YYYY-MM-DD format
        """
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT * FROM get_daily_report(%s)", (report_date,))
            row = cur.fetchone()
            if not row:
                return json.dumps({"error": f"No report found for {report_date}"})
            return json.dumps(
                {k: str(v) if v is not None else None for k, v in dict(row).items()},
                indent=2,
            )

    @mcp.tool()
    def get_decision_history(contact_id: int, limit: int = 20) -> str:
        """Get the audit trail of lifecycle decisions for a contact.

        Args:
            contact_id: The contact ID
            limit: Max decisions to return (default 20)
        """
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT * FROM get_decision_history(%s, %s)", (contact_id, limit))
            rows = cur.fetchall()
            result = [{k: str(v) for k, v in dict(r).items()} for r in rows]
            return json.dumps({"contact_id": contact_id, "decisions": result}, indent=2)
