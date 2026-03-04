import logging
from datetime import date

from fastapi import APIRouter, HTTPException

from app.db import get_cursor

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/daily/{report_date}")
def get_daily_report(report_date: date):
    logger.debug("get_daily_report — date=%s", report_date)
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT * FROM get_daily_report(%s)", (report_date,))
        row = cur.fetchone()
        if not row:
            logger.info("get_daily_report — no report found for date=%s", report_date)
            raise HTTPException(status_code=404, detail=f"No report for {report_date}")
        return dict(row)


@router.post("/daily/{report_date}")
def generate_daily_report(report_date: date):
    """Generate and store a daily report with campaign activity, lifecycle transitions, and order attribution."""
    logger.info("generate_daily_report — generating report for date=%s", report_date)
    with get_cursor() as cur:
        cur.execute("SELECT generate_daily_report(%s)", (report_date,))
        result = cur.fetchone()["generate_daily_report"]
    logger.info("generate_daily_report — done for date=%s", report_date)
    return result
