"""
/api/query — Marketing intelligence query endpoint.

Routes form submissions from the DabbahWala Marketing Intelligence n8n form
to the appropriate data handler and returns a formatted plain-text answer
in `formatted_answer` (consumed directly by the n8n form response node).
"""

import json
import os
from datetime import datetime, timezone
from typing import Optional

import anthropic
from fastapi import APIRouter
from pydantic import BaseModel

from app.db import get_cursor

router = APIRouter()

MODEL = "claude-sonnet-4-5-20250929"


# ---------------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    category: str
    question: str = ""
    contact_email: Optional[str] = None


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _pipeline_snapshot() -> str:
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT * FROM get_lifecycle_summary()")
        rows = cur.fetchall()
    total = sum(r["count"] for r in rows)
    lines = [f"Pipeline Snapshot — {total:,} total contacts\n"]
    for r in rows:
        pct = round(r["count"] / total * 100) if total else 0
        lines.append(f"  {r['lifecycle_segment']:<25} {r['count']:>5}  ({pct}%)")
    return "\n".join(lines)


def _who_to_contact() -> str:
    with get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT c.first_name, c.last_name, c.email, c.phone,
                   c.lifecycle_segment,
                   COALESCE(er.opens_30d, 0) AS opens_30d,
                   COALESCE(er.clicks_30d, 0) AS clicks_30d,
                   (SELECT goal FROM customer_goals g
                    WHERE g.contact_id = c.id AND g.status = 'active'
                    ORDER BY g.created_at DESC LIMIT 1) AS active_goal
            FROM contacts c
            LEFT JOIN engagement_rollups er ON er.contact_id = c.id
            WHERE c.lifecycle_segment NOT IN ('optout')
              AND (
                  EXISTS (
                      SELECT 1 FROM customer_goals g
                      WHERE g.contact_id = c.id AND g.status = 'active'
                  )
                  OR er.opens_30d > 0
              )
              AND NOT EXISTS (
                  SELECT 1 FROM action_queue aq
                  WHERE aq.contact_id = c.id
                    AND aq.created_at > now() - interval '24 hours'
              )
            ORDER BY
                (EXISTS (
                    SELECT 1 FROM customer_goals g
                    WHERE g.contact_id = c.id AND g.status = 'active'
                )) DESC,
                (COALESCE(er.opens_30d, 0) + COALESCE(er.clicks_30d, 0) * 3) DESC
            LIMIT 20
        """)
        rows = cur.fetchall()

    if not rows:
        return "No contacts to prioritize right now — everyone was contacted in the last 24 hours."

    lines = [f"Who to Contact Today — {len(rows)} prioritized contacts\n"]
    for r in rows:
        name = f"{r['first_name'] or ''} {r['last_name'] or ''}".strip()
        contact_info = r["phone"] or r["email"] or "N/A"
        goal_note = f"  Goal: {r['active_goal'][:60]}" if r["active_goal"] else ""
        lines.append(
            f"  {name:<25}  {contact_info:<32}  [{r['lifecycle_segment']}]{goal_note}"
        )
    return "\n".join(lines)


def _daily_summary() -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT event_type, COUNT(*) AS count
            FROM events
            WHERE DATE(occurred_at) = %s::date
            GROUP BY event_type ORDER BY count DESC
        """, (today,))
        event_rows = cur.fetchall()

        cur.execute("""
            SELECT COUNT(*) AS c, COALESCE(SUM(total_amount), 0) AS rev
            FROM orders WHERE order_date = %s::date
        """, (today,))
        order_row = cur.fetchone()

        cur.execute("""
            SELECT direction, COUNT(*) AS c FROM telnyx_tracking
            WHERE DATE(created_at) = %s::date GROUP BY direction
        """, (today,))
        sms_rows = cur.fetchall()

    lines = [f"Daily Summary — {today}\n"]

    if event_rows:
        lines.append("Events today:")
        for r in event_rows:
            lines.append(f"  {r['event_type']:<25} {r['count']}")
    else:
        lines.append("  No events recorded today.")

    orders_count = order_row["c"] if order_row else 0
    orders_rev = order_row["rev"] if order_row else 0
    lines.append(f"\nOrders today: {orders_count}   Revenue: AED {orders_rev:,.2f}")

    if sms_rows:
        lines.append("\nSMS / Calls:")
        for r in sms_rows:
            lines.append(f"  {r['direction']:<12} {r['c']}")

    return "\n".join(lines)


def _order_analytics() -> str:
    with get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT COUNT(*) AS orders, COALESCE(SUM(total_amount), 0) AS revenue
            FROM orders
            WHERE order_date >= CURRENT_DATE - INTERVAL '30 days'
        """)
        totals = cur.fetchone()

        cur.execute("""
            SELECT source, COUNT(*) AS orders, COALESCE(SUM(total_amount), 0) AS revenue
            FROM orders
            WHERE order_date >= CURRENT_DATE - INTERVAL '30 days'
            GROUP BY source ORDER BY revenue DESC
        """)
        by_source = cur.fetchall()

        cur.execute("""
            SELECT oi.item_name,
                   SUM(oi.quantity) AS qty,
                   COALESCE(SUM(oi.line_total), 0) AS revenue
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            WHERE o.order_date >= CURRENT_DATE - INTERVAL '30 days'
            GROUP BY oi.item_name ORDER BY qty DESC LIMIT 10
        """)
        dishes = cur.fetchall()

    lines = ["Order Analytics — Last 30 Days\n"]
    lines.append(
        f"Total orders: {totals['orders']:,}   Revenue: AED {totals['revenue']:,.2f}\n"
    )

    if by_source:
        lines.append("By source:")
        for r in by_source:
            lines.append(
                f"  {r['source']:<28} {r['orders']:>4} orders   AED {r['revenue']:,.2f}"
            )

    if dishes:
        lines.append("\nTop dishes:")
        for r in dishes:
            lines.append(
                f"  {r['item_name']:<38} {r['qty']:>4} sold   AED {r['revenue']:,.2f}"
            )

    return "\n".join(lines)


def _campaign_performance() -> str:
    campaigns = [
        "NURTURE_SLOW",
        "PROMO_STANDARD",
        "PROMO_AGGRESSIVE",
        "NEW_CUSTOMER_ONBOARDING",
        "REACTIVATION",
    ]
    lines = ["Campaign Performance — Last 7 Days\n"]
    with get_cursor(commit=False) as cur:
        for campaign in campaigns:
            cur.execute("SELECT get_campaign_performance(%s, 7)", (campaign,))
            row = cur.fetchone()
            data = list(row.values())[0] if row else {}
            if isinstance(data, str):
                data = json.loads(data)
            if not isinstance(data, dict):
                data = {}
            contacts = data.get("contacts_in_campaign", 0)
            activity = data.get("activity", {})
            opens = activity.get("email_open", 0)
            clicks = activity.get("email_click", 0)
            orders = activity.get("order_placed", 0)
            lines.append(
                f"  {campaign:<28} contacts: {contacts:>4}  "
                f"opens: {opens:>3}  clicks: {clicks:>3}  orders: {orders:>3}"
            )
    return "\n".join(lines)


def _customer_lookup(email: Optional[str]) -> str:
    if not email:
        return "Please provide a customer email in the 'Customer Email' field to look up a customer."

    with get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT c.first_name, c.last_name, c.email, c.phone,
                   c.lifecycle_segment, c.current_campaign,
                   c.total_orders, c.last_order_date, c.subscription_type,
                   er.opens_7d, er.opens_30d, er.clicks_7d, er.clicks_30d,
                   er.sms_sent_30d, er.orders_90d
            FROM contacts c
            LEFT JOIN engagement_rollups er ON er.contact_id = c.id
            WHERE c.email = %s
        """, (email,))
        contact = cur.fetchone()

    if not contact:
        return f"No customer found with email: {email}"

    c = dict(contact)
    name = f"{c.get('first_name') or ''} {c.get('last_name') or ''}".strip()
    lines = [
        f"Customer: {name}",
        f"Email:    {c.get('email')}",
        f"Phone:    {c.get('phone') or 'N/A'}",
        f"Segment:  {c.get('lifecycle_segment')}",
        f"Campaign: {c.get('current_campaign') or 'none'}",
        f"Sub type: {c.get('subscription_type') or 'N/A'}",
        f"",
        f"Orders:   {c.get('total_orders', 0)} total  |  {c.get('orders_90d', 0)} in last 90d",
        f"Last order: {c.get('last_order_date') or 'never'}",
        f"",
        f"Engagement 30d: opens={c.get('opens_30d', 0)}  clicks={c.get('clicks_30d', 0)}  sms={c.get('sms_sent_30d', 0)}",
        f"Engagement  7d: opens={c.get('opens_7d', 0)}   clicks={c.get('clicks_7d', 0)}",
    ]
    return "\n".join(lines)


def _communication_history(email: Optional[str]) -> str:
    if not email:
        return "Please provide a customer email in the 'Customer Email' field to view communication history."

    with get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT tt.direction, tt.body, tt.summary, tt.created_at, tt.duration_sec
            FROM telnyx_tracking tt
            JOIN contacts c ON c.id = tt.contact_id
            WHERE c.email = %s
            ORDER BY tt.created_at DESC LIMIT 20
        """, (email,))
        rows = cur.fetchall()

    if not rows:
        return f"No communication history found for {email}."

    lines = [f"Communication History for {email}\n"]
    for r in rows:
        ts = r["created_at"].strftime("%Y-%m-%d %H:%M") if r["created_at"] else "?"
        text = (r["summary"] or r["body"] or "").replace("\n", " ")[:100]
        duration = f"  ({r['duration_sec']}s)" if r["duration_sec"] else ""
        lines.append(f"  [{ts}] {r['direction']:<8}{duration}  {text}")
    return "\n".join(lines)


def _free_form(question: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "AI assistant is not configured (ANTHROPIC_API_KEY missing)."
    if not question.strip():
        return "Please describe your question in the 'Your Question or Details' field."

    with get_cursor(commit=False) as cur:
        cur.execute("SELECT * FROM get_lifecycle_summary()")
        segments = {r["lifecycle_segment"]: r["count"] for r in cur.fetchall()}

        cur.execute("""
            SELECT COUNT(*) AS orders, COALESCE(SUM(total_amount), 0) AS revenue
            FROM orders WHERE order_date >= CURRENT_DATE - INTERVAL '30 days'
        """)
        order_row = cur.fetchone()

    context = (
        f"Pipeline segments: {json.dumps(dict(segments))}\n"
        f"Orders last 30d: {order_row['orders']}  "
        f"Revenue: AED {order_row['revenue']:,.2f}"
    )

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=MODEL,
        max_tokens=800,
        system=(
            "You are a marketing intelligence assistant for DabbahWala, a food delivery "
            "business in the UAE. Answer questions using the provided data context. "
            "Be concise and factual. If the available data is insufficient to answer "
            "precisely, say so clearly and suggest what to check."
        ),
        messages=[{
            "role": "user",
            "content": f"Data context:\n{context}\n\nQuestion: {question}",
        }],
    )
    return response.content[0].text if response.content else "No answer generated."


# ---------------------------------------------------------------------------
# Main route
# ---------------------------------------------------------------------------

@router.post("")
def handle_query(req: QueryRequest):
    """
    Central query handler for the DabbahWala Marketing Intelligence n8n form.
    Accepts category + optional question/contact_email and returns formatted_answer.
    """
    category = req.category
    question = req.question or ""
    email = req.contact_email

    if category == "pipeline_snapshot":
        answer = _pipeline_snapshot()
    elif category == "who_to_contact":
        answer = _who_to_contact()
    elif category == "daily_summary":
        answer = _daily_summary()
    elif category == "order_analytics":
        answer = _order_analytics()
    elif category == "campaign_performance":
        answer = _campaign_performance()
    elif category == "customer_lookup":
        answer = _customer_lookup(email)
    elif category == "communication_history":
        answer = _communication_history(email)
    else:
        answer = _free_form(question)

    return {"formatted_answer": answer, "category": category}
