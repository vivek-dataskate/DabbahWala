"""MCP tools for contact lookup and search."""

import json

from app.db import get_cursor


def register_contacts_tools(mcp):
    @mcp.tool()
    def get_contact_detail(email_or_id: str) -> str:
        """Get full contact record with recent events, communications, and lifecycle history.

        Args:
            email_or_id: Contact email address or numeric ID
        """
        with get_cursor(commit=False) as cur:
            # Find contact
            if email_or_id.isdigit():
                cur.execute("SELECT * FROM contacts WHERE id = %s", (int(email_or_id),))
            else:
                cur.execute("SELECT * FROM contacts WHERE email = %s", (email_or_id,))

            contact = cur.fetchone()
            if not contact:
                return json.dumps({"error": f"Contact not found: {email_or_id}"})

            contact_id = contact["id"]

            # Recent events (last 30 days)
            cur.execute(
                """
                SELECT event_type, occurred_at, metadata
                FROM events WHERE contact_id = %s AND occurred_at > now() - interval '30 days'
                ORDER BY occurred_at DESC LIMIT 20
                """,
                (contact_id,),
            )
            events = [dict(r) for r in cur.fetchall()]

            # Recent decisions
            cur.execute(
                """
                SELECT r.rule_name, dl.prev_lifecycle, dl.new_lifecycle, dl.changes_applied, dl.decided_at
                FROM decision_log dl
                LEFT JOIN rules r ON r.id = dl.rule_id
                WHERE dl.contact_id = %s
                ORDER BY dl.decided_at DESC LIMIT 10
                """,
                (contact_id,),
            )
            decisions = [dict(r) for r in cur.fetchall()]

            # Engagement rollups
            cur.execute("SELECT * FROM engagement_rollups WHERE contact_id = %s", (contact_id,))
            rollups = dict(cur.fetchone()) if cur.rowcount else {}

            result = {
                "contact": {k: str(v) if v is not None else None for k, v in dict(contact).items()},
                "engagement_rollups": {k: str(v) if v is not None else None for k, v in rollups.items()},
                "recent_events": [{k: str(v) for k, v in e.items()} for e in events],
                "recent_decisions": [{k: str(v) for k, v in d.items()} for d in decisions],
            }
            return json.dumps(result, indent=2)

    @mcp.tool()
    def search_contacts(
        lifecycle_segment: str | None = None,
        email_promo_enabled: bool | None = None,
        sms_promo_enabled: bool | None = None,
        min_orders: int | None = None,
        max_orders: int | None = None,
        limit: int = 50,
    ) -> str:
        """Search contacts by lifecycle segment, channel flags, or order count.

        Args:
            lifecycle_segment: Filter by lifecycle (cold, engaged, active_customer, etc.)
            email_promo_enabled: Filter by email promo flag
            sms_promo_enabled: Filter by SMS promo flag
            min_orders: Minimum total orders
            max_orders: Maximum total orders
            limit: Max results to return (default 50)
        """
        conditions = []
        params = []

        if lifecycle_segment:
            conditions.append("lifecycle_segment = %s::lifecycle_segment")
            params.append(lifecycle_segment)
        if email_promo_enabled is not None:
            conditions.append("email_promo_enabled = %s")
            params.append(email_promo_enabled)
        if sms_promo_enabled is not None:
            conditions.append("sms_promo_enabled = %s")
            params.append(sms_promo_enabled)
        if min_orders is not None:
            conditions.append("total_orders >= %s")
            params.append(min_orders)
        if max_orders is not None:
            conditions.append("total_orders <= %s")
            params.append(max_orders)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(limit)

        with get_cursor(commit=False) as cur:
            cur.execute(
                f"""
                SELECT id, email, first_name, last_name, lifecycle_segment,
                       email_promo_enabled, sms_promo_enabled, sms_level,
                       current_campaign, total_orders, last_order_at
                FROM contacts {where}
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                params,
            )
            rows = cur.fetchall()
            result = [{k: str(v) if v is not None else None for k, v in dict(r).items()} for r in rows]
            return json.dumps({"count": len(result), "contacts": result}, indent=2)
