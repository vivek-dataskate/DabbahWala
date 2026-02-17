import json
from datetime import date

from fastapi import APIRouter, HTTPException

from app.db import get_cursor

router = APIRouter()


@router.get("/daily/{report_date}")
def get_daily_report(report_date: date):
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT * FROM daily_reports WHERE report_date = %s", (report_date,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"No report for {report_date}")
        return dict(row)


@router.post("/daily/{report_date}")
def generate_daily_report(report_date: date):
    """Generate and store a daily report with campaign activity, lifecycle transitions, and order attribution."""
    with get_cursor() as cur:
        # Campaign activity
        cur.execute(
            """
            SELECT
                e.event_type,
                count(*) AS count
            FROM events e
            WHERE e.occurred_at::date = %s
              AND e.event_type IN ('email_open', 'email_click', 'sms_sent', 'sms_click', 'order_placed', 'unsubscribe')
            GROUP BY e.event_type
            """,
            (report_date,),
        )
        activity = {row["event_type"]: row["count"] for row in cur.fetchall()}

        # Lifecycle transitions
        cur.execute(
            """
            SELECT
                prev_lifecycle,
                new_lifecycle,
                count(*) AS count
            FROM decision_log
            WHERE decided_at::date = %s
            GROUP BY prev_lifecycle, new_lifecycle
            """,
            (report_date,),
        )
        transitions = [dict(r) for r in cur.fetchall()]

        # Pipeline snapshot
        cur.execute(
            """
            SELECT lifecycle_segment, count(*) AS count
            FROM contacts
            GROUP BY lifecycle_segment
            """
        )
        pipeline = {row["lifecycle_segment"]: row["count"] for row in cur.fetchall()}

        # Net new orders (orders where contact had marketing touch in prior 7 days)
        cur.execute(
            """
            SELECT count(DISTINCT e_order.id) AS attributed_orders
            FROM events e_order
            WHERE e_order.event_type = 'order_placed'
              AND e_order.occurred_at::date = %s
              AND EXISTS (
                  SELECT 1 FROM events e_touch
                  WHERE e_touch.contact_id = e_order.contact_id
                    AND e_touch.event_type IN ('email_open', 'email_click', 'sms_click')
                    AND e_touch.occurred_at BETWEEN e_order.occurred_at - interval '7 days' AND e_order.occurred_at
              )
            """,
            (report_date,),
        )
        attributed = cur.fetchone()
        net_new_orders = attributed["attributed_orders"] if attributed else 0

        report_data = {
            "activity": activity,
            "transitions": transitions,
            "pipeline": pipeline,
            "net_new_orders": net_new_orders,
        }

        # Store the report
        cur.execute(
            """
            INSERT INTO daily_reports (report_date, report_data, net_new_orders)
            VALUES (%s, %s, %s)
            ON CONFLICT (report_date) DO UPDATE SET
                report_data = EXCLUDED.report_data,
                net_new_orders = EXCLUDED.net_new_orders,
                created_at = now()
            RETURNING id
            """,
            (report_date, json.dumps(report_data), net_new_orders),
        )
        row = cur.fetchone()

        return {"id": row["id"], "report_date": str(report_date), **report_data}
