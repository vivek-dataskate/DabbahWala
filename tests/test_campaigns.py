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


# ---------------------------------------------------------------------------
# push_lead_to_instantly (internal helper) — direct tests
# ---------------------------------------------------------------------------

class TestPushLeadToInstantly:
    """Test the push_lead_to_instantly helper function directly."""

    def test_returns_true_when_enqueued_successfully(self):
        """Returns True when campaign found and action_queue INSERT succeeds."""
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch
        from app.routers.campaigns import push_lead_to_instantly

        routing_row = {
            "default_campaign": "DW-Promo",
            "instantly_campaign_id": "cid-abc123",
            "instantly_campaign_name": "DW Promo Standard",
            "template_file": "promo_standard.json",
        }
        cur = MagicMock()

        @contextmanager
        def _cursor_ctx(commit=False):
            yield cur

        with patch("app.routers.campaigns._get_routing_row", return_value=routing_row), \
             patch("app.routers.campaigns.get_cursor", side_effect=_cursor_ctx):
            result = push_lead_to_instantly(
                email="alice@example.com",
                first_name="Alice",
                last_name="Smith",
                phone="+14041111111",
                campaign_name="DW-Promo",
                contact_id=42,
            )

        assert result is True

    def test_returns_false_when_campaign_not_found(self):
        """Returns False when _get_routing_row raises HTTPException (404)."""
        from fastapi import HTTPException
        from app.routers.campaigns import push_lead_to_instantly
        from unittest.mock import patch

        with patch("app.routers.campaigns._get_routing_row",
                   side_effect=HTTPException(status_code=404, detail="Not found")):
            result = push_lead_to_instantly(
                email="bob@example.com",
                first_name="Bob",
                last_name="Jones",
                phone="",
                campaign_name="UNKNOWN_CAMPAIGN",
            )

        assert result is False

    def test_returns_false_when_no_instantly_id(self):
        """Returns False when routing row has no instantly_campaign_id."""
        from app.routers.campaigns import push_lead_to_instantly
        from unittest.mock import patch

        routing_row = {
            "default_campaign": "DW-Promo",
            "instantly_campaign_id": None,  # no ID
            "instantly_campaign_name": "DW Promo",
            "template_file": "promo.json",
        }

        with patch("app.routers.campaigns._get_routing_row", return_value=routing_row):
            result = push_lead_to_instantly(
                email="carol@example.com",
                first_name="Carol",
                last_name="Lee",
                phone="",
                campaign_name="DW-Promo",
            )

        assert result is False


# ---------------------------------------------------------------------------
# _get_routing_rows and _get_routing_row — direct tests
# ---------------------------------------------------------------------------

class TestGetRoutingRows:
    """Test _get_routing_rows DB helper directly."""

    def test_returns_list_of_dicts(self):
        """_get_routing_rows returns a list of routing row dicts."""
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch
        from app.routers.campaigns import _get_routing_rows

        rows = [
            {"default_campaign": "DW-Promo", "instantly_campaign_id": "cid1",
             "instantly_campaign_name": "DW Promo", "template_file": "promo.json"},
        ]
        cur = MagicMock()
        cur.fetchall.return_value = rows

        @contextmanager
        def _cursor_ctx(commit=False):
            yield cur

        with patch("app.routers.campaigns.get_cursor", side_effect=_cursor_ctx):
            result = _get_routing_rows()

        assert len(result) == 1
        assert result[0]["default_campaign"] == "DW-Promo"


class TestGetRoutingRow:
    """Test _get_routing_row DB helper directly."""

    def test_returns_row_when_found(self):
        """Returns dict when campaign_name exists in campaign_routing."""
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch
        from app.routers.campaigns import _get_routing_row

        row = {"default_campaign": "DW-Promo", "instantly_campaign_id": "cid1",
               "instantly_campaign_name": "DW Promo", "template_file": "promo.json"}
        cur = MagicMock()
        cur.fetchone.return_value = row

        @contextmanager
        def _cursor_ctx(commit=False):
            yield cur

        with patch("app.routers.campaigns.get_cursor", side_effect=_cursor_ctx):
            result = _get_routing_row("DW-Promo")

        assert result["default_campaign"] == "DW-Promo"

    def test_raises_404_when_not_found(self):
        """Raises HTTPException 404 when campaign_name not in routing table."""
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch
        from fastapi import HTTPException
        from app.routers.campaigns import _get_routing_row

        cur = MagicMock()
        cur.fetchone.return_value = None

        @contextmanager
        def _cursor_ctx(commit=False):
            yield cur

        import pytest
        with patch("app.routers.campaigns.get_cursor", side_effect=_cursor_ctx):
            with pytest.raises(HTTPException) as exc_info:
                _get_routing_row("NONEXISTENT_CAMPAIGN")

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/campaigns/push-log additional tests
# ---------------------------------------------------------------------------

class TestPushLogFilters:
    def test_filters_by_success_false(self, client):
        """?success=false returns only failed pushes."""
        failed_row = {
            "id": 5, "queue_id": 2, "email": "fail@test.com",
            "to_campaign": "DW-Promo", "success": False,
            "status_code": 422, "error_message": "Unprocessable",
            "response_body": None, "created_at": "2026-01-10T00:00:00",
        }
        cur = _make_cursor(rows=[failed_row])

        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/campaigns/push-log?success=false")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["success"] is False

    def test_push_log_with_custom_limit(self, client):
        """?limit=5 passes limit to the DB query."""
        cur = _make_cursor(rows=[])
        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/campaigns/push-log?limit=5")
        assert resp.status_code == 200

    def test_push_log_default_returns_all(self, client):
        """No success filter returns all entries."""
        rows = [
            {"id": 1, "queue_id": 1, "email": "a@x.com", "to_campaign": "DW-Promo",
             "success": True, "status_code": 200, "error_message": None,
             "response_body": None, "created_at": "2026-01-10T00:00:00"},
            {"id": 2, "queue_id": 2, "email": "b@x.com", "to_campaign": "DW-Promo",
             "success": False, "status_code": 422, "error_message": "Error",
             "response_body": None, "created_at": "2026-01-10T00:00:00"},
        ]
        cur = _make_cursor(rows=rows)
        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/campaigns/push-log")
        assert resp.status_code == 200
        assert len(resp.json()) == 2


# ---------------------------------------------------------------------------
# GET /api/campaigns/analytics — with mocked INSTANTLY_API_KEY
# ---------------------------------------------------------------------------

class TestCampaignAnalyticsWithKey:
    def test_analytics_returns_empty_when_no_routing_rows(self, client, monkeypatch):
        """Returns empty list when no campaign_routing rows exist."""
        with patch("app.routers.campaigns.INSTANTLY_API_KEY", "test-key"), \
             patch("app.routers.campaigns._get_routing_rows", return_value=[]):
            resp = client.get("/api/campaigns/analytics")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_analytics_handles_httpx_error_gracefully(self, client):
        """When Instantly API call fails, error is captured in the result dict."""
        import httpx
        routing_row = {
            "instantly_campaign_id": "cid-abc",
            "default_campaign": "DW-Promo",
            "instantly_campaign_name": "DW Promo",
        }
        with patch("app.routers.campaigns.INSTANTLY_API_KEY", "test-key"), \
             patch("app.routers.campaigns._get_routing_rows", return_value=[routing_row]), \
             patch("app.routers.campaigns.httpx.get",
                   side_effect=httpx.ConnectError("connection refused")):
            resp = client.get("/api/campaigns/analytics")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert "error" in data[0]


# ---------------------------------------------------------------------------
# POST /api/campaigns/{queue_id}/executed
# ---------------------------------------------------------------------------

class TestMarkExecutedAdditional:
    def test_mark_executed_calls_stored_proc(self, client):
        """POST /{queue_id}/executed invokes mark_campaign_executed stored proc."""
        cur = _make_cursor()

        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=True: _cursor_ctx(cur)):
            resp = client.post("/api/campaigns/999/executed")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# _get_push_log with verify=True — calls Instantly API per lead
# ---------------------------------------------------------------------------

class TestPushLogVerify:
    def test_verify_flag_calls_instantly_api(self, client):
        """GET /push-log?verify=true calls Instantly API for each successful push."""
        push_rows = [
            {"queue_id": 1, "email": "alice@example.com", "to_campaign": "WARM_NURTURE",
             "success": True, "status_code": 200, "error_message": None, "created_at": None},
        ]
        cur = _make_cursor(rows=push_rows)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"campaign": "camp-123"}]

        routing_row = {"instantly_campaign_id": "camp-123", "default_campaign": "WARM_NURTURE"}

        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("app.routers.campaigns.httpx.get", return_value=mock_resp), \
             patch("app.routers.campaigns._get_routing_row", return_value=routing_row):
            resp = client.get("/api/campaigns/push-log?verify=true")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["instantly_found"] is True

    def test_verify_marks_not_found_on_404(self, client):
        """GET /push-log?verify=true marks instantly_found=False when lead not found."""
        push_rows = [
            {"queue_id": 2, "email": "bob@example.com", "to_campaign": "PROMO",
             "success": True, "status_code": 200, "error_message": None, "created_at": None},
        ]
        cur = _make_cursor(rows=push_rows)

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("app.routers.campaigns.httpx.get", return_value=mock_resp), \
             patch("app.routers.campaigns._get_routing_row", return_value={}):
            resp = client.get("/api/campaigns/push-log?verify=true")

        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["instantly_found"] is False

    def test_verify_handles_api_exception(self, client):
        """GET /push-log?verify=true handles Instantly API exceptions per row."""
        push_rows = [
            {"queue_id": 3, "email": "carol@example.com", "to_campaign": "LAPSED",
             "success": True, "status_code": 200, "error_message": None, "created_at": None},
        ]
        cur = _make_cursor(rows=push_rows)

        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("app.routers.campaigns.httpx.get", side_effect=Exception("timeout")):
            resp = client.get("/api/campaigns/push-log?verify=true")

        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["instantly_found"] is None
        assert "instantly_error" in data[0]

    def test_verify_skips_failed_pushes(self, client):
        """GET /push-log?verify=true skips API check for failed push rows."""
        push_rows = [
            {"queue_id": 4, "email": "dave@example.com", "to_campaign": "PROMO",
             "success": False, "status_code": 400, "error_message": "rejected", "created_at": None},
        ]
        cur = _make_cursor(rows=push_rows)

        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("app.routers.campaigns.httpx.get") as mock_get:
            resp = client.get("/api/campaigns/push-log?verify=true")

        mock_get.assert_not_called()
        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["instantly_found"] is None


# ---------------------------------------------------------------------------
# _log_push — direct test
# ---------------------------------------------------------------------------

class TestLogPushDirect:
    def test_log_push_inserts_row(self):
        """_log_push inserts a row into campaign_push_log."""
        from app.routers.campaigns import _log_push

        cur = _make_cursor()

        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=True: _cursor_ctx(cur)):
            _log_push(queue_id=1, email="a@b.com", to_campaign="PROMO",
                      success=True, status_code=200, error_message=None)

        assert cur.execute.called

    def test_log_push_silently_handles_exception(self):
        """_log_push silently ignores DB exceptions."""
        from app.routers.campaigns import _log_push

        with patch("app.routers.campaigns.get_cursor", side_effect=Exception("DB down")):
            _log_push(1, "a@b.com", "PROMO", True, 200, None)  # should not raise


# ---------------------------------------------------------------------------
# repair_push loop body (lines 182, 188-207)
# ---------------------------------------------------------------------------

class TestRepairPushLoop:
    def test_repair_push_skips_rows_without_campaign_id(self, client):
        """repair_push skips rows where _get_routing_row finds no instantly_campaign_id."""
        push_rows = [{"email": "a@b.com", "first_name": "A", "last_name": "B",
                      "to_campaign": "PROMO", "queue_id": 1}]
        cur = _make_cursor(rows=push_rows)

        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("app.routers.campaigns.INSTANTLY_API_KEY", "fake-key"), \
             patch("app.routers.campaigns._get_routing_row", return_value={}):
            resp = client.post("/api/campaigns/repair-push?hours=24")

        assert resp.status_code == 200

    def test_repair_push_pushes_successfully(self, client):
        """repair_push POSTs to Instantly for each valid row."""
        push_rows = [{"email": "a@b.com", "first_name": "A", "last_name": "B",
                      "to_campaign": "PROMO", "queue_id": 1}]
        cur = _make_cursor(rows=push_rows)

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_http_client = MagicMock()
        mock_http_client.__enter__ = MagicMock(return_value=mock_http_client)
        mock_http_client.__exit__ = MagicMock(return_value=False)
        mock_http_client.post.return_value = mock_resp

        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("app.routers.campaigns.INSTANTLY_API_KEY", "fake-key"), \
             patch("app.routers.campaigns._get_routing_row",
                   return_value={"instantly_campaign_id": "camp-123"}), \
             patch("app.routers.campaigns.httpx.Client", return_value=mock_http_client):
            resp = client.post("/api/campaigns/repair-push?hours=24")

        assert resp.status_code == 200

    def test_repair_push_handles_http_error(self, client):
        """repair_push counts error when Instantly returns 4xx."""
        push_rows = [{"email": "a@b.com", "first_name": "A", "last_name": "B",
                      "to_campaign": "PROMO", "queue_id": 1}]
        cur = _make_cursor(rows=push_rows)

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad request"

        mock_http_client = MagicMock()
        mock_http_client.__enter__ = MagicMock(return_value=mock_http_client)
        mock_http_client.__exit__ = MagicMock(return_value=False)
        mock_http_client.post.return_value = mock_resp

        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("app.routers.campaigns.INSTANTLY_API_KEY", "fake-key"), \
             patch("app.routers.campaigns._get_routing_row",
                   return_value={"instantly_campaign_id": "camp-123"}), \
             patch("app.routers.campaigns.httpx.Client", return_value=mock_http_client):
            resp = client.post("/api/campaigns/repair-push?hours=24")

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# bulk_push_to_instantly loop body — httpx calls
# ---------------------------------------------------------------------------

class TestBulkPushLoop:
    def test_bulk_push_deduplicates_email(self):
        """_run_bulk_push skips duplicate emails in the queue."""
        from app.routers.campaigns import _run_bulk_push

        queue_rows = [
            {"queue_id": 1, "email": "alice@example.com", "first_name": "Alice",
             "last_name": "A", "to_campaign": "PROMO", "instantly_campaign_id": "camp-1"},
            {"queue_id": 2, "email": "alice@example.com", "first_name": "Alice",
             "last_name": "A", "to_campaign": "PROMO", "instantly_campaign_id": "camp-1"},
        ]
        cur = _make_cursor(rows=queue_rows)

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_http_client = MagicMock()
        mock_http_client.__enter__ = MagicMock(return_value=mock_http_client)
        mock_http_client.__exit__ = MagicMock(return_value=False)
        mock_http_client.post.return_value = mock_resp

        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=True: _cursor_ctx(cur)), \
             patch("app.routers.campaigns.INSTANTLY_API_KEY", "fake-key"), \
             patch("app.routers.campaigns.httpx.Client", return_value=mock_http_client), \
             patch("app.routers.campaigns._log_push"):
            _run_bulk_push(batch_size=10, delay_ms=0)

        # Second email was duplicate, should only push once
        assert mock_http_client.post.call_count == 1

    def test_bulk_push_skips_no_campaign_id(self):
        """_run_bulk_push skips rows with no instantly_campaign_id."""
        from app.routers.campaigns import _run_bulk_push

        queue_rows = [
            {"queue_id": 1, "email": "bob@example.com", "first_name": "Bob",
             "last_name": "B", "to_campaign": "PROMO", "instantly_campaign_id": None},
        ]
        cur = _make_cursor(rows=queue_rows)

        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=True: _cursor_ctx(cur)), \
             patch("app.routers.campaigns.INSTANTLY_API_KEY", "fake-key"), \
             patch("app.routers.campaigns._log_push") as mock_log:
            _run_bulk_push(batch_size=10, delay_ms=0)

        # Logged as failed push
        mock_log.assert_called_once()
        assert mock_log.call_args[0][3] is False  # success=False

    def test_bulk_push_handles_http_exception(self):
        """_run_bulk_push catches httpx exceptions per lead."""
        from app.routers.campaigns import _run_bulk_push

        queue_rows = [
            {"queue_id": 1, "email": "carol@example.com", "first_name": "Carol",
             "last_name": "C", "to_campaign": "PROMO", "instantly_campaign_id": "camp-1"},
        ]
        cur = _make_cursor(rows=queue_rows)

        mock_http_client = MagicMock()
        mock_http_client.__enter__ = MagicMock(return_value=mock_http_client)
        mock_http_client.__exit__ = MagicMock(return_value=False)
        mock_http_client.post.side_effect = Exception("network error")

        with patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=True: _cursor_ctx(cur)), \
             patch("app.routers.campaigns.INSTANTLY_API_KEY", "fake-key"), \
             patch("app.routers.campaigns.httpx.Client", return_value=mock_http_client), \
             patch("app.routers.campaigns._log_push") as mock_log:
            _run_bulk_push(batch_size=10, delay_ms=0)

        # Should have logged the error
        mock_log.assert_called_once()
        assert mock_log.call_args[0][3] is False  # success=False


# ---------------------------------------------------------------------------
# _load_campaign_json — error paths
# ---------------------------------------------------------------------------

class TestLoadCampaignJson:
    def test_raises_404_when_no_template_file(self):
        """_load_campaign_json raises 404 when routing row has no template_file."""
        from app.routers.campaigns import _load_campaign_json
        from fastapi import HTTPException

        with patch("app.routers.campaigns._get_routing_row",
                   return_value={"default_campaign": "TEST", "template_file": None}):
            try:
                _load_campaign_json("TEST")
                assert False, "Should have raised HTTPException"
            except HTTPException as e:
                assert e.status_code == 404

    def test_raises_404_when_template_file_missing(self):
        """_load_campaign_json raises 404 when template file doesn't exist on disk."""
        from app.routers.campaigns import _load_campaign_json
        from fastapi import HTTPException
        from pathlib import Path

        with patch("app.routers.campaigns._get_routing_row",
                   return_value={"default_campaign": "TEST", "template_file": "nonexistent.json"}), \
             patch.object(Path, "exists", return_value=False):
            try:
                _load_campaign_json("TEST")
                assert False, "Should have raised HTTPException"
            except HTTPException as e:
                assert e.status_code == 404


# ---------------------------------------------------------------------------
# get_campaign_template — error paths
# ---------------------------------------------------------------------------

class TestGetCampaignTemplateErrors:
    def _make_template_data(self):
        return {
            "sequences": [{
                "steps": [{
                    "delay": 0,
                    "type": "email",
                    "variants": [{"subject": "S1", "body": "<div>B1</div>"}]
                }]
            }]
        }

    def _routing_row(self):
        return {"default_campaign": "PROMO", "instantly_campaign_id": "camp-1",
                "instantly_campaign_name": "Promo", "template_file": "promo.json"}

    def test_no_steps_returns_404(self, client):
        """get_campaign_template returns 404 when template has no steps."""
        with patch("app.routers.campaigns._load_campaign_json",
                   return_value=(self._routing_row(), {"sequences": [{"steps": []}]})):
            resp = client.get("/api/campaigns/templates/PROMO?step=0&variant=0")

        assert resp.status_code == 404

    def test_step_out_of_range_returns_400(self, client):
        """get_campaign_template returns 400 when step index is out of range."""
        with patch("app.routers.campaigns._load_campaign_json",
                   return_value=(self._routing_row(), self._make_template_data())):
            resp = client.get("/api/campaigns/templates/PROMO?step=99&variant=0")

        assert resp.status_code == 400

    def test_no_variants_returns_404(self, client):
        """get_campaign_template returns 404 when step has no variants."""
        data = {"sequences": [{"steps": [{"delay": 0, "variants": []}]}]}
        with patch("app.routers.campaigns._load_campaign_json",
                   return_value=(self._routing_row(), data)):
            resp = client.get("/api/campaigns/templates/PROMO?step=0&variant=0")

        assert resp.status_code == 404

    def test_variant_out_of_range_returns_400(self, client):
        """get_campaign_template returns 400 when variant index is out of range."""
        with patch("app.routers.campaigns._load_campaign_json",
                   return_value=(self._routing_row(), self._make_template_data())):
            resp = client.get("/api/campaigns/templates/PROMO?step=0&variant=99")

        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# update_campaign_template — error paths and enqueue exception
# ---------------------------------------------------------------------------

class TestUpdateCampaignTemplateErrors:
    def _routing_row(self):
        return {"default_campaign": "PROMO", "instantly_campaign_id": "camp-1",
                "instantly_campaign_name": "Promo", "template_file": "promo.json"}

    def _make_template_data(self):
        return {
            "sequences": [{
                "steps": [{
                    "delay": 0,
                    "type": "email",
                    "variants": [{"subject": "S1", "body": "<div>B1</div>"}]
                }]
            }]
        }

    def test_step_out_of_range_returns_400(self, client):
        """PUT returns 400 when step_index is out of range."""
        with patch("app.routers.campaigns._load_campaign_json",
                   return_value=(self._routing_row(), self._make_template_data())):
            resp = client.put("/api/campaigns/templates/PROMO", json={
                "step_index": 99, "variant_index": 0,
                "subject": "X", "body": "Y",
            })

        assert resp.status_code == 400

    def test_variant_out_of_range_returns_400(self, client):
        """PUT returns 400 when variant_index is out of range."""
        with patch("app.routers.campaigns._load_campaign_json",
                   return_value=(self._routing_row(), self._make_template_data())):
            resp = client.put("/api/campaigns/templates/PROMO", json={
                "step_index": 0, "variant_index": 99,
                "subject": "X", "body": "Y",
            })

        assert resp.status_code == 400

    def test_enqueue_exception_returns_enqueue_failed(self, client):
        """PUT returns enqueue_failed status when DB insert raises."""
        from pathlib import Path
        from unittest.mock import mock_open

        cur = _make_cursor()
        cur.execute.side_effect = Exception("DB error")

        with patch("app.routers.campaigns._load_campaign_json",
                   return_value=(self._routing_row(), self._make_template_data())), \
             patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=True: _cursor_ctx(cur)), \
             patch("pathlib.Path.write_text"):
            resp = client.put("/api/campaigns/templates/PROMO", json={
                "step_index": 0, "variant_index": 0,
                "subject": "New S", "body": "<div>New B</div>",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["instantly_status"] == "enqueue_failed"


# ---------------------------------------------------------------------------
# rewrite_template_with_claude — error paths
# ---------------------------------------------------------------------------

class TestRewriteTemplateErrors:
    def _routing_row(self):
        return {"default_campaign": "PROMO", "instantly_campaign_id": "camp-1",
                "instantly_campaign_name": "Promo"}

    def test_no_json_in_claude_response_returns_500(self, client):
        """Rewrite returns 500 if Claude response has no JSON object."""
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="Sorry, I cannot help with that.")]

        mock_claude = MagicMock()
        mock_claude.messages.create.return_value = mock_message

        with patch("app.routers.campaigns._get_routing_row", return_value=self._routing_row()), \
             patch("app.routers.campaigns.ANTHROPIC_API_KEY", "test-key"), \
             patch("anthropic.Anthropic", return_value=mock_claude):
            resp = client.post("/api/campaigns/templates/PROMO/rewrite", json={
                "step_index": 0, "variant_index": 0,
                "subject": "Old S", "body": "Old B",
            })

        assert resp.status_code == 500

    def test_invalid_json_in_claude_response_returns_500(self, client):
        """Rewrite returns 500 if JSON parse fails."""
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text='{"subject": invalid json here}')]

        mock_claude = MagicMock()
        mock_claude.messages.create.return_value = mock_message

        with patch("app.routers.campaigns._get_routing_row", return_value=self._routing_row()), \
             patch("app.routers.campaigns.ANTHROPIC_API_KEY", "test-key"), \
             patch("anthropic.Anthropic", return_value=mock_claude):
            resp = client.post("/api/campaigns/templates/PROMO/rewrite", json={
                "step_index": 0, "variant_index": 0,
                "subject": "Old S", "body": "Old B",
            })

        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# _get_all_campaigns — pagination logic
# ---------------------------------------------------------------------------

class TestGetAllCampaigns:
    def test_returns_campaigns_from_single_page(self):
        """_get_all_campaigns returns campaigns when response has <100 items."""
        from app.routers.campaigns import _get_all_campaigns

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "items": [{"id": "c1", "name": "Campaign 1"}],
            "next_cursor": None,
        }

        with patch("app.routers.campaigns.httpx.get", return_value=mock_resp):
            result = _get_all_campaigns({"Authorization": "Bearer test"})

        assert len(result) == 1
        assert result[0]["id"] == "c1"

    def test_handles_exception_returns_partial(self):
        """_get_all_campaigns returns partial list on API exception."""
        from app.routers.campaigns import _get_all_campaigns

        with patch("app.routers.campaigns.httpx.get", side_effect=Exception("timeout")):
            result = _get_all_campaigns({"Authorization": "Bearer test"})

        assert result == []


# ---------------------------------------------------------------------------
# _get_or_create_tag_id — various paths
# ---------------------------------------------------------------------------

class TestGetOrCreateTagId:
    def test_returns_existing_tag_id(self):
        """_get_or_create_tag_id returns existing tag ID when found."""
        from app.routers.campaigns import _get_or_create_tag_id

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [{"id": "tag-123", "label": "Dabbahwala"}]

        with patch("app.routers.campaigns.httpx.get", return_value=mock_resp):
            result = _get_or_create_tag_id({"Authorization": "Bearer test"}, "Dabbahwala")

        assert result == "tag-123"

    def test_creates_new_tag_when_not_found(self):
        """_get_or_create_tag_id creates tag when none exists."""
        from app.routers.campaigns import _get_or_create_tag_id

        list_resp = MagicMock()
        list_resp.raise_for_status = MagicMock()
        list_resp.json.return_value = []  # no tags

        create_resp = MagicMock()
        create_resp.raise_for_status = MagicMock()
        create_resp.json.return_value = {"id": "new-tag-456"}

        with patch("app.routers.campaigns.httpx.get", return_value=list_resp), \
             patch("app.routers.campaigns.httpx.post", return_value=create_resp):
            result = _get_or_create_tag_id({"Authorization": "Bearer test"}, "Dabbahwala")

        assert result == "new-tag-456"

    def test_returns_none_on_exception(self):
        """_get_or_create_tag_id returns None when API call raises."""
        from app.routers.campaigns import _get_or_create_tag_id

        with patch("app.routers.campaigns.httpx.get", side_effect=Exception("timeout")):
            result = _get_or_create_tag_id({"Authorization": "Bearer test"}, "Dabbahwala")

        assert result is None

    def test_deduplicates_multiple_matching_tags(self):
        """_get_or_create_tag_id deletes duplicate tags and keeps first."""
        from app.routers.campaigns import _get_or_create_tag_id

        list_resp = MagicMock()
        list_resp.raise_for_status = MagicMock()
        list_resp.json.return_value = [
            {"id": "tag-1", "label": "Dabbahwala"},
            {"id": "tag-2", "label": "Dabbahwala"},
        ]

        delete_resp = MagicMock()
        delete_resp.raise_for_status = MagicMock()

        with patch("app.routers.campaigns.httpx.get", return_value=list_resp), \
             patch("app.routers.campaigns.httpx.delete", return_value=delete_resp):
            result = _get_or_create_tag_id({"Authorization": "Bearer test"}, "Dabbahwala")

        assert result == "tag-1"  # kept the first one


# ---------------------------------------------------------------------------
# _get_all_account_emails
# ---------------------------------------------------------------------------

class TestGetAllAccountEmails:
    def test_returns_emails_list(self):
        """_get_all_account_emails returns list of email strings."""
        from app.routers.campaigns import _get_all_account_emails

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "items": [{"email": "sender@dabbahwala.com"}, {"email": "noreply@dabbahwala.com"}]
        }

        with patch("app.routers.campaigns.httpx.get", return_value=mock_resp):
            result = _get_all_account_emails({"Authorization": "Bearer test"})

        assert "sender@dabbahwala.com" in result
        assert len(result) == 2

    def test_returns_empty_list_on_exception(self):
        """_get_all_account_emails returns [] on API error."""
        from app.routers.campaigns import _get_all_account_emails

        with patch("app.routers.campaigns.httpx.get", side_effect=Exception("network")):
            result = _get_all_account_emails({"Authorization": "Bearer test"})

        assert result == []


# ---------------------------------------------------------------------------
# _create_instantly_campaign
# ---------------------------------------------------------------------------

class TestCreateInstantlyCampaign:
    def test_returns_campaign_id_on_success(self):
        """_create_instantly_campaign returns new campaign ID."""
        from app.routers.campaigns import _create_instantly_campaign

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"id": "new-camp-789"}

        with patch("app.routers.campaigns.httpx.post", return_value=mock_resp):
            result = _create_instantly_campaign({"Authorization": "Bearer test"}, "New Campaign")

        assert result == "new-camp-789"

    def test_returns_none_on_exception(self):
        """_create_instantly_campaign returns None on API error."""
        from app.routers.campaigns import _create_instantly_campaign

        with patch("app.routers.campaigns.httpx.post", side_effect=Exception("timeout")):
            result = _create_instantly_campaign({"Authorization": "Bearer test"}, "Fail")

        assert result is None


# ---------------------------------------------------------------------------
# setup_instantly_campaigns — endpoint
# ---------------------------------------------------------------------------

class TestSetupInstantly:
    def test_returns_503_when_no_api_key(self, client):
        """setup-instantly returns 503 when INSTANTLY_API_KEY not set."""
        with patch("app.routers.campaigns.INSTANTLY_API_KEY", ""):
            resp = client.post("/api/campaigns/setup-instantly")
        assert resp.status_code == 503

    def test_returns_already_setup_when_all_canonical_present(self, client):
        """setup-instantly returns already_setup when all campaigns exist and no dups."""
        routing_rows = [
            {"default_campaign": "PROMO", "instantly_campaign_id": "camp-1",
             "instantly_campaign_name": "Promo", "template_file": None},
        ]
        all_campaigns = [{"id": "camp-1", "name": "Promo"}]

        with patch("app.routers.campaigns.INSTANTLY_API_KEY", "test-key"), \
             patch("app.routers.campaigns._get_routing_rows", return_value=routing_rows), \
             patch("app.routers.campaigns._get_all_campaigns", return_value=all_campaigns):
            resp = client.post("/api/campaigns/setup-instantly")

        assert resp.status_code == 200
        assert resp.json()["status"] == "already_setup"

    def test_creates_missing_campaigns_and_tags(self, client):
        """setup-instantly creates campaigns and tags them when not present."""
        routing_rows = [
            {"default_campaign": "PROMO", "instantly_campaign_id": None,
             "instantly_campaign_name": "Promo", "template_file": None},
        ]

        cur = _make_cursor()

        patch_resp = MagicMock()
        patch_resp.status_code = 200

        with patch("app.routers.campaigns.INSTANTLY_API_KEY", "test-key"), \
             patch("app.routers.campaigns._get_routing_rows", return_value=routing_rows), \
             patch("app.routers.campaigns._get_all_campaigns", return_value=[]), \
             patch("app.routers.campaigns._get_or_create_tag_id", return_value="tag-1"), \
             patch("app.routers.campaigns._create_instantly_campaign", return_value="new-camp-1"), \
             patch("app.routers.campaigns._get_all_account_emails", return_value=[]), \
             patch("app.routers.campaigns._load_campaign_json",
                   side_effect=Exception("no template")), \
             patch("app.routers.campaigns._tag_instantly_campaigns", return_value={"new-camp-1": True}), \
             patch("app.routers.campaigns.get_cursor",
                   side_effect=lambda commit=True: _cursor_ctx(cur)):
            resp = client.post("/api/campaigns/setup-instantly")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
