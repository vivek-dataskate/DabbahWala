"""
Tests for app/routers/campaigns.py
====================================

Covers:
  - GET  /api/campaigns/pending
  - POST /api/campaigns/log-push
  - GET  /api/campaigns/push-log
  - POST /api/campaigns/bulk-executed
  - GET  /api/campaigns/active-contacts
  - GET  /api/campaigns/analytics
  - GET  /api/campaigns/templates
  - GET  /api/campaigns/templates/{campaign_name}
  - PUT  /api/campaigns/templates/{campaign_name}

Run with:
    pytest tests/test_campaigns.py -v
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
# 1. GET /api/campaigns/pending
# ---------------------------------------------------------------------------

class TestPendingCampaigns:
    def test_get_pending_empty(self, client):
        """No pending moves → 200 with empty list."""
        cur = _make_cursor(rows=[])

        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/campaigns/pending")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_pending_with_data(self, client):
        """One pending move → 200 with a single-item list."""
        row = {
            "queue_id": 1,
            "contact_email": "a@b.com",
            "contact_phone": "+1234567890",
            "contact_first_name": "A",
            "contact_last_name": "B",
            "from_campaign": "old",
            "to_campaign": "new",
        }
        cur = _make_cursor(rows=[row])

        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/campaigns/pending")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["queue_id"] == 1
        assert data[0]["to_campaign"] == "new"


# ---------------------------------------------------------------------------
# 2. POST /api/campaigns/log-push
# ---------------------------------------------------------------------------

class TestLogPush:
    def test_log_push_success(self, client):
        """Successful push logged → 200 {status: 'ok'}."""
        cur = _make_cursor()

        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/campaigns/log-push",
                json={
                    "queue_id": 1,
                    "email": "a@b.com",
                    "to_campaign": "DW-PromoStandard-ActiveEngaged",
                    "success": True,
                },
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_log_push_failure(self, client):
        """Failed push with error_message → 200 {status: 'ok'}."""
        cur = _make_cursor()

        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/campaigns/log-push",
                json={
                    "queue_id": 1,
                    "email": "a@b.com",
                    "to_campaign": "DW-PromoStandard-ActiveEngaged",
                    "success": False,
                    "error_message": "quota exceeded",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# 3. POST /api/campaigns/bulk-executed
# ---------------------------------------------------------------------------

class TestBulkExecuted:
    def test_bulk_executed(self, client):
        """Three queue IDs marked executed → 200 {updated: 3}."""
        cur = MagicMock()
        cur.rowcount = 3

        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/campaigns/bulk-executed",
                json={"queue_ids": [1, 2, 3]},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] == 3


# ---------------------------------------------------------------------------
# 4. GET /api/campaigns/analytics
# ---------------------------------------------------------------------------

class TestAnalytics:
    def test_get_analytics(self, client, monkeypatch):
        """
        Mock _get_routing_rows + httpx + INSTANTLY_API_KEY; endpoint must
        return 200 with a list of per-campaign analytics.
        """
        monkeypatch.setenv("INSTANTLY_API_KEY", "test_key_abc123")

        routing_row = {
            "default_campaign": "DW-PromoStandard-ActiveEngaged",
            "instantly_campaign_id": "cid1",
            "instantly_campaign_name": "DW Promo Standard",
            "template_file": None,
        }
        analytics_payload = [
            {
                "leads_count": 50,
                "emails_sent_count": 40,
                "open_rate": 0.2,
                "reply_rate": 0.05,
                "bounced_count": 2,
                "unsubscribed_count": 1,
            }
        ]

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = analytics_payload

        with patch("app.routers.campaigns._get_routing_rows", return_value=[routing_row]), \
             patch("app.routers.campaigns.httpx.get", return_value=mock_resp), \
             patch("app.routers.campaigns.INSTANTLY_API_KEY", "test_key_abc123"):
            resp = client.get("/api/campaigns/analytics")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["campaign_name"] == "DW-PromoStandard-ActiveEngaged"


# ---------------------------------------------------------------------------
# 5. GET /api/campaigns/templates
# ---------------------------------------------------------------------------

class TestTemplates:
    def test_get_templates(self, client):
        """
        Mock _get_routing_rows; no filesystem access needed when template_file
        is None (results in total_steps: 0 without reading any file).
        """
        routing_rows = [
            {
                "default_campaign": "DW-PromoStandard-ActiveEngaged",
                "instantly_campaign_id": "cid1",
                "instantly_campaign_name": "DW Promo Standard",
                "template_file": None,
            }
        ]

        with patch("app.routers.campaigns._get_routing_rows", return_value=routing_rows):
            resp = client.get("/api/campaigns/templates")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["campaign_name"] == "DW-PromoStandard-ActiveEngaged"

    def test_get_single_template(self, client):
        """
        Mock _load_campaign_json; GET a specific campaign template → 200 with
        subject, body, and campaign_name in the response.
        """
        meta = {
            "default_campaign": "DW-NurtureSlow-ColdContacts",
            "instantly_campaign_id": "cid2",
            "instantly_campaign_name": "DW Nurture Slow",
            "template_file": "DW-NurtureSlow-ColdContacts.json",
        }
        data = {
            "sequences": [
                {
                    "steps": [
                        {
                            "type": "email",
                            "delay": 0,
                            "variants": [
                                {"subject": "Hi there", "body": "Hello body"},
                            ],
                        }
                    ]
                }
            ]
        }

        with patch("app.routers.campaigns._load_campaign_json", return_value=(meta, data)):
            resp = client.get("/api/campaigns/templates/DW-NurtureSlow-ColdContacts")

        assert resp.status_code == 200
        body = resp.json()
        assert body["campaign_name"] == "DW-NurtureSlow-ColdContacts"
        assert body["subject"] == "Hi there"
        assert body["body"] == "Hello body"

    def test_template_not_found(self, client):
        """
        When _load_campaign_json raises HTTPException 404 the endpoint returns 404.
        """
        from fastapi import HTTPException as FastAPIHTTPException

        with patch(
            "app.routers.campaigns._load_campaign_json",
            side_effect=FastAPIHTTPException(status_code=404, detail="Template file missing"),
        ):
            resp = client.get("/api/campaigns/templates/DW-NonExistent")

        assert resp.status_code == 404

    def test_update_template(self, client):
        """
        PUT with new subject/body → mock _load_campaign_json + file write +
        get_cursor → 200 with status 'saved'.
        """
        meta = {
            "default_campaign": "DW-NurtureSlow-ColdContacts",
            "instantly_campaign_id": "cid2",
            "instantly_campaign_name": "DW Nurture Slow",
            "template_file": "DW-NurtureSlow-ColdContacts.json",
        }
        data = {
            "sequences": [
                {
                    "steps": [
                        {
                            "type": "email",
                            "delay": 0,
                            "variants": [
                                {"subject": "Old subject", "body": "Old body"},
                            ],
                        }
                    ]
                }
            ]
        }

        mock_path = MagicMock()
        mock_path.write_text = MagicMock()

        cur = _make_cursor()

        with patch("app.routers.campaigns._load_campaign_json", return_value=(meta, data)), \
             patch("app.routers.campaigns._DATA_DIR", MagicMock(__truediv__=lambda s, x: mock_path)), \
             patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.put(
                "/api/campaigns/templates/DW-NurtureSlow-ColdContacts",
                json={
                    "step_index": 0,
                    "variant_index": 0,
                    "subject": "New subject",
                    "body": "New body",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        # The endpoint returns a status key
        assert "status" in body


# ---------------------------------------------------------------------------
# 6. GET /api/campaigns/push-log
# ---------------------------------------------------------------------------

class TestPushLog:
    """GET /api/campaigns/push-log — return recent campaign push log entries."""

    def test_returns_all_entries_by_default(self, client):
        """No success filter → returns all entries."""
        rows = [
            {"id": 1, "email": "a@b.com", "to_campaign": "X", "success": True},
            {"id": 2, "email": "c@d.com", "to_campaign": "Y", "success": False},
        ]
        cur = _make_cursor(rows=rows)
        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/campaigns/push-log")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_filters_by_success_true(self, client):
        """?success=true should query the DB with success=True filter."""
        rows = [{"id": 1, "email": "a@b.com", "to_campaign": "X", "success": True}]
        cur = _make_cursor(rows=rows)
        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/campaigns/push-log?success=true")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["success"] is True

    def test_filters_by_success_false(self, client):
        """?success=false should query the DB with success=False filter."""
        rows = [{"id": 2, "email": "c@d.com", "to_campaign": "Y", "success": False}]
        cur = _make_cursor(rows=rows)
        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/campaigns/push-log?success=false")

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 7. GET /api/campaigns/active-contacts
# ---------------------------------------------------------------------------

class TestActiveContacts:
    """GET /api/campaigns/active-contacts — contacts with active campaign assignment."""

    def test_returns_contacts_with_campaigns(self, client):
        """Should return contacts joined to campaign_routing."""
        rows = [
            {
                "contact_id": 1, "email": "a@example.com",
                "first_name": "Alice", "last_name": "Smith",
                "phone": "+12223334444",
                "current_campaign": "WARM_NURTURE",
                "instantly_campaign_id": "camp-abc",
            }
        ]
        cur = _make_cursor(rows=rows)
        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/campaigns/active-contacts")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["email"] == "a@example.com"

    def test_returns_empty_list_when_no_contacts(self, client):
        """Should return [] when no active-campaign contacts exist."""
        cur = _make_cursor(rows=[])
        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/campaigns/active-contacts")

        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# 8. GET /api/campaigns/active-contacts-stats
# ---------------------------------------------------------------------------

class TestActiveContactsStats:
    """GET /api/campaigns/active-contacts-stats — diagnostic filter counts."""

    def test_returns_stats_and_distribution(self, client):
        """Should return aggregate counts and campaign_distribution list."""
        stats_row = {
            "total_contacts": 100, "no_email": 5, "placeholder_email": 3,
            "optout": 2, "cooling": 1, "returned_by_api": 89,
        }
        dist_rows = [
            {"current_campaign": "WARM_NURTURE", "cnt": 50},
            {"current_campaign": "COLD_OUTREACH", "cnt": 39},
        ]
        cur = MagicMock()
        cur.fetchone.return_value = stats_row
        cur.fetchall.return_value = dist_rows

        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/campaigns/active-contacts-stats")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_contacts"] == 100
        assert "campaign_distribution" in data
        assert len(data["campaign_distribution"]) == 2


# ---------------------------------------------------------------------------
# 9. POST /api/campaigns/{queue_id}/executed
# ---------------------------------------------------------------------------

class TestMarkExecuted:
    """POST /api/campaigns/{queue_id}/executed — mark single queue entry via stored proc."""

    def test_marks_single_entry_executed(self, client):
        """Should call the stored proc and return status='ok'."""
        cur = _make_cursor()
        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/campaigns/7/executed")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# 10. POST /api/campaigns/templates/{name}/rewrite
# ---------------------------------------------------------------------------

class TestRewriteTemplate:
    """POST /api/campaigns/templates/{name}/rewrite — Claude AI rewrite suggestion."""

    def _routing_row(self):
        return {
            "default_campaign": "WARM_NURTURE",
            "instantly_campaign_id": "camp-111",
            "instantly_campaign_name": "Warm Nurture",
            "template_file": "warm_nurture.json",
        }

    def test_returns_rewritten_subject_and_body(self, client):
        """Should call Claude and return rewritten subject/body JSON."""
        cur = _make_cursor(fetchone_val=self._routing_row())

        mock_message = MagicMock()
        mock_message.content = [MagicMock(
            text='{"subject": "Better subject", "body": "<div>Better body</div>"}'
        )]

        mock_claude_client = MagicMock()
        mock_claude_client.messages.create.return_value = mock_message

        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("app.routers.campaigns.ANTHROPIC_API_KEY", "fake-key"), \
             patch("anthropic.Anthropic", return_value=mock_claude_client):
            resp = client.post("/api/campaigns/templates/WARM_NURTURE/rewrite", json={
                "step_index": 0,
                "variant_index": 0,
                "subject": "Old subject",
                "body": "<div>Old body text here</div>",
                "instruction": "Make it warmer",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["subject"] == "Better subject"
        assert "body" in data

    def test_returns_404_for_unknown_campaign(self, client):
        """Unknown campaign should return 404."""
        from fastapi import HTTPException as FastAPIHTTPException
        with patch("app.routers.campaigns._get_routing_row",
                   side_effect=FastAPIHTTPException(status_code=404, detail="Unknown campaign")):
            resp = client.post("/api/campaigns/templates/NO_SUCH/rewrite", json={
                "step_index": 0,
                "variant_index": 0,
                "subject": "X",
                "body": "Y",
            })
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 11. POST /api/campaigns/bulk-push-to-instantly
# ---------------------------------------------------------------------------

class TestBulkPushToInstantly:
    """POST /api/campaigns/bulk-push-to-instantly — start background push task."""

    def test_starts_background_task(self, client):
        """Should return status='started' with pending_moves count."""
        cur = _make_cursor(fetchone_val={"cnt": 25})
        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("app.routers.campaigns.INSTANTLY_API_KEY", "fake-key"):
            resp = client.post("/api/campaigns/bulk-push-to-instantly")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert data["pending_moves"] == 25

    def test_returns_503_when_no_api_key(self, client):
        """Should return 503 when INSTANTLY_API_KEY is not configured."""
        with patch("app.routers.campaigns.INSTANTLY_API_KEY", ""):
            resp = client.post("/api/campaigns/bulk-push-to-instantly")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# 12. POST /api/campaigns/repair-push
# ---------------------------------------------------------------------------

class TestRepairPush:
    """POST /api/campaigns/repair-push — start background repair task."""

    def test_starts_repair_task(self, client):
        """Should return status='started' with count of leads to repair."""
        rows = [
            {"email": "a@b.com", "to_campaign": "WARM_NURTURE",
             "first_name": "Alice", "last_name": "Smith"},
        ]
        cur = _make_cursor(rows=rows)
        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("app.routers.campaigns.INSTANTLY_API_KEY", "fake-key"):
            resp = client.post("/api/campaigns/repair-push?hours=24")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert data["leads_to_repair"] == 1

    def test_returns_503_when_no_api_key(self, client):
        """Should return 503 when INSTANTLY_API_KEY is not configured."""
        with patch("app.routers.campaigns.INSTANTLY_API_KEY", ""):
            resp = client.post("/api/campaigns/repair-push")
        assert resp.status_code == 503
