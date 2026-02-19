"""
Airtable sync service — creates and updates records in Airtable for field sales tasks.
Uses the Airtable REST API via httpx.
"""

import httpx

from app.config import AIRTABLE_API_KEY, AIRTABLE_BASE_ID, AIRTABLE_FIELD_SALES_TABLE, AIRTABLE_QUERY_LOG_TABLE

AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_FIELD_SALES_TABLE}"
AIRTABLE_QUERY_LOG_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_QUERY_LOG_TABLE}"


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


def log_query_to_airtable(category: str, question: str, answer: str, contact_email: str | None = None) -> None:
    """
    Persist a marketing query (category, question, answer) to the Airtable Query Log table.
    Silently no-ops if Airtable is not configured, so it never breaks the query response.
    """
    if not AIRTABLE_API_KEY or not AIRTABLE_BASE_ID:
        return
    fields = {
        "Category":      category,
        "Question":      question[:100000],   # Airtable long-text limit
        "Answer":        answer[:100000],
    }
    if contact_email:
        fields["Customer Email"] = contact_email
    try:
        httpx.post(
            AIRTABLE_QUERY_LOG_URL,
            headers=_headers(),
            json={"fields": fields},
            timeout=8,
        )
    except Exception:
        pass  # never let Airtable failure affect the user response


def get_updated_tasks(since_formula: str | None = None) -> list[dict]:
    """Fetch tasks from Airtable that have been updated (for bidirectional sync)."""
    params = {}
    if since_formula:
        params["filterByFormula"] = since_formula

    response = httpx.get(AIRTABLE_URL, headers=_headers(), params=params)
    response.raise_for_status()
    return response.json().get("records", [])
