"""
Tests for app/routers/schedules.py
=====================================

Covers:
  Endpoint tests (TestClient with mocked httpx for n8n API calls)
    - GET  /api/admin/schedules          — no key / empty, with mock workflows
    - POST /api/admin/schedules/{id}     — update schedule

Note: All routes require a valid session (@dabbahwala.com Google login).
      We patch `app.routers.schedules._require_session` so auth is bypassed.

Run with:
    pytest tests/test_schedules.py -v
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

@contextmanager
def _cursor_ctx(cur):
    yield cur


def _make_cursor(rows=None, fetchone_val=None):
    c = MagicMock()
    c.fetchall.return_value = rows or []
    c.fetchone.return_value = fetchone_val
    return c


def _mock_httpx_get(json_data: dict, status_code: int = 200):
    """Return a mock for httpx.get that returns the given JSON payload."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


def _mock_httpx_put(json_data: dict, status_code: int = 200):
    """Return a mock for httpx.put that returns the given JSON payload."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/admin/schedules
# ─────────────────────────────────────────────────────────────────────────────

class TestGetSchedules:
    def test_get_schedules_no_key(self, client):
        """Without N8N_API_KEY, n8n call fails → 502 or empty list."""
        import httpx as _httpx

        with patch("app.routers.schedules._require_session", return_value="admin@dabbahwala.com"), \
             patch("app.routers.schedules.httpx.get", side_effect=_httpx.HTTPError("no key")):
            resp = client.get("/api/admin/schedules")

        # Expect either 502 (error raised) or a graceful empty response
        assert resp.status_code in (200, 502)

    def test_get_schedules_with_mock(self, client):
        """With mocked n8n API returning workflow list — 200 with schedules."""
        workflows = [
            {
                "id": "wf1",
                "name": "Test Workflow",
                "active": True,
                "nodes": [
                    {
                        "type": "n8n-nodes-base.scheduleTrigger",
                        "parameters": {
                            "rule": {
                                "interval": [{"field": "minutes", "minutesInterval": 30}]
                            }
                        },
                    }
                ],
            }
        ]
        mock_resp = _mock_httpx_get({"data": workflows})

        with patch("app.routers.schedules._require_session", return_value="admin@dabbahwala.com"), \
             patch("app.routers.schedules.httpx.get", return_value=mock_resp):
            resp = client.get("/api/admin/schedules")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["id"] == "wf1"
        assert data[0]["schedule"] == "Every 30 min"


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/admin/schedules/{workflow_id}
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdateSchedule:
    def test_update_schedule(self, client):
        """POST /api/admin/schedules/wf1 — updates schedule trigger node — 200."""
        existing_wf = {
            "id": "wf1",
            "name": "Test Workflow",
            "nodes": [
                {
                    "type": "n8n-nodes-base.scheduleTrigger",
                    "parameters": {
                        "rule": {
                            "interval": [{"field": "minutes", "minutesInterval": 30}]
                        }
                    },
                }
            ],
            "connections": {},
            "settings": {},
        }
        get_resp = _mock_httpx_get(existing_wf)
        put_resp = _mock_httpx_put({"id": "wf1"})

        with patch("app.routers.schedules._require_session", return_value="admin@dabbahwala.com"), \
             patch("app.routers.schedules.httpx.get", return_value=get_resp), \
             patch("app.routers.schedules.httpx.put", return_value=put_resp):
            resp = client.post(
                "/api/admin/schedules/wf1",
                json={"field": "minutes", "interval": 60},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"
        assert data["workflow_id"] == "wf1"
        assert "schedule" in data

    def test_update_schedule_no_trigger_node_returns_400(self, client):
        """POST /api/admin/schedules/wf2 with no scheduleTrigger node — 400."""
        existing_wf = {
            "id": "wf2",
            "name": "No Trigger Workflow",
            "nodes": [{"type": "n8n-nodes-base.set", "parameters": {}}],
            "connections": {},
            "settings": {},
        }
        get_resp = _mock_httpx_get(existing_wf)

        with patch("app.routers.schedules._require_session", return_value="admin@dabbahwala.com"), \
             patch("app.routers.schedules.httpx.get", return_value=get_resp):
            resp = client.post(
                "/api/admin/schedules/wf2",
                json={"field": "hours", "interval": 6},
            )

        assert resp.status_code == 400

    def test_update_cron_schedule_hours(self, client):
        """POST /api/admin/schedules/wf1 with hours field — returns 'Every N hours'."""
        existing_wf = {
            "id": "wf1",
            "name": "Hourly Workflow",
            "nodes": [
                {
                    "type": "n8n-nodes-base.scheduleTrigger",
                    "parameters": {
                        "rule": {
                            "interval": [{"field": "hours", "hoursInterval": 1}]
                        }
                    },
                }
            ],
            "connections": {},
            "settings": {},
        }
        get_resp = _mock_httpx_get(existing_wf)
        put_resp = _mock_httpx_put({"id": "wf1"})

        with patch("app.routers.schedules._require_session", return_value="admin@dabbahwala.com"), \
             patch("app.routers.schedules.httpx.get", return_value=get_resp), \
             patch("app.routers.schedules.httpx.put", return_value=put_resp):
            resp = client.post(
                "/api/admin/schedules/wf1",
                json={"field": "hours", "interval": 3},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"
        assert "hours" in data["schedule"].lower() or "hour" in data["schedule"].lower()

    def test_get_schedules_no_auth_returns_403(self, client):
        """Without a valid session, GET /api/admin/schedules returns 403."""
        resp = client.get("/api/admin/schedules")
        # No session cookie set → _require_session raises HTTPException 403
        assert resp.status_code == 403

    def test_update_schedule_with_valid_session_cookie(self, client):
        """Use create_session to build a valid cookie for auth."""
        from app.routers.auth import create_session

        workflows = [
            {
                "id": "wf_cookie",
                "name": "Cookie Workflow",
                "active": True,
                "nodes": [
                    {
                        "type": "n8n-nodes-base.scheduleTrigger",
                        "parameters": {
                            "rule": {
                                "interval": [{"field": "minutes", "minutesInterval": 15}]
                            }
                        },
                    }
                ],
            }
        ]
        mock_resp = _mock_httpx_get({"data": workflows})

        client.cookies.set("dw_session", create_session("test@dabbahwala.com"))
        with patch("app.routers.schedules.httpx.get", return_value=mock_resp):
            resp = client.get("/api/admin/schedules")

        # cleanup
        client.cookies.clear()
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# _parse_schedule helper — direct tests
# ---------------------------------------------------------------------------

class TestParseSchedule:
    """Test _parse_schedule helper directly."""

    def test_no_trigger_node_returns_manual_only(self):
        """Nodes list with no scheduleTrigger returns 'Manual only'."""
        from app.routers.schedules import _parse_schedule
        nodes = [{"type": "n8n-nodes-base.set", "parameters": {}}]
        assert _parse_schedule(nodes) == "Manual only"

    def test_empty_nodes_returns_manual_only(self):
        """Empty nodes list returns 'Manual only'."""
        from app.routers.schedules import _parse_schedule
        assert _parse_schedule([]) == "Manual only"

    def test_no_intervals_returns_no_schedule(self):
        """scheduleTrigger with no intervals returns 'No schedule'."""
        from app.routers.schedules import _parse_schedule
        nodes = [{
            "type": "n8n-nodes-base.scheduleTrigger",
            "parameters": {"rule": {"interval": []}},
        }]
        assert _parse_schedule(nodes) == "No schedule"

    def test_minutes_field(self):
        """Minutes interval returns 'Every N min'."""
        from app.routers.schedules import _parse_schedule
        nodes = [{
            "type": "n8n-nodes-base.scheduleTrigger",
            "parameters": {"rule": {"interval": [{"field": "minutes", "minutesInterval": 30}]}},
        }]
        assert _parse_schedule(nodes) == "Every 30 min"

    def test_hours_every_hour(self):
        """Hours interval of 1 returns 'Every hour'."""
        from app.routers.schedules import _parse_schedule
        nodes = [{
            "type": "n8n-nodes-base.scheduleTrigger",
            "parameters": {"rule": {"interval": [{"field": "hours", "hoursInterval": 1}]}},
        }]
        assert _parse_schedule(nodes) == "Every hour"

    def test_hours_every_3(self):
        """Hours interval of 3 returns 'Every 3 hours'."""
        from app.routers.schedules import _parse_schedule
        nodes = [{
            "type": "n8n-nodes-base.scheduleTrigger",
            "parameters": {"rule": {"interval": [{"field": "hours", "hoursInterval": 3}]}},
        }]
        assert _parse_schedule(nodes) == "Every 3 hours"

    def test_daily_at_specific_hour(self):
        """Hours=24 with triggerAtHour returns 'Daily at HH:MM'."""
        from app.routers.schedules import _parse_schedule
        nodes = [{
            "type": "n8n-nodes-base.scheduleTrigger",
            "parameters": {"rule": {"interval": [{
                "field": "hours", "hoursInterval": 24,
                "triggerAtHour": 8, "triggerAtMinute": 30,
            }]}},
        }]
        assert _parse_schedule(nodes) == "Daily at 08:30"

    def test_days_interval_of_1(self):
        """Days interval of 1 returns 'Daily at HH:MM'."""
        from app.routers.schedules import _parse_schedule
        nodes = [{
            "type": "n8n-nodes-base.scheduleTrigger",
            "parameters": {"rule": {"interval": [{
                "field": "days", "daysInterval": 1,
                "triggerAtHour": 7, "triggerAtMinute": 30,
            }]}},
        }]
        assert _parse_schedule(nodes) == "Daily at 07:30"

    def test_days_interval_multiple(self):
        """Days interval > 1 returns 'Every N days at HH:MM'."""
        from app.routers.schedules import _parse_schedule
        nodes = [{
            "type": "n8n-nodes-base.scheduleTrigger",
            "parameters": {"rule": {"interval": [{
                "field": "days", "daysInterval": 3,
                "triggerAtHour": 0, "triggerAtMinute": 0,
            }]}},
        }]
        assert _parse_schedule(nodes) == "Every 3 days at 00:00"

    def test_weeks_interval(self):
        """Weekly interval returns 'Weekly Mon at HH:MM'."""
        from app.routers.schedules import _parse_schedule
        nodes = [{
            "type": "n8n-nodes-base.scheduleTrigger",
            "parameters": {"rule": {"interval": [{
                "field": "weeks", "weeksInterval": 1,
                "triggerAtDay": [1],  # Monday
                "triggerAtHour": 6, "triggerAtMinute": 30,
            }]}},
        }]
        result = _parse_schedule(nodes)
        assert "Mon" in result
        assert "06:30" in result

    def test_weeks_interval_integer_day(self):
        """Weekly interval with integer day (not list) still works."""
        from app.routers.schedules import _parse_schedule
        nodes = [{
            "type": "n8n-nodes-base.scheduleTrigger",
            "parameters": {"rule": {"interval": [{
                "field": "weeks", "weeksInterval": 1,
                "triggerAtDay": 2,  # integer Tuesday
                "triggerAtHour": 9, "triggerAtMinute": 0,
            }]}},
        }]
        result = _parse_schedule(nodes)
        assert "Tue" in result


# ---------------------------------------------------------------------------
# _build_interval helper — direct tests
# ---------------------------------------------------------------------------

class TestBuildInterval:
    """Test _build_interval helper directly."""

    def test_minutes_field(self):
        """_build_interval with field=minutes includes minutesInterval."""
        from app.routers.schedules import _build_interval, ScheduleUpdate
        payload = ScheduleUpdate(field="minutes", interval=15)
        iv = _build_interval(payload)
        assert iv["field"] == "minutes"
        assert iv["minutesInterval"] == 15

    def test_hours_field(self):
        """_build_interval with field=hours includes hoursInterval."""
        from app.routers.schedules import _build_interval, ScheduleUpdate
        payload = ScheduleUpdate(field="hours", interval=3, trigger_at_hour=12)
        iv = _build_interval(payload)
        assert iv["field"] == "hours"
        assert iv["hoursInterval"] == 3
        assert iv["triggerAtHour"] == 12

    def test_days_field(self):
        """_build_interval with field=days includes daysInterval."""
        from app.routers.schedules import _build_interval, ScheduleUpdate
        payload = ScheduleUpdate(field="days", interval=1, trigger_at_hour=8, trigger_at_minute=30)
        iv = _build_interval(payload)
        assert iv["field"] == "days"
        assert iv["daysInterval"] == 1
        assert iv["triggerAtHour"] == 8
        assert iv["triggerAtMinute"] == 30

    def test_weeks_field(self):
        """_build_interval with field=weeks includes weeksInterval and triggerAtDay."""
        from app.routers.schedules import _build_interval, ScheduleUpdate
        payload = ScheduleUpdate(field="weeks", interval=1, trigger_at_day=[1, 3])
        iv = _build_interval(payload)
        assert iv["field"] == "weeks"
        assert iv["weeksInterval"] == 1
        assert iv["triggerAtDay"] == [1, 3]
