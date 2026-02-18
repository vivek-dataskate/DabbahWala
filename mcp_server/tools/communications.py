"""MCP tools for communication history — SMS, calls, transcripts, delivery status."""

import json

from app.db import get_cursor


def register_communications_tools(mcp):
    @mcp.tool()
    def get_communication_history(contact_id: int, days: int = 30) -> str:
        """Get all SMS messages, voice calls (with transcripts), and delivery status for a contact.

        Args:
            contact_id: The contact ID
            days: Lookback period in days (default 30)
        """
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT get_communication_history(%s, %s)", (contact_id, days))
            result = cur.fetchone()["get_communication_history"]
            return json.dumps(result, indent=2)

    @mcp.tool()
    def get_delivery_tracking(contact_id: int) -> str:
        """Get delivery status history for a contact's orders.

        Args:
            contact_id: The contact ID
        """
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT * FROM get_delivery_tracking(%s)", (contact_id,))
            rows = [{k: str(v) if v is not None else None for k, v in dict(r).items()} for r in cur.fetchall()]
            return json.dumps({"contact_id": contact_id, "deliveries": rows}, indent=2)
