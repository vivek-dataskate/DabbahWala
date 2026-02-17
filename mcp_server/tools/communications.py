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
            # SMS messages
            cur.execute(
                """
                SELECT id, direction, from_number, to_number, body, status,
                       is_delivery_staff, sent_at
                FROM telnyx_messages
                WHERE contact_id = %s AND sent_at > now() - (%s || ' days')::interval
                ORDER BY sent_at DESC
                """,
                (contact_id, str(days)),
            )
            messages = [{k: str(v) if v is not None else None for k, v in dict(r).items()} for r in cur.fetchall()]

            # Voice calls
            cur.execute(
                """
                SELECT id, direction, from_number, to_number, duration_sec,
                       transcript, summary, is_delivery_staff, started_at, ended_at
                FROM telnyx_calls
                WHERE contact_id = %s AND started_at > now() - (%s || ' days')::interval
                ORDER BY started_at DESC
                """,
                (contact_id, str(days)),
            )
            calls = [{k: str(v) if v is not None else None for k, v in dict(r).items()} for r in cur.fetchall()]

            # Delivery status
            cur.execute(
                """
                SELECT id, order_ref, status, updated_by, notes, location, occurred_at
                FROM delivery_status
                WHERE contact_id = %s AND occurred_at > now() - (%s || ' days')::interval
                ORDER BY occurred_at DESC
                """,
                (contact_id, str(days)),
            )
            deliveries = [{k: str(v) if v is not None else None for k, v in dict(r).items()} for r in cur.fetchall()]

            result = {
                "contact_id": contact_id,
                "days": days,
                "sms_messages": messages,
                "voice_calls": calls,
                "delivery_updates": deliveries,
            }
            return json.dumps(result, indent=2)

    @mcp.tool()
    def get_delivery_tracking(contact_id: int) -> str:
        """Get delivery status history for a contact's orders.

        Args:
            contact_id: The contact ID
        """
        with get_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT ds.id, ds.order_ref, ds.status, ds.updated_by,
                       ds.notes, ds.location, ds.occurred_at
                FROM delivery_status ds
                WHERE ds.contact_id = %s
                ORDER BY ds.occurred_at DESC
                LIMIT 50
                """,
                (contact_id,),
            )
            rows = [{k: str(v) if v is not None else None for k, v in dict(r).items()} for r in cur.fetchall()]
            return json.dumps({"contact_id": contact_id, "deliveries": rows}, indent=2)
