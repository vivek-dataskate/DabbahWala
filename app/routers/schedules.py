"""
Schedule Management — GET /api/admin/schedules
                      POST /api/admin/schedules/{workflow_id}

Reads live workflow schedules from n8n and lets admins update them without
touching JSON files or the n8n UI directly.

Auth: requires an active @dabbahwala.com Google OAuth session (same as /dashboard).
"""
import logging
import os

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()

_N8N_URL = "https://digitalworker.dataskate.io"
_N8N_KEY = os.environ.get(
    "N8N_API_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJkMmFmN2JlMi1hMTYwLTRlZmUtYjFhOC0wMjlmM2U3OWZmMDkiLCJpc3MiOiJuOG4iLCJhdWQiOiJwdWJsaWMtYXBpIiwiaWF0IjoxNzcxNTQ3NzAzfQ.lOtKLp-YEdulBGSOD62uKCPTJBHOl_-0rDy2qa79FqE",
)
_N8N_HEADERS = {"X-N8N-API-KEY": _N8N_KEY}


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _require_session(request: Request) -> str:
    """Return user email if session is valid, else raise 403."""
    from app.routers.auth import get_current_user
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=403, detail="Login required")
    return user


# ---------------------------------------------------------------------------
# Schedule parsing helpers
# ---------------------------------------------------------------------------

_DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def _parse_schedule(nodes: list) -> str:
    """Return a human-readable schedule string from a workflow's node list."""
    for node in nodes:
        if node.get("type") == "n8n-nodes-base.scheduleTrigger":
            intervals = node.get("parameters", {}).get("rule", {}).get("interval", [])
            if not intervals:
                return "No schedule"
            iv = intervals[0]
            field = iv.get("field", "")
            if field == "minutes":
                n = iv.get("minutesInterval", 5)
                return f"Every {n} min"
            if field == "hours":
                n = iv.get("hoursInterval", 1)
                h = iv.get("triggerAtHour")
                m = iv.get("triggerAtMinute", 0)
                if n == 24 and h is not None:
                    return f"Daily at {h:02d}:{m:02d}"
                if n == 1:
                    return "Every hour"
                return f"Every {n} hours"
            if field == "days":
                n = iv.get("daysInterval", 1)
                h = iv.get("triggerAtHour", 0)
                m = iv.get("triggerAtMinute", 0)
                if n == 1:
                    return f"Daily at {h:02d}:{m:02d}"
                return f"Every {n} days at {h:02d}:{m:02d}"
            if field == "weeks":
                days = iv.get("triggerAtDay", [1])
                if not isinstance(days, list):
                    days = [days]
                day_str = "/".join(_DAY_NAMES[d % 7] for d in days)
                h = iv.get("triggerAtHour", 0)
                m = iv.get("triggerAtMinute", 0)
                return f"Weekly {day_str} at {h:02d}:{m:02d}"
    return "Manual only"


def _build_interval(payload: "ScheduleUpdate") -> dict:
    """Convert a ScheduleUpdate payload into an n8n interval spec dict."""
    iv: dict = {"field": payload.field}
    if payload.field == "minutes":
        iv["minutesInterval"] = payload.interval
    elif payload.field == "hours":
        iv["hoursInterval"] = payload.interval
        if payload.trigger_at_hour is not None:
            iv["triggerAtHour"] = payload.trigger_at_hour
        if payload.trigger_at_minute:
            iv["triggerAtMinute"] = payload.trigger_at_minute
    elif payload.field == "days":
        iv["daysInterval"] = payload.interval
        if payload.trigger_at_hour is not None:
            iv["triggerAtHour"] = payload.trigger_at_hour
        iv["triggerAtMinute"] = payload.trigger_at_minute
    elif payload.field == "weeks":
        iv["weeksInterval"] = payload.interval
        iv["triggerAtDay"] = payload.trigger_at_day or [1]
        if payload.trigger_at_hour is not None:
            iv["triggerAtHour"] = payload.trigger_at_hour
        iv["triggerAtMinute"] = payload.trigger_at_minute
    return iv


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ScheduleUpdate(BaseModel):
    field: str                          # "minutes" | "hours" | "days" | "weeks"
    interval: int = 1
    trigger_at_hour: int | None = None
    trigger_at_minute: int = 0
    trigger_at_day: list[int] | None = None   # 0=Sun … 6=Sat; only for "weeks"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/schedules")
def list_schedules(request: Request):
    """
    Return all n8n workflow schedules in a flat list.
    Sorted by workflow name. Each entry: {id, name, active, schedule}.
    """
    _require_session(request)
    try:
        resp = httpx.get(
            f"{_N8N_URL}/api/v1/workflows?limit=50",
            headers=_N8N_HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.error("list_schedules: n8n API error: %s", e)
        raise HTTPException(502, "Failed to reach n8n API")

    workflows = resp.json().get("data", [])
    result = [
        {
            "id": wf["id"],
            "name": wf["name"],
            "active": wf.get("active", False),
            "schedule": _parse_schedule(wf.get("nodes", [])),
        }
        for wf in sorted(workflows, key=lambda x: x["name"])
    ]
    logger.info("list_schedules: returned %d workflows", len(result))
    return result


@router.post("/schedules/{workflow_id}")
def update_schedule(workflow_id: str, payload: ScheduleUpdate, request: Request):
    """
    Update the scheduleTrigger node of a workflow and push to n8n.
    Returns the new human-readable schedule string.
    """
    user = _require_session(request)
    logger.info("update_schedule: user=%s workflow=%s field=%s interval=%s",
                user, workflow_id, payload.field, payload.interval)

    # 1. Fetch current workflow
    try:
        resp = httpx.get(
            f"{_N8N_URL}/api/v1/workflows/{workflow_id}",
            headers=_N8N_HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.error("update_schedule: fetch failed: %s", e)
        raise HTTPException(404, f"Workflow {workflow_id} not found in n8n")

    wf = resp.json()

    # 2. Update the scheduleTrigger node
    new_interval = _build_interval(payload)
    updated = False
    for node in wf.get("nodes", []):
        if node.get("type") == "n8n-nodes-base.scheduleTrigger":
            node.setdefault("parameters", {}).setdefault("rule", {})["interval"] = [new_interval]
            updated = True
            break

    if not updated:
        raise HTTPException(400, f"Workflow {workflow_id} has no scheduleTrigger node")

    # 3. Push back (n8n only accepts name/nodes/connections/settings)
    push = {k: wf[k] for k in ("name", "nodes", "connections", "settings") if k in wf}
    try:
        resp2 = httpx.put(
            f"{_N8N_URL}/api/v1/workflows/{workflow_id}",
            headers={**_N8N_HEADERS, "Content-Type": "application/json"},
            json=push,
            timeout=20,
        )
        resp2.raise_for_status()
    except httpx.HTTPError as e:
        logger.error("update_schedule: push failed: %s", e)
        raise HTTPException(502, f"Failed to update workflow in n8n: {e}")

    new_schedule = _parse_schedule(wf.get("nodes", []))
    logger.info("update_schedule: workflow=%s new_schedule=%s", workflow_id, new_schedule)
    return {"status": "updated", "workflow_id": workflow_id, "schedule": new_schedule}
