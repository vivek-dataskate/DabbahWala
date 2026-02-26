"""
Tests for app/routers/opportunities.py
========================================

Covers:
  - GET  /api/opportunities/detect
  - GET  /api/opportunities/detect/new-customer-no-repeat
  - GET  /api/opportunities/detect/lapsed-reengaged
  - GET  /api/opportunities/detect/reorder-intent
  - POST /api/opportunities
  - GET  /api/opportunities/pending
  - POST /api/opportunities/{id}/dispatched
  - POST /api/opportunities/{id}/outcome

Run with:
    pytest tests/test_opportunities.py -v
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
# 1. Detection endpoints
# ---------------------------------------------------------------------------

class TestDetectOpportunities:
    def test_detect_engaged(self, client):
        """GET /detect with 3 candidates → 200 {count:3, candidates:[...]}."""
        candidates = [
            {"id": 1, "email": "a@b.com", "opens_7d": 5, "clicks_7d": 2},
            {"id": 2, "email": "c@d.com", "opens_7d": 3, "clicks_7d": 0},
            {"id": 3, "email": "e@f.com", "opens_7d": 1, "clicks_7d": 1},
        ]
        cur = _make_cursor(rows=candidates)

        with patch("app.routers.opportunities.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/opportunities/detect")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 3
        assert isinstance(data["candidates"], list)

    def test_detect_new_customer(self, client):
        """GET /detect/new-customer-no-repeat → 200 with count and candidates."""
        cur = _make_cursor(rows=[{"id": 10, "email": "new@c.com"}])

        with patch("app.routers.opportunities.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/opportunities/detect/new-customer-no-repeat")

        assert resp.status_code == 200
        data = resp.json()
        assert "count" in data
        assert "candidates" in data

    def test_detect_lapsed(self, client):
        """GET /detect/lapsed-reengaged → 200 with count and candidates."""
        cur = _make_cursor(rows=[])

        with patch("app.routers.opportunities.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/opportunities/detect/lapsed-reengaged")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["candidates"] == []

    def test_detect_reorder(self, client):
        """GET /detect/reorder-intent → 200 with count and candidates."""
        cur = _make_cursor(rows=[{"id": 5, "email": "r@t.com"}])

        with patch("app.routers.opportunities.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/opportunities/detect/reorder-intent")

        assert resp.status_code == 200
        data = resp.json()
        assert "count" in data
        assert "candidates" in data


# ---------------------------------------------------------------------------
# 2. POST /api/opportunities  (create)
# ---------------------------------------------------------------------------

class TestCreateOpportunity:
    def test_create_opportunity(self, client):
        """Valid payload with contact_id → 200 {id: 99}."""
        cur = _make_cursor(fetchone_val={"create_opportunity": 99})

        with patch("app.routers.opportunities.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/opportunities",
                json={
                    "contact_id": 1,
                    "action": "send_sms",
                    "priority": "hot",
                    "reason": "test",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["id"] == 99

    def test_create_missing_contact_id(self, client):
        """Omitting required contact_id → 422 Unprocessable Entity."""
        resp = client.post(
            "/api/opportunities",
            json={
                "action": "send_sms",
                "priority": "hot",
                "reason": "test",
            },
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 3. GET /api/opportunities/pending
# ---------------------------------------------------------------------------

class TestPendingOpportunities:
    def test_get_pending(self, client):
        """fetchall returns one opportunity row → 200 list with one item."""
        row = {
            "id": 1,
            "contact_id": 2,
            "action": "send_sms",
            "priority": "hot",
            "status": "pending",
            "reason": "test",
            "suggested_message": "Hi",
            "created_at": None,
            "contact_email": "t@t.com",
            "contact_phone": "+1",
            "contact_name": "A B",
            "lifecycle_segment": "active",
        }
        cur = _make_cursor(rows=[row])

        with patch("app.routers.opportunities.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/opportunities/pending")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["id"] == 1
        assert data[0]["priority"] == "hot"


# ---------------------------------------------------------------------------
# 4. POST /api/opportunities/{id}/dispatched  and  /{id}/outcome
# ---------------------------------------------------------------------------

class TestOpportunityOutcome:
    def test_mark_dispatched(self, client):
        """POST /{id}/dispatched with airtable_record_id → 200 {status:'dispatched'}."""
        cur = _make_cursor(fetchone_val={"mark_opportunity_dispatched": True})

        with patch("app.routers.opportunities.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/opportunities/1/dispatched",
                json={"airtable_record_id": "rec123"},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "dispatched"

    def test_mark_outcome(self, client):
        """POST /{id}/outcome with status + outcome → 200 {status: <given_status>}."""
        cur = _make_cursor(fetchone_val={"update_opportunity_outcome": True})

        with patch("app.routers.opportunities.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/opportunities/1/outcome",
                json={"status": "completed", "outcome": "ordered"},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"
