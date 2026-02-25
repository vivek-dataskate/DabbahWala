"""
Test Harness API Router
========================
Exposes endpoints to trigger and retrieve end-to-end test suite runs.

POST /api/test/run is intentionally open (no auth) — consistent with all other
n8n-called endpoints (/api/lifecycle/run, /api/intelligence/run-cycle, etc.).
n8n on this instance does not support environment variables, so secrets cannot
be injected at runtime. The endpoint is harmless without auth: it only creates
test data tagged source='test_harness' and cleans it up at the end of the run.

GET endpoints accept an optional ADMIN_SECRET for direct access control.

Endpoints
---------
POST /api/test/run              — Run the full test suite (open — n8n callable)
GET  /api/test/results          — List recent test runs (optional auth)
GET  /api/test/results/{run_id} — Get full results for a specific run (optional auth)
"""

import logging
import os

from fastapi import APIRouter, Header, HTTPException, Query
from typing import Optional

logger = logging.getLogger(__name__)
router = APIRouter()


def _check_auth(secret: Optional[str]) -> None:
    """Optional auth for read endpoints. Skips check if ADMIN_SECRET is unset."""
    admin_secret = os.environ.get("ADMIN_SECRET", "")
    if not admin_secret:
        return
    if secret and secret != admin_secret:
        raise HTTPException(status_code=403, detail="Invalid admin secret")


@router.post("/run")
def run_test_suite(
    triggered_by: str = Query(default="manual", description="'manual' | 'n8n_daily'"),
):
    """
    Run the complete DabbahWala end-to-end test suite.

    Open endpoint — callable by n8n without authentication (consistent with
    /api/lifecycle/run, /api/intelligence/run-cycle, and all other n8n endpoints).

    Executes 55+ tests across 14 groups synchronously. May take up to 10 minutes
    (Claude agent calls included). n8n sets a 900 s timeout.

    Zero real-customer impact: all test data uses source='test_harness' and is
    cascade-deleted at the end of every run. SMS goes to +18444322224 (self-loop).
    Emails go to vivek@dabbahwala.com (admin inbox).
    """
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
