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
            cur.execute("SELECT get_contact_detail(%s)", (email_or_id,))
            result = cur.fetchone()["get_contact_detail"]
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
        with get_cursor(commit=False) as cur:
            cur.execute(
                "SELECT * FROM search_contacts(%s, %s, %s, %s, %s, %s)",
                (lifecycle_segment, email_promo_enabled, sms_promo_enabled, min_orders, max_orders, limit),
            )
            rows = cur.fetchall()
            result = [{k: str(v) if v is not None else None for k, v in dict(r).items()} for r in rows]
            return json.dumps({"count": len(result), "contacts": result}, indent=2)
