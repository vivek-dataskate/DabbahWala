"""
Airtable sync service — creates and updates records in Airtable for field sales tasks.
Uses the Airtable REST API via httpx.
"""

import httpx

from app.config import AIRTABLE_API_KEY, AIRTABLE_BASE_ID, AIRTABLE_FIELD_SALES_TABLE

AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_FIELD_SALES_TABLE}"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json",
    }


def create_field_sales_task(opportunity: dict) -> str:
    """Create an Airtable record for a field sales task. Returns the Airtable record ID."""
    fields = {
        "Customer Name": f"{opportunity.get('first_name', '')} {opportunity.get('last_name', '')}".strip(),
        "Phone": opportunity.get("phone", ""),
        "Email": opportunity.get("email", ""),
        "Priority": opportunity.get("priority", "warm").capitalize(),
        "Reason": opportunity.get("reason", ""),
        "Suggested Action": opportunity.get("suggested_message", ""),
        "Lifecycle Stage": opportunity.get("lifecycle_segment", ""),
        "Total Orders": opportunity.get("total_orders", 0),
        "Postgres Opportunity ID": opportunity.get("id"),
        "Status": "New",
    }

    if opportunity.get("last_order_at"):
        fields["Last Order"] = str(opportunity["last_order_at"])[:10]

    response = httpx.post(
        AIRTABLE_URL,
        headers=_headers(),
        json={"fields": fields},
    )
    response.raise_for_status()
    return response.json()["id"]


def get_updated_tasks(since_formula: str | None = None) -> list[dict]:
    """Fetch tasks from Airtable that have been updated (for bidirectional sync)."""
    params = {}
    if since_formula:
        params["filterByFormula"] = since_formula

    response = httpx.get(AIRTABLE_URL, headers=_headers(), params=params)
    response.raise_for_status()
    return response.json().get("records", [])
