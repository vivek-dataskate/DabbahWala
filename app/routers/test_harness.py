"""
Test Harness API Router
========================
Exposes endpoints to trigger and retrieve end-to-end test suite runs.

Authentication: All endpoints require the ADMIN_SECRET header or query param.

Endpoints
---------
POST /api/test/run              — Run the full test suite synchronously
GET  /api/test/results          — List recent test runs (last 10)
GET  /api/test/results/{run_id} — Get full results for a specific run
"""

import logging
import os

from fastapi import APIRouter, Header, HTTPException, Query
from typing import Optional

logger = logging.getLogger(__name__)
router = APIRouter()


def _check_auth(secret: Optional[str]) -> None:
    admin_secret = os.environ.get("ADMIN_SECRET", "")
    if not admin_secret:
        logger.warning("ADMIN_SECRET not configured — test harness unprotected")
        return
    if secret != admin_secret:
        raise HTTPException(status_code=403, detail="Invalid admin secret")


@router.post("/run")
def run_test_suite(
    secret: str = Query(default="", description="Admin secret (or use X-Admin-Secret header)"),
    x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret"),
    triggered_by: str = Query(default="manual", description="'manual' | 'n8n_daily'"),
):
    """
    Run the complete DabbahWala end-to-end test suite.

    Executes all 55+ tests across 14 groups synchronously. The request may take
    up to 10 minutes depending on Claude agent calls. n8n should set a 900s timeout.

    Returns the full test run results including per-test pass/fail/skip status.
    Zero impact on real customers — all test data uses source='test_harness' and
    is cleaned up at the end of the run.
    """
    _check_auth(secret or x_admin_secret)
    logger.info("Test suite triggered — triggered_by=%s", triggered_by)
    from app.services.test_harness_service import run_full_suite
    suite = run_full_suite(triggered_by=triggered_by)
    return suite.to_dict()


@router.get("/results")
def list_test_runs(
    secret: str = Query(default=""),
    x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret"),
    limit: int = Query(default=10, ge=1, le=50),
):
    """Return the most recent test runs (summary only, no per-test details)."""
    _check_auth(secret or x_admin_secret)
    from app.services.test_harness_service import get_recent_runs
    return {"runs": get_recent_runs(limit=limit)}


@router.get("/results/{run_id}")
def get_test_run(
    run_id: str,
    secret: str = Query(default=""),
    x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret"),
):
    """Return full results (including per-test details) for a specific test run."""
    _check_auth(secret or x_admin_secret)
    from app.services.test_harness_service import get_run_by_id
    run = get_run_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Test run {run_id} not found")
    return run
