"""
Airtable sync service — enqueues Airtable write actions to the action_queue table.
n8n's Action Queue Executor picks them up and calls the Airtable API directly,
keeping all external HTTP calls out of Python.
"""

import json
import logging

from app.db import get_cursor

logger = logging.getLogger(__name__)


def create_field_sales_task(opportunity: dict) -> None:
    """Enqueue an Airtable field sales task creation to the action_queue.

    n8n will pick this up via the Action Queue Executor and create the record.
    """
    contact_id = opportunity.get("id")
    if not contact_id:
        logger.warning("create_field_sales_task called with no opportunity id — skipped")
        return

    payload = {
        "customer_name": f"{opportunity.get('first_name', '')} {opportunity.get('last_name', '')}".strip(),
        "phone": opportunity.get("phone", ""),
        "email": opportunity.get("email", ""),
        "priority": opportunity.get("priority", "warm").capitalize(),
        "reason": opportunity.get("reason", ""),
        "suggested_action": opportunity.get("suggested_message", ""),
        "action_type": opportunity.get("action_type", ""),
        "lifecycle_stage": opportunity.get("lifecycle_segment", ""),
        "total_orders": opportunity.get("total_orders", 0),
        "last_order": str(opportunity["last_order_at"])[:10] if opportunity.get("last_order_at") else "",
        "postgres_opportunity_id": contact_id,
        "status": "New",
    }

    try:
        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO action_queue (contact_id, action_type, payload)
                VALUES (%s, 'sync_airtable_task', %s::jsonb)
                """,
                (contact_id, json.dumps(payload)),
            )
        logger.debug("Enqueued sync_airtable_task for contact_id=%s", contact_id)
    except Exception as e:
        logger.warning("Failed to enqueue Airtable task for contact_id=%s: %s", contact_id, e)


