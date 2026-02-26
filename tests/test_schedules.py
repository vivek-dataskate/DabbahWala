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
