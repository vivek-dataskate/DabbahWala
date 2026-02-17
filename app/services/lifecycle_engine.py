"""
Lifecycle engine service — runs the full lifecycle evaluation cycle.
Wraps the Postgres stored procs with Python-side orchestration.
"""

from app.db import get_cursor


def run_full_cycle() -> dict:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM run_lifecycle_cycle()")
        row = cur.fetchone()
        return {
            "contacts_updated": row["contacts_updated"],
            "campaigns_queued": row["campaigns_queued"],
        }


def evaluate_single_contact(contact_id: int) -> int:
    with get_cursor() as cur:
        cur.execute("SELECT evaluate_rules(%s)", (contact_id,))
        row = cur.fetchone()
        return row["evaluate_rules"]
