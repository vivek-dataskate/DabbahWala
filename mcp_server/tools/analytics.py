"""MCP tools for analytics, reporting, and campaign performance."""

import json

from app.db import get_cursor


def register_analytics_tools(mcp):
    @mcp.tool()
    def get_lifecycle_summary() -> str:
        """Get count of contacts per lifecycle segment. Returns a pipeline snapshot."""
        with get_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT lifecycle_segment, count(*) AS count
                FROM contacts
                GROUP BY lifecycle_segment
                ORDER BY count DESC
                """
            )
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
            # Contacts currently in this campaign
            cur.execute(
                "SELECT count(*) AS count FROM contacts WHERE current_campaign = %s::campaign_name",
                (campaign,),
            )
            contact_count = cur.fetchone()["count"]

            # Events for contacts in this campaign
            cur.execute(
                """
                SELECT e.event_type, count(*) AS count
                FROM events e
                JOIN contacts c ON c.id = e.contact_id
                WHERE c.current_campaign = %s::campaign_name
                  AND e.occurred_at > now() - (%s || ' days')::interval
                GROUP BY e.event_type
                """,
                (campaign, str(days)),
            )
            activity = {r["event_type"]: r["count"] for r in cur.fetchall()}

            result = {
                "campaign": campaign,
                "days": days,
                "contacts_in_campaign": contact_count,
                "activity": activity,
            }
            return json.dumps(result, indent=2)

    @mcp.tool()
    def get_engagement_trends(days: int = 30) -> str:
        """Get aggregate engagement metrics over time, grouped by day.

        Args:
            days: Lookback period (default 30)
        """
        with get_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT
                    occurred_at::date AS day,
                    event_type,
                    count(*) AS count
                FROM events
                WHERE occurred_at > now() - (%s || ' days')::interval
                GROUP BY occurred_at::date, event_type
                ORDER BY day DESC, event_type
                """,
                (str(days),),
            )
            rows = cur.fetchall()

            # Reshape into day → {event_type: count}
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
            cur.execute(
                """
                WITH orders AS (
                    SELECT e.id AS order_event_id, e.contact_id, e.occurred_at AS order_at,
                           c.email, c.current_campaign
                    FROM events e
                    JOIN contacts c ON c.id = e.contact_id
                    WHERE e.event_type = 'order_placed'
                      AND e.occurred_at > now() - interval '30 days'
                ),
                attributed AS (
                    SELECT o.*,
                           (SELECT e2.event_type FROM events e2
                            WHERE e2.contact_id = o.contact_id
                              AND e2.event_type IN ('email_open', 'email_click', 'sms_click')
                              AND e2.occurred_at BETWEEN o.order_at - (%s || ' days')::interval AND o.order_at
                            ORDER BY e2.occurred_at DESC LIMIT 1
                           ) AS attributed_touch
                    FROM orders o
                )
                SELECT
                    count(*) AS total_orders,
                    count(attributed_touch) AS attributed_orders,
                    count(*) - count(attributed_touch) AS unattributed_orders
                FROM attributed
                """,
                (str(days_lookback),),
            )
            row = cur.fetchone()
            return json.dumps(dict(row), indent=2)

    @mcp.tool()
    def get_daily_report(report_date: str) -> str:
        """Retrieve a previously generated daily report.

        Args:
            report_date: Date in YYYY-MM-DD format
        """
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT * FROM daily_reports WHERE report_date = %s", (report_date,))
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
            cur.execute(
                """
                SELECT dl.id, r.rule_name, dl.prev_lifecycle, dl.new_lifecycle,
                       dl.changes_applied, dl.decided_at
                FROM decision_log dl
                LEFT JOIN rules r ON r.id = dl.rule_id
                WHERE dl.contact_id = %s
                ORDER BY dl.decided_at DESC
                LIMIT %s
                """,
                (contact_id, limit),
            )
            rows = cur.fetchall()
            result = [{k: str(v) for k, v in dict(r).items()} for r in rows]
            return json.dumps({"contact_id": contact_id, "decisions": result}, indent=2)
