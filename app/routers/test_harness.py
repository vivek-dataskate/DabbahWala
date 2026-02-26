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


@router.get("/run/{group_id}")
def run_test_group(
    group_id: int,
):
    """
    Run a single test group by number (1-15) and return its results.
    Used by [System] Feature Tests n8n workflow — each node calls one group,
    so pass/fail is visible per group in n8n execution view.

    Groups:
      1  connectivity        7  intelligence
      2  schema              8  instantly
      3  contact_setup       9  airtable
      4  events             10  action_queue
      5  telnyx_sms         11  orders
      6  agent_pipeline     12  reports
                            13  query_chatbot
                            14  cleanup
                            15  competitor_agent
    """
    import uuid, time
    from datetime import datetime, timezone
    from app.services.test_harness_service import (
        TestSuite,
        _g1_connectivity, _g2_schema, _g3_contact_setup, _g4_events,
        _g5_telnyx_sms, _g6_agent_pipeline, _g7_intelligence, _g8_instantly,
        _g9_airtable, _g10_action_queue, _g11_orders, _g12_reports,
        _g13_query_chatbot, _g14_cleanup, _g15_competitor_agent,
    )

    GROUP_FNS = {
        1: _g1_connectivity,
        2: _g2_schema,
        3: _g3_contact_setup,
        4: _g4_events,
        5: _g5_telnyx_sms,
        6: _g6_agent_pipeline,
        7: _g7_intelligence,
        8: _g8_instantly,
        9: _g9_airtable,
        10: _g10_action_queue,
        11: _g11_orders,
        12: _g12_reports,
        13: _g13_query_chatbot,
        14: _g14_cleanup,
        15: _g15_competitor_agent,
    }

    if group_id not in GROUP_FNS:
        raise HTTPException(status_code=404, detail=f"Test group {group_id} not found. Valid: 1-15.")

    logger.info("Single test group triggered — group_id=%d", group_id)
    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    suite = TestSuite(run_id=run_id, started_at=started_at, triggered_by=f"n8n_group_{group_id}")

    t0 = time.time()
    try:
        GROUP_FNS[group_id](suite)
    except Exception as e:
        logger.error("Group %d raised unexpected error: %s", group_id, e, exc_info=True)

    suite.completed_at = datetime.now(timezone.utc).isoformat()
    suite.duration_seconds = round(time.time() - t0, 1)
    summary = suite.summary()

    passed = summary["passed"]
    failed = summary["failed"]
    total = summary["total"]
    logger.info("Group %d done: passed=%d failed=%d total=%d duration=%.1fs",
                group_id, passed, failed, total, suite.duration_seconds)

    # Return 200 with results (n8n node shows green). If any failures, return 207 so
    # the node turns orange, making failures visible in n8n execution view.
    suite_dict = suite.to_dict()
    result = {
        "group": group_id,
        "passed": passed,
        "failed": failed,
        "total": total,
        "duration_seconds": suite.duration_seconds,
        "groups": suite_dict.get("groups", []),
    }
    if failed > 0:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=207, content=result)
    return result


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
