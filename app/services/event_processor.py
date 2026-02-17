"""
Event processing service — handles event ingestion with validation.
Used by the events router. Wraps the Postgres stored proc with Python-side checks.
"""

import json

from app.db import get_cursor

VALID_EVENT_TYPES = {
    "email_open",
    "email_click",
    "sms_sent",
    "sms_received",
    "sms_click",
    "call_completed",
    "order_placed",
    "unsubscribe",
    "sms_stop",
    "delivery_update",
}


def process_event(contact_email: str, event_type: str, metadata: dict | None = None) -> int:
    if event_type not in VALID_EVENT_TYPES:
        raise ValueError(f"Invalid event type: {event_type}. Must be one of: {VALID_EVENT_TYPES}")

    with get_cursor() as cur:
        cur.execute(
            "SELECT ingest_event(%s, %s::event_type, %s::jsonb)",
            (contact_email, event_type, json.dumps(metadata or {})),
        )
        row = cur.fetchone()
        return row["ingest_event"]
