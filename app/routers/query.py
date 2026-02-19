"""
Marketing Query Form — Self-service intelligence interface.

Two-tier system:
  Tier 1: Pre-built categories that hit stored procs directly (fast, free)
  Tier 2: Free-form questions answered by Claude with real data context (slower, costs ~$0.02)

Called by n8n form workflow. Returns formatted text answers.
"""
import json
import os
import traceback
from datetime import date, datetime, timedelta

import anthropic
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_cursor
from app.services.airtable_sync import log_query_to_airtable

router = APIRouter()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-5-20250929"

CATEGORIES = {
    "customer_lookup": "Look up a specific customer by email",
    "pipeline_snapshot": "How many contacts are in each lifecycle stage",
    "campaign_performance": "How are email campaigns performing",
    "who_to_contact": "Which customers should we reach out to today",
    "daily_summary": "Today's or recent order/activity summary",
    "order_analytics": "Top dishes, revenue trends, order patterns",
    "communication_history": "SMS/call history for a specific customer",
    "ground_team_notes": "Browse recent field notes from the ground team",
    "ad_copies": "Browse recent social media ad copies",
    "submit_input": "Submit a new observation, note, or question",
    "free_form": "Ask any question about the marketing system",
}


class QueryRequest(BaseModel):
    category: str
    question: str = ""
    contact_email: str | None = None
    contact_phone: str | None = None
    contact_name: str | None = None
    # Used by submit_input category
    author: str | None = None
    input_type: str | None = None  # ground_note, ad_copy, observation, question


class QueryResponse(BaseModel):
    category: str
    question: str
    answer: str
    data: dict | None = None


# ---------------------------------------------------------------------------
# Tier 1 handlers — direct stored proc / SQL queries
# ---------------------------------------------------------------------------

def _handle_customer_lookup(question: str, contact_email: str | None) -> tuple[str, dict]:
    # If no email, try name search using the question text
    if not contact_email:
        name = question.strip()
        if not name:
            return "Please provide a customer email address or name to look up.", {}

        with get_cursor(commit=False) as cur:
            cur.execute("""
                SELECT id, first_name, last_name, email, lifecycle_segment, total_orders
                FROM contacts
                WHERE first_name ILIKE %s OR last_name ILIKE %s
                ORDER BY last_name, first_name
                LIMIT 10
            """, (f"%{name}%", f"%{name}%"))
            matches = [dict(r) for r in cur.fetchall()]

        if not matches:
            return f"No customer found with name matching '{name}'.", {}

        if len(matches) > 1:
            lines = [f"## {len(matches)} customers found for '{name}'", ""]
            for m in matches:
                lines.append(
                    f"- **{m['first_name']} {m['last_name']}** — {m['email']} "
                    f"({m.get('lifecycle_segment', 'N/A')}, {m.get('total_orders', 0)} orders)"
                )
            lines.append("\nEnter the email address to get full details.")
            return "\n".join(lines), {"matches": matches}

        # Exactly one match — use their email for full detail lookup
        contact_email = matches[0]["email"]

    with get_cursor(commit=False) as cur:
        cur.execute("SELECT get_contact_detail(%s)", (contact_email,))
        row = cur.fetchone()
        result = row.get("get_contact_detail") if row else None

    if not result:
        return f"No customer found with email: {contact_email}", {}

    c = result if isinstance(result, dict) else json.loads(result)
    contact = c.get("contact", {})
    events = c.get("recent_events", [])
    decisions = c.get("recent_decisions", [])
    rollup = c.get("engagement_rollup", {})

    lines = [
        f"## Customer: {contact.get('first_name', '')} {contact.get('last_name', '')}",
        f"**Email**: {contact.get('email', 'N/A')}",
        f"**Phone**: {contact.get('phone', 'N/A')}",
        f"**Lifecycle**: {contact.get('lifecycle_segment', 'N/A')}",
        f"**Total Orders**: {contact.get('total_orders', 0)}",
        f"**Last Order**: {contact.get('last_order_at', 'Never')}",
        f"**Source**: {contact.get('primary_source', 'N/A')}",
        f"**Subscription**: {contact.get('subscription_type', 'None')}",
        f"**Current Campaign**: {contact.get('current_campaign', 'None')}",
        "",
        "### Engagement (7-day)",
        f"- Opens: {rollup.get('opens_7d', 0)}",
        f"- Clicks: {rollup.get('clicks_7d', 0)}",
        f"- SMS Sent: {rollup.get('sms_sent_7d', 0)}",
        f"- Orders: {rollup.get('orders_7d', 0)}",
        "",
        "### Channel Flags",
        f"- Email Nurture: {'Yes' if contact.get('email_nurture_enabled') else 'No'}",
        f"- Email Promo: {'Yes' if contact.get('email_promo_enabled') else 'No'}",
        f"- SMS Promo: {'Yes' if contact.get('sms_promo_enabled') else 'No'}",
        f"- SMS Level: {contact.get('sms_level', 1)}",
    ]

    if events:
        lines.append("")
        lines.append(f"### Recent Events (last {len(events)})")
        for e in events[:10]:
            lines.append(f"- {e.get('event_type')} at {e.get('occurred_at', '')}")

    return "\n".join(lines), c


def _handle_pipeline_snapshot(question: str) -> tuple[str, dict]:
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT get_lifecycle_summary()")
        row = cur.fetchone()
        result = row.get("get_lifecycle_summary") if row else None

    # Parse result early so invalid/empty JSON falls through to the fallback
    if result and not isinstance(result, dict):
        try:
            result = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            result = None

    if not result:
        # Fallback: direct query
        with get_cursor(commit=False) as cur:
            cur.execute("""
                SELECT lifecycle_segment, count(*) as cnt
                FROM contacts GROUP BY lifecycle_segment ORDER BY cnt DESC
            """)
            segments = {r["lifecycle_segment"]: r["cnt"] for r in cur.fetchall()}

            cur.execute("SELECT count(*) as total FROM contacts")
            total = cur.fetchone()["total"]

            cur.execute("""
                SELECT count(*) as cnt FROM contacts WHERE email_promo_enabled = true
            """)
            email_promo = cur.fetchone()["cnt"]

            cur.execute("""
                SELECT count(*) as cnt FROM contacts WHERE sms_promo_enabled = true
            """)
            sms_promo = cur.fetchone()["cnt"]

        data = {"segments": segments, "total": total}
    else:
        data = result  # already a dict (parsed above or returned directly by psycopg2)
        segments = data.get("segments", data)
        total = sum(segments.values()) if isinstance(segments, dict) else 0
        email_promo = data.get("email_promo_count", "?")
        sms_promo = data.get("sms_promo_count", "?")

    lines = [
        "## Pipeline Snapshot",
        f"**Total Contacts**: {total}",
        "",
        "### Lifecycle Distribution",
    ]

    if isinstance(segments, dict):
        for seg, cnt in sorted(segments.items(), key=lambda x: -x[1]):
            pct = (cnt / total * 100) if total > 0 else 0
            bar = "█" * int(pct / 3)
            lines.append(f"- **{seg}**: {cnt} ({pct:.1f}%) {bar}")
    else:
        lines.append(str(segments))

    lines.extend([
        "",
        "### Channel Opt-Ins",
        f"- Email Promo Enabled: {email_promo}",
        f"- SMS Promo Enabled: {sms_promo}",
    ])

    return "\n".join(lines), data


def _handle_campaign_performance(question: str) -> tuple[str, dict]:
    with get_cursor(commit=False) as cur:
        # Get per-campaign stats from recent events
        cur.execute("""
            SELECT
                c.current_campaign,
                count(DISTINCT c.id) as contacts,
                count(DISTINCT CASE WHEN e.event_type = 'email_open' THEN e.id END) as opens,
                count(DISTINCT CASE WHEN e.event_type = 'email_click' THEN e.id END) as clicks,
                count(DISTINCT CASE WHEN e.event_type = 'order_placed' THEN e.id END) as orders
            FROM contacts c
            LEFT JOIN events e ON e.contact_id = c.id
                AND e.occurred_at > now() - interval '30 days'
            WHERE c.current_campaign IS NOT NULL
            GROUP BY c.current_campaign
            ORDER BY contacts DESC
        """)
        campaigns = [dict(r) for r in cur.fetchall()]

        # Pending campaign moves
        cur.execute("""
            SELECT to_campaign, count(*) as cnt
            FROM campaign_queue WHERE status = 'pending'
            GROUP BY to_campaign
        """)
        pending = {r["to_campaign"]: r["cnt"] for r in cur.fetchall()}

    lines = ["## Campaign Performance (Last 30 Days)", ""]

    if not campaigns:
        lines.append("No campaign data available yet.")
    else:
        for camp in campaigns:
            name = camp["current_campaign"] or "None"
            contacts = camp["contacts"]
            opens = camp["opens"]
            clicks = camp["clicks"]
            orders = camp["orders"]
            open_rate = (opens / contacts * 100) if contacts > 0 else 0
            click_rate = (clicks / contacts * 100) if contacts > 0 else 0

            lines.extend([
                f"### {name}",
                f"- Contacts: {contacts}",
                f"- Opens: {opens} ({open_rate:.1f}%)",
                f"- Clicks: {clicks} ({click_rate:.1f}%)",
                f"- Orders: {orders}",
                "",
            ])

    if pending:
        lines.extend(["### Pending Campaign Moves"])
        for camp, cnt in pending.items():
            lines.append(f"- {camp}: {cnt} pending")

    return "\n".join(lines), {"campaigns": campaigns, "pending": pending}


def _handle_who_to_contact(question: str) -> tuple[str, dict]:
    with get_cursor(commit=False) as cur:
        # Pending opportunities (prioritized)
        cur.execute("""
            SELECT o.id, o.action, o.priority, o.reason,
                   o.suggested_message, o.confidence_score,
                   c.first_name, c.last_name, c.email, c.phone,
                   c.lifecycle_segment, c.total_orders
            FROM opportunities o
            JOIN contacts c ON c.id = o.contact_id
            WHERE o.status = 'pending'
            ORDER BY
                CASE o.priority WHEN 'hot' THEN 1 WHEN 'warm' THEN 2 ELSE 3 END,
                o.confidence_score DESC
            LIMIT 25
        """)
        opps = [dict(r) for r in cur.fetchall()]

        # Reactivation targets
        try:
            cur.execute("SELECT * FROM suggest_reactivation_targets(10)")
            reactivation = [dict(r) for r in cur.fetchall()]
        except Exception:
            reactivation = []

    lines = ["## Who to Contact Today", ""]

    if opps:
        hot = [o for o in opps if o["priority"] == "hot"]
        warm = [o for o in opps if o["priority"] == "warm"]

        if hot:
            lines.append(f"### HOT Priority ({len(hot)} contacts)")
            for o in hot:
                name = f"{o.get('first_name', '')} {o.get('last_name', '')}".strip() or o.get("email", "Unknown")
                lines.extend([
                    f"- **{name}** ({o['action']})",
                    f"  Reason: {o['reason']}",
                    f"  Phone: {o.get('phone', 'N/A')} | Orders: {o.get('total_orders', 0)}",
                    f"  Confidence: {o.get('confidence_score', 0):.0%}",
                ])
                if o.get("suggested_message"):
                    lines.append(f"  Message: {o['suggested_message'][:120]}...")
                lines.append("")

        if warm:
            lines.append(f"### WARM Priority ({len(warm)} contacts)")
            for o in warm[:10]:
                name = f"{o.get('first_name', '')} {o.get('last_name', '')}".strip() or o.get("email", "Unknown")
                lines.append(f"- **{name}** — {o['reason'][:80]} ({o['action']})")
    else:
        lines.append("No pending opportunities right now. Run the intelligence cycle first.")

    if reactivation:
        lines.extend(["", f"### Reactivation Targets ({len(reactivation)})"])
        for r in reactivation:
            name = f"{r.get('first_name', '')} {r.get('last_name', '')}".strip()
            lines.append(f"- {name} — {r.get('total_orders', 0)} orders, last: {r.get('last_order_at', 'N/A')}")

    return "\n".join(lines), {"opportunities": len(opps), "reactivation": len(reactivation)}


def _handle_daily_summary(question: str) -> tuple[str, dict]:
    today = date.today()

    with get_cursor(commit=False) as cur:
        # Today's events
        cur.execute("""
            SELECT event_type, count(*) as cnt
            FROM events WHERE occurred_at::date = %s
            GROUP BY event_type ORDER BY cnt DESC
        """, (today,))
        events = {r["event_type"]: r["cnt"] for r in cur.fetchall()}

        # Today's orders
        cur.execute("""
            SELECT count(*) as order_count, coalesce(sum(total_amount), 0) as revenue
            FROM orders WHERE order_date::date = %s
        """, (today,))
        order_row = cur.fetchone()

        # This week's orders
        week_start = today - timedelta(days=today.weekday())
        cur.execute("""
            SELECT count(*) as order_count, coalesce(sum(total_amount), 0) as revenue
            FROM orders WHERE order_date::date >= %s
        """, (week_start,))
        week_row = cur.fetchone()

        # Lifecycle transitions today
        cur.execute("""
            SELECT prev_lifecycle, new_lifecycle, count(*) as cnt
            FROM decision_log WHERE decided_at::date = %s
            GROUP BY prev_lifecycle, new_lifecycle ORDER BY cnt DESC
        """, (today,))
        transitions = [dict(r) for r in cur.fetchall()]

        # Pending actions
        cur.execute("SELECT count(*) as cnt FROM opportunities WHERE status = 'pending'")
        pending_opps = cur.fetchone()["cnt"]

        cur.execute("SELECT count(*) as cnt FROM campaign_queue WHERE status = 'pending'")
        pending_campaigns = cur.fetchone()["cnt"]

    lines = [
        f"## Daily Summary — {today.strftime('%A, %B %d, %Y')}",
        "",
        "### Orders",
        f"- Today: **{order_row['order_count']} orders** (${float(order_row['revenue']):.2f} revenue)",
        f"- This week: **{week_row['order_count']} orders** (${float(week_row['revenue']):.2f} revenue)",
        "",
        "### Events Today",
    ]

    if events:
        for et, cnt in events.items():
            lines.append(f"- {et}: {cnt}")
    else:
        lines.append("- No events recorded today yet.")

    if transitions:
        lines.extend(["", "### Lifecycle Transitions Today"])
        for t in transitions:
            lines.append(f"- {t['prev_lifecycle']} → {t['new_lifecycle']}: {t['cnt']} contacts")

    lines.extend([
        "",
        "### Pending Actions",
        f"- Opportunities: {pending_opps}",
        f"- Campaign Moves: {pending_campaigns}",
    ])

    data = {
        "date": str(today),
        "orders_today": order_row["order_count"],
        "revenue_today": float(order_row["revenue"]),
        "events": events,
    }
    return "\n".join(lines), data


def _handle_order_analytics(question: str) -> tuple[str, dict]:
    with get_cursor(commit=False) as cur:
        # Top dishes by quantity
        cur.execute("""
            SELECT oi.item_name, sum(oi.quantity) as total_qty,
                   count(DISTINCT o.id) as order_count,
                   sum(oi.line_total) as total_revenue
            FROM order_items oi JOIN orders o ON o.id = oi.order_id
            GROUP BY oi.item_name ORDER BY total_qty DESC LIMIT 15
        """)
        top_dishes = [dict(r) for r in cur.fetchall()]

        # Orders per day (last 14 days)
        cur.execute("""
            SELECT order_date::date as day, count(*) as orders,
                   coalesce(sum(total_amount), 0) as revenue
            FROM orders
            WHERE order_date > now() - interval '14 days'
            GROUP BY day ORDER BY day DESC
        """)
        daily = [dict(r) for r in cur.fetchall()]

        # Avg order value
        cur.execute("""
            SELECT avg(total_amount) as avg_order, count(*) as total_orders
            FROM orders WHERE total_amount > 0
        """)
        avg_row = cur.fetchone()

        # Repeat rate
        cur.execute("""
            SELECT
                count(*) as total_customers,
                count(*) FILTER (WHERE order_count > 1) as repeat_customers
            FROM (
                SELECT contact_id, count(*) as order_count
                FROM orders GROUP BY contact_id
            ) sub
        """)
        repeat_row = cur.fetchone()

        # Source breakdown
        cur.execute("""
            SELECT source, count(*) as cnt
            FROM orders GROUP BY source ORDER BY cnt DESC
        """)
        sources = [dict(r) for r in cur.fetchall()]

    lines = [
        "## Order Analytics",
        "",
        "### Key Metrics",
        f"- Total Orders: {avg_row['total_orders']}",
        f"- Avg Order Value: ${float(avg_row['avg_order'] or 0):.2f}",
        f"- Total Customers: {repeat_row['total_customers']}",
        f"- Repeat Customers: {repeat_row['repeat_customers']} "
        f"({repeat_row['repeat_customers'] / max(repeat_row['total_customers'], 1) * 100:.1f}%)",
        "",
        "### Top 15 Dishes (All Time)",
    ]

    for d in top_dishes:
        lines.append(
            f"- **{d['item_name']}**: {d['total_qty']} qty, "
            f"{d['order_count']} orders, ${float(d.get('total_revenue', 0) or 0):.2f}"
        )

    if daily:
        lines.extend(["", "### Daily Orders (Last 14 Days)"])
        for d in daily:
            lines.append(f"- {d['day']}: {d['orders']} orders, ${float(d['revenue']):.2f}")

    if sources:
        lines.extend(["", "### Order Sources"])
        for s in sources:
            lines.append(f"- {s['source'] or 'Unknown'}: {s['cnt']} orders")

    data = {
        "top_dishes": top_dishes,
        "avg_order_value": float(avg_row["avg_order"] or 0),
        "total_orders": avg_row["total_orders"],
        "repeat_rate": repeat_row["repeat_customers"] / max(repeat_row["total_customers"], 1),
    }
    return "\n".join(lines), data


def _handle_communication_history(
    question: str,
    contact_email: str | None,
    contact_phone: str | None = None,
    contact_name: str | None = None,
) -> tuple[str, dict]:
    if not contact_email and not contact_phone and not contact_name:
        return (
            "Please provide at least one of: phone number, email address, or customer name "
            "to look up communication history.",
            {},
        )

    with get_cursor(commit=False) as cur:
        contact = None

        if contact_phone:
            raw_phone = contact_phone.strip()
            cur.execute(
                "SELECT id, first_name, last_name, email, phone FROM contacts WHERE phone = %s",
                (raw_phone,),
            )
            contact = cur.fetchone()
            if not contact:
                return f"No customer found with phone number: {raw_phone}", {}

        elif contact_email:
            cur.execute(
                "SELECT id, first_name, last_name, email, phone FROM contacts WHERE email = %s",
                (contact_email,),
            )
            contact = cur.fetchone()
            if not contact:
                return f"No customer found with email: {contact_email}", {}

        else:  # name search
            name_q = contact_name.strip()
            cur.execute("""
                SELECT id, first_name, last_name, email, phone
                FROM contacts
                WHERE first_name ILIKE %s OR last_name ILIKE %s
                ORDER BY last_name, first_name
                LIMIT 10
            """, (f"%{name_q}%", f"%{name_q}%"))
            matches = cur.fetchall()
            if not matches:
                return f"No customer found with name matching '{name_q}'.", {}
            if len(matches) > 1:
                lines = [f"## {len(matches)} customers found for '{name_q}'", ""]
                for m in matches:
                    lines.append(
                        f"- **{m['first_name']} {m['last_name']}** — "
                        f"{m.get('email') or 'no email'} / {m.get('phone') or 'no phone'}"
                    )
                lines.append("\nPlease refine your search using an email or phone number.")
                return "\n".join(lines), {"matches": [dict(m) for m in matches]}
            contact = matches[0]

        cid = contact["id"]
        name = f"{contact['first_name'] or ''} {contact['last_name'] or ''}".strip()
        phone = (contact.get("phone") or "").strip() or None
        email = contact.get("email") or ""

        if not phone:
            return (
                f"No phone number on file for **{name}** ({email}). "
                "Communication history via SMS/calls cannot be retrieved without a phone number.",
                {"phone_resolved": False},
            )

        # SMS
        cur.execute("""
            SELECT direction, body, status, sent_at
            FROM telnyx_messages WHERE contact_id = %s
            ORDER BY sent_at DESC LIMIT 20
        """, (cid,))
        sms = [dict(r) for r in cur.fetchall()]

        # Calls
        cur.execute("""
            SELECT direction, duration_sec, transcript, summary, started_at
            FROM telnyx_calls WHERE contact_id = %s
            ORDER BY started_at DESC LIMIT 10
        """, (cid,))
        calls = [dict(r) for r in cur.fetchall()]

        # Delivery notes
        cur.execute("""
            SELECT status, notes, updated_by, occurred_at
            FROM delivery_status WHERE contact_id = %s
            ORDER BY occurred_at DESC LIMIT 10
        """, (cid,))
        deliveries = [dict(r) for r in cur.fetchall()]

    lines = [f"## Communication History: {name}", f"**Email**: {email} | **Phone**: {phone}", ""]

    if sms:
        lines.append(f"### SMS Messages ({len(sms)})")
        for s in sms:
            direction = "→ OUT" if s["direction"] == "outbound" else "← IN"
            lines.append(f"- [{s['sent_at']}] {direction}: {(s.get('body') or 'N/A')[:120]}")
        lines.append("")

    if calls:
        lines.append(f"### Voice Calls ({len(calls)})")
        for c in calls:
            direction = "→ OUT" if c["direction"] == "outbound" else "← IN"
            duration = f"{c.get('duration_sec', 0)}s" if c.get("duration_sec") else "N/A"
            lines.append(f"- [{c['started_at']}] {direction} ({duration})")
            if c.get("summary"):
                lines.append(f"  Summary: {c['summary'][:150]}")
            if c.get("transcript"):
                lines.append(f"  Transcript: {c['transcript'][:200]}...")
        lines.append("")

    if deliveries:
        lines.append(f"### Delivery Updates ({len(deliveries)})")
        for d in deliveries:
            lines.append(f"- [{d['occurred_at']}] {d['status']} by {d.get('updated_by', 'N/A')}")
            if d.get("notes"):
                lines.append(f"  Notes: {d['notes'][:120]}")

    if not sms and not calls and not deliveries:
        lines.append("No communication history found for this customer.")

    data = {"phone": phone, "email": email, "sms_count": len(sms), "calls_count": len(calls), "deliveries_count": len(deliveries)}
    return "\n".join(lines), data


# ---------------------------------------------------------------------------
# Tier 1 handlers — team content (ground notes, ad copies, submissions)
# ---------------------------------------------------------------------------

def _handle_ground_team_notes(question: str) -> tuple[str, dict]:
    """Browse and search ground team field notes."""
    with get_cursor(commit=False) as cur:
        if question and len(question) > 3:
            cur.execute(
                "SELECT * FROM search_team_content('ground_note', %s, 15, 0)",
                (question,)
            )
        else:
            cur.execute(
                "SELECT * FROM get_recent_team_content('ground_note', 30, 15)"
            )
        results = [dict(r) for r in cur.fetchall()]

    if not results:
        return "No ground team notes found. Notes synced from Google Docs will appear here.", {}

    lines = ["## Ground Team Field Notes", ""]
    for r in results:
        date_str = r.get("created_at", "")
        if hasattr(date_str, "strftime"):
            date_str = date_str.strftime("%b %d, %Y")
        author = r.get("author", "Team")
        title = r.get("title", "Untitled")
        body = r.get("body_preview", r.get("body", ""))[:300]

        lines.extend([
            f"### {title}",
            f"**By**: {author} | **Date**: {date_str}",
            body,
            "",
        ])

    return "\n".join(lines), {"count": len(results)}


def _handle_ad_copies(question: str) -> tuple[str, dict]:
    """Browse and search social media ad copies."""
    with get_cursor(commit=False) as cur:
        if question and len(question) > 3:
            cur.execute(
                "SELECT * FROM search_team_content('ad_copy', %s, 15, 0)",
                (question,)
            )
        else:
            cur.execute(
                "SELECT * FROM get_recent_team_content('ad_copy', 30, 15)"
            )
        results = [dict(r) for r in cur.fetchall()]

    if not results:
        return "No ad copies found. Ad copies synced from Google Docs will appear here.", {}

    lines = ["## Social Media Ad Copies", ""]
    for r in results:
        date_str = r.get("created_at", "")
        if hasattr(date_str, "strftime"):
            date_str = date_str.strftime("%b %d, %Y")
        title = r.get("title", "Untitled")
        body = r.get("body_preview", r.get("body", ""))[:500]

        lines.extend([
            f"### {title}",
            f"**Date**: {date_str}",
            body,
            "",
        ])

    return "\n".join(lines), {"count": len(results)}


def _handle_submit_input(
    question: str,
    author: str | None,
    input_type: str | None,
) -> tuple[str, dict]:
    """Store a user-submitted observation/note."""
    if not question or len(question.strip()) < 5:
        return "Please provide the content of your observation, note, or question (at least 5 characters).", {}

    actual_type = input_type or "observation"
    actual_author = author or "Anonymous"
    title = (
        f"{actual_type.replace('_', ' ').title()} — "
        f"{datetime.now().strftime('%b %d %Y')}"
    )

    with get_cursor(commit=True) as cur:
        cur.execute(
            """INSERT INTO team_content
               (content_type, title, body, author, source)
               VALUES (%s, %s, %s, %s, 'form_submission')
               RETURNING id""",
            (actual_type, title, question.strip(), actual_author)
        )
        row = cur.fetchone()

    return (
        f"Your {actual_type.replace('_', ' ')} has been saved (ID: {row['id']}).\n\n"
        f"**Author**: {actual_author}\n"
        f"**Type**: {actual_type}\n"
        f"**Content**: {question[:200]}{'...' if len(question) > 200 else ''}\n\n"
        f"This will also be synced to Airtable for the team to review."
    ), {"id": row["id"], "type": actual_type, "author": actual_author}


# ---------------------------------------------------------------------------
# Tier 2: Free-form — Claude with real data context
# ---------------------------------------------------------------------------

async def _handle_free_form(question: str) -> tuple[str, dict]:
    """Answer any question by giving Claude real data context."""
    if not ANTHROPIC_API_KEY:
        return "AI queries require ANTHROPIC_API_KEY to be configured.", {}
    if not question.strip():
        return "Please type a question before submitting.", {}

    # Gather context data for Claude
    context_parts = []

    with get_cursor(commit=False) as cur:
        # Lifecycle summary
        cur.execute("""
            SELECT lifecycle_segment, count(*) as cnt
            FROM contacts GROUP BY lifecycle_segment ORDER BY cnt DESC
        """)
        segments = {r["lifecycle_segment"]: r["cnt"] for r in cur.fetchall()}
        context_parts.append(f"Lifecycle Distribution: {json.dumps(segments)}")

        # Recent order stats
        cur.execute("""
            SELECT count(*) as total_orders,
                   count(DISTINCT contact_id) as unique_customers,
                   coalesce(sum(total_amount), 0) as total_revenue,
                   coalesce(avg(total_amount), 0) as avg_order
            FROM orders WHERE order_date > now() - interval '30 days'
        """)
        order_stats = dict(cur.fetchone())
        order_stats = {k: float(v) if v is not None else 0 for k, v in order_stats.items()}
        context_parts.append(f"Last 30 Days Orders: {json.dumps(order_stats)}")

        # Top dishes
        cur.execute("""
            SELECT oi.item_name, sum(oi.quantity) as qty
            FROM order_items oi JOIN orders o ON o.id = oi.order_id
            WHERE o.order_date > now() - interval '30 days'
            GROUP BY oi.item_name ORDER BY qty DESC LIMIT 10
        """)
        top_dishes = {r["item_name"]: r["qty"] for r in cur.fetchall()}
        context_parts.append(f"Top Dishes (30d): {json.dumps(top_dishes)}")

        # Recent opportunities
        cur.execute("""
            SELECT action, priority, count(*) as cnt,
                   count(*) FILTER (WHERE status = 'completed' AND outcome = 'ordered') as converted
            FROM opportunities
            WHERE created_at > now() - interval '30 days'
            GROUP BY action, priority
        """)
        opp_stats = [dict(r) for r in cur.fetchall()]
        context_parts.append(f"Opportunity Stats (30d): {json.dumps(opp_stats, default=str)}")

        # Source breakdown
        cur.execute("""
            SELECT primary_source, count(*) as cnt
            FROM contacts WHERE primary_source IS NOT NULL
            GROUP BY primary_source
        """)
        sources = {r["primary_source"]: r["cnt"] for r in cur.fetchall()}
        context_parts.append(f"Customer Sources: {json.dumps(sources)}")

        # Playbook rules
        cur.execute("""
            SELECT category, rule_name, instruction
            FROM agent_playbook WHERE is_active = true
            ORDER BY priority DESC LIMIT 20
        """)
        rules = [dict(r) for r in cur.fetchall()]
        if rules:
            context_parts.append(f"Active Playbook Rules: {json.dumps(rules)}")

        # Recent team content (ground notes + ad copies)
        try:
            cur.execute("""
                SELECT content_type, title, LEFT(body, 300) as body_preview,
                       author, created_at
                FROM team_content
                WHERE is_active = true
                  AND created_at > now() - interval '14 days'
                ORDER BY created_at DESC
                LIMIT 10
            """)
            team_items = [dict(r) for r in cur.fetchall()]
            if team_items:
                team_context = []
                for t in team_items:
                    team_context.append({
                        "type": t["content_type"],
                        "title": t["title"],
                        "preview": t["body_preview"],
                        "author": t.get("author", "Unknown"),
                        "date": str(t["created_at"])[:10],
                    })
                context_parts.append(
                    f"Recent Team Content (ground notes, ad copies — last 14 days): "
                    f"{json.dumps(team_context, default=str)}"
                )
        except Exception:
            pass  # team_content table may not exist yet

    data_context = "\n".join(context_parts)

    system_prompt = f"""You are the DabbahWala marketing intelligence assistant. You answer questions
about the DabbahWala marketing system using REAL DATA provided below.

DabbahWala is a fresh, home-style Indian food delivery service in Atlanta.
We have ~4,000 contacts, run email campaigns via Instantly, SMS via Telnyx,
and track lifecycle stages (cold, engaged, active_customer, new_customer,
lapsed_customer, reactivation_candidate, cooling, optout).

We also have a ground team that records field notes about deliveries and customer
interactions, plus a social media team that creates daily ad copies. This team
content is included below when available — reference it by title and author.

REAL DATA FROM THE SYSTEM:
{data_context}

Rules:
- Answer based ONLY on the data provided. Do not make up numbers.
- If you don't have enough data to answer, say so clearly.
- Be specific with numbers and percentages.
- Keep answers concise but informative.
- Format with markdown (headers, lists, bold) for readability.
- If the question is about a specific customer, say you need their email."""

    ai_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    resp = await ai_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1500,
        system=system_prompt,
        messages=[{"role": "user", "content": question}],
    )
    answer = resp.content[0].text if resp.content else "No response generated."

    return answer, {"model": CLAUDE_MODEL, "data_sources": list(segments.keys())}


# ---------------------------------------------------------------------------
# Tone drafts
# ---------------------------------------------------------------------------

class ToneRequest(BaseModel):
    contact_email: str
    goal: str


_TONES = {
    "warm":   "friendly, personal, and empathetic — like a message from someone who genuinely cares",
    "urgent": "time-sensitive and action-oriented — creates a sense of urgency without being pushy",
    "casual": "relaxed and conversational — like a WhatsApp message from a friend",
}


@router.post("/tone")
def tone_drafts(req: ToneRequest):
    """
    Generate three message drafts (warm / urgent / casual) for a specific contact.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"error": "AI not configured (ANTHROPIC_API_KEY missing)"}

    with get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT first_name, last_name, lifecycle_segment, total_orders, last_order_at
            FROM contacts WHERE email = %s
        """, (req.contact_email,))
        row = cur.fetchone()

    if not row:
        return {"error": f"Contact not found: {req.contact_email}"}

    c = dict(row)
    name = f"{c.get('first_name') or ''} {c.get('last_name') or ''}".strip()
    profile = (
        f"Customer name: {name}\n"
        f"Lifecycle stage: {c.get('lifecycle_segment')}\n"
        f"Total orders: {c.get('total_orders', 0)}\n"
        f"Last order: {c.get('last_order_at') or 'never'}"
    )

    client = anthropic.Anthropic(api_key=api_key)
    result = {"contact_name": name}

    for tone_name, tone_desc in _TONES.items():
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=200,
            system=(
                f"You write marketing SMS/WhatsApp messages for DabbahWala, a food delivery "
                f"business in the UAE. Write ONE short message (max 160 chars) in a {tone_name} "
                f"tone: {tone_desc}. Use the customer's first name directly — no placeholders. "
                f"Output only the message text, nothing else."
            ),
            messages=[{
                "role": "user",
                "content": f"Customer profile:\n{profile}\n\nGoal: {req.goal}",
            }],
        )
        result[tone_name] = resp.content[0].text.strip() if resp.content else ""

    return result


# ---------------------------------------------------------------------------
# Main route
# Main endpoint
# ---------------------------------------------------------------------------

@router.post("", response_model=QueryResponse)
async def handle_query(req: QueryRequest):
    """
    Process a marketing query. Routes to the appropriate handler based on category.
    Called by n8n form workflow and the dashboard.
    """
    category = req.category.lower().strip()
    question = req.question.strip()
    email = req.contact_email.strip().lower() if req.contact_email else None
    phone = req.contact_phone.strip() if req.contact_phone else None
    name = req.contact_name.strip() if req.contact_name else None

    try:
        if category == "customer_lookup":
            answer, data = _handle_customer_lookup(question, email)
        elif category == "pipeline_snapshot":
            answer, data = _handle_pipeline_snapshot(question)
        elif category == "campaign_performance":
            answer, data = _handle_campaign_performance(question)
        elif category == "who_to_contact":
            answer, data = _handle_who_to_contact(question)
        elif category == "daily_summary":
            answer, data = _handle_daily_summary(question)
        elif category == "order_analytics":
            answer, data = _handle_order_analytics(question)
        elif category == "communication_history":
            answer, data = _handle_communication_history(question, email, phone, name)
        elif category == "ground_team_notes":
            answer, data = _handle_ground_team_notes(question)
        elif category == "ad_copies":
            answer, data = _handle_ad_copies(question)
        elif category == "submit_input":
            answer, data = _handle_submit_input(question, req.author, req.input_type)
        elif category == "free_form":
            answer, data = await _handle_free_form(question)
        else:
            answer = (
                f"Unknown category: {category}. "
                f"Available: {', '.join(CATEGORIES.keys())}"
            )
            data = {"available_categories": CATEGORIES}
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Query failed: {exc}")

    return QueryResponse(
        category=category,
        question=question or f"[{CATEGORIES.get(category, category)}]",
        answer=answer,
        data=data,
    )


@router.get("/categories")
def list_categories():
    """Return available query categories for the form dropdown."""
    return {"categories": CATEGORIES}
