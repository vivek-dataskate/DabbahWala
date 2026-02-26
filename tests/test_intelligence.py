"""
Tests for app/routers/intelligence.py
======================================

Covers:
  - POST /api/intelligence/run-cycle
  - GET  /api/intelligence/pending-actions
  - POST /api/intelligence/ingest-instantly-events

Run with:
    pytest tests/test_intelligence.py -v
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cursor(rows=None, fetchone_val=None):
    c = MagicMock()
    c.fetchall.return_value = rows or []
    c.fetchone.return_value = fetchone_val
    return c


@contextmanager
def _cursor_ctx(cur):
    yield cur


# ---------------------------------------------------------------------------
# 1. POST /api/intelligence/run-cycle
# ---------------------------------------------------------------------------

class TestRunCycle:
    def test_run_cycle_returns_phases(self, client):
        """
        Mock all five phase functions; POST /run-cycle must return 200 with
        all five phase keys in the response body.
        """
        collect_result  = {"email_opens": 1, "new_events_total": 1}
        profile_result  = {"rollups_refreshed": True, "active_contacts": 5}
        signal_counts   = {"engaged_no_order": 0}
        raw_signals     = {"engaged_no_order": []}
        route_result    = {"opportunities_created": 0, "total_actions": 0}
        dispatch_result = {"pending_campaign_moves": 0}

        with patch("app.routers.intelligence._phase_collect",  return_value=collect_result), \
             patch("app.routers.intelligence._phase_profile",  return_value=profile_result), \
             patch("app.routers.intelligence._phase_signal",   return_value=(signal_counts, raw_signals)), \
             patch("app.routers.intelligence._phase_route",    return_value=(route_result, [])), \
             patch("app.routers.intelligence._phase_dispatch", return_value=dispatch_result), \
             patch("app.routers.intelligence.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(MagicMock())):
            resp = client.post("/api/intelligence/run-cycle")

        assert resp.status_code == 200
        data = resp.json()
        assert "timestamp" in data
        assert "collect"  in data
        assert "profile"  in data
        assert "signal"   in data
        assert "route"    in data
        assert "dispatch" in data

    def test_run_cycle_db_error(self, client):
        """
        When get_cursor raises an unhandled exception the endpoint propagates it.
        TestClient with raise_server_exceptions=True will re-raise it here.
        """
        with patch("app.routers.intelligence.get_cursor",
                   side_effect=Exception("connection refused")):
            with pytest.raises(Exception, match="connection refused"):
                client.post("/api/intelligence/run-cycle")


# ---------------------------------------------------------------------------
# 2. GET /api/intelligence/pending-actions
# ---------------------------------------------------------------------------

class TestPendingActions:
    def test_get_pending_actions(self, client):
        """
        fetchall returns a campaign-move row and an opportunity row;
        endpoint must return 200 and include both in the response.
        """
        campaign_row = {
            "queue_id": 1,
            "email": "t@t.com",
            "phone": "+1",
            "first_name": "A",
            "last_name": "B",
            "from_campaign": "old",
            "to_campaign": "DW-PromoStandard-ActiveEngaged",
            "instantly_campaign_id": "cid1",
            "instantly_campaign_name": "DW-PromoStandard-ActiveEngaged",
        }
        opportunity_row = {
            "id": 1,
            "contact_id": 1,
            "action": "campaign_move",
            "priority": "hot",
            "reason": "test",
            "suggested_message": None,
            "confidence_score": 0.9,
            "email": "t@t.com",
            "phone": "+1",
            "first_name": "A",
            "last_name": "B",
            "lifecycle_segment": "active",
            "total_orders": 3,
        }

        cur = MagicMock()
        # First fetchall → campaign moves; second fetchall → opportunities
        cur.fetchall.side_effect = [[campaign_row], [opportunity_row]]
        # fetchone calls used for SMS template lookup
        cur.fetchone.return_value = None

        with patch("app.routers.intelligence.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/intelligence/pending-actions")

        assert resp.status_code == 200
        data = resp.json()
        assert "campaign_moves" in data
        assert "summary" in data
        assert data["summary"]["total_pending"] >= 1

    def test_pending_actions_empty(self, client):
        """When there are no pending actions the summary counts are all zero."""
        cur = MagicMock()
        cur.fetchall.return_value = []
        cur.fetchone.return_value = None

        with patch("app.routers.intelligence.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/intelligence/pending-actions")

        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["total_pending"] == 0
        assert data["campaign_moves"] == []


# ---------------------------------------------------------------------------
# 3. POST /api/intelligence/ingest-instantly-events
# ---------------------------------------------------------------------------

class TestIngestInstantlyEvents:
    def test_ingest_events(self, client):
        """
        POST a single email_open event for a known contact; expect 200 with
        the ingested count reflected in the response.
        """
        cur = MagicMock()
        # fetchone returns the contact lookup
        cur.fetchone.return_value = {"id": 42}

        with patch("app.routers.intelligence.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/intelligence/ingest-instantly-events",
                json={
                    "events": [
                        {
                            "email": "t@t.com",
                            "event_type": "email_open",
                            "campaign_id": "c1",
                        }
                    ]
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        # The endpoint returns ingested + errors + total
        assert data["total"] == 1
        assert data["ingested"] == 1
        assert data["errors"] == 0
