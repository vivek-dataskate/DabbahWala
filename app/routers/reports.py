from datetime import date

from fastapi import APIRouter, HTTPException

from app.db import get_cursor

router = APIRouter()


@router.get("/daily/{report_date}")
def get_daily_report(report_date: date):
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT * FROM get_daily_report(%s)", (report_date,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"No report for {report_date}")
        return dict(row)


@router.post("/daily/{report_date}")
def generate_daily_report(report_date: date):
    """Generate and store a daily report with campaign activity, lifecycle transitions, and order attribution."""
    with get_cursor() as cur:
        cur.execute("SELECT generate_daily_report(%s)", (report_date,))
        result = cur.fetchone()["generate_daily_report"]
        return result
