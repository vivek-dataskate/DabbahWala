"""
Tests for app/routers/broadcasts.py
=====================================

Router prefix: /api/broadcasts

Covers:
  - POST /
  - POST /delay-alert
  - POST /{job_id}/queue
  - GET  /
  - GET  /pending-recipients
  - POST /recipients/{id}/sent
  - POST /recipients/{id}/failed
  - GET  /{job_id}

All DB calls are mocked via patch on app.routers.broadcasts.get_cursor.
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
# TestCreateBroadcast
# ---------------------------------------------------------------------------

class TestCreateBroadcast:
    """Tests for POST /."""

    def test_create_sms_broadcast(self, client):
        """POST / creates a blast broadcast and returns status:created with job_id."""
        cur = _make_cursor(fetchone_val={"id": 101, "created_at": "2026-01-01T00:00:00Z"})

        with patch(
            "app.routers.broadcasts.get_cursor",
            side_effect=lambda commit=True: _cursor_ctx(cur),
        ):
            resp = client.post(
                "/api/broadcasts/",
                json={
                    "title": "Eid Special",
                    "broadcast_type": "promotional",
                    "channels": ["sms"],
                    "sms_message": "Hi {first_name}! Eid Mubarak — order today!",
                    "target_type": "all_customers",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "created"
        assert data["job_id"] == 101

    def test_create_missing_title(self, client):
        """POST / without a title field returns 422 validation error."""
        resp = client.post(
            "/api/broadcasts/",
            json={
                "broadcast_type": "promotional",
                "channels": ["sms"],
                "sms_message": "Hi!",
                "target_type": "all_customers",
            },
        )

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# TestDelayAlert
# ---------------------------------------------------------------------------

class TestDelayAlert:
    """Tests for POST /delay-alert."""

    def test_delay_alert(self, client):
        """POST /delay-alert creates and queues a delay alert job."""
        # First fetchone: INSERT RETURNING id
        # Second fetchone: recipient count (None here — handled by populate helper which uses fetchall)
        insert_cur = _make_cursor(fetchone_val={"id": 50})
        recipients_cur = _make_cursor(rows=[], fetchone_val={"id": 50})

        call_count = [0]

        @contextmanager
        def _multi_cursor(commit=True):
            call_count[0] += 1
            if call_count[0] == 1:
                yield insert_cur
            else:
                yield recipients_cur

        with patch(
            "app.routers.broadcasts.get_cursor",
            side_effect=_multi_cursor,
        ):
            resp = client.post(
                "/api/broadcasts/delay-alert",
                json={
                    "sms_message": "Deliveries delayed today — sorry!",
                    "email_subject": "Delivery Delay Notice",
                    "email_body": "<p>We are running late today.</p>",
                    "target_date": "2026-01-15",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "job_id" in data
        assert "target_date" in data


# ---------------------------------------------------------------------------
# TestQueueBroadcast
# ---------------------------------------------------------------------------

class TestQueueBroadcast:
    """Tests for POST /{job_id}/queue."""

    def test_queue_job(self, client):
        """POST /{job_id}/queue for a draft job populates recipients and returns queued status."""
        job_row = {
            "id": 101,
            "title": "Eid Special",
            "broadcast_type": "promotional",
            "channels": ["sms"],
            "target_type": "all_customers",
            "target_date": None,
            "status": "draft",
            "total_recipients": None,
            "sms_message": "Hi!",
            "email_subject": None,
            "email_body": None,
        }
        contacts_cur = _make_cursor(rows=[{"id": 1}, {"id": 2}])

        call_count = [0]

        @contextmanager
        def _multi_cursor(commit=True):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: SELECT * FROM broadcast_jobs WHERE id = %s
                cur = _make_cursor(fetchone_val=job_row)
                yield cur
            else:
                # Subsequent calls: populate recipients, UPDATE
                yield contacts_cur

        with patch(
            "app.routers.broadcasts.get_cursor",
            side_effect=_multi_cursor,
        ):
            resp = client.post("/api/broadcasts/101/queue")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        assert data["job_id"] == 101

    def test_queue_not_found(self, client):
        """POST /{job_id}/queue when job doesn't exist returns 404."""
        cur = _make_cursor(fetchone_val=None)

        with patch(
            "app.routers.broadcasts.get_cursor",
            side_effect=lambda commit=True: _cursor_ctx(cur),
        ):
            resp = client.post("/api/broadcasts/9999/queue")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# TestListBroadcasts
# ---------------------------------------------------------------------------

class TestListBroadcasts:
    """Tests for GET /."""

    def test_list_empty(self, client):
        """GET / with no jobs returns an empty list."""
        cur = _make_cursor(rows=[])

        with patch(
            "app.routers.broadcasts.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.get("/api/broadcasts/")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_list_with_data(self, client):
        """GET / with one job returns a list with one item."""
        row = {
            "id": 101,
            "title": "Eid Special",
            "broadcast_type": "promotional",
            "channels": ["sms"],
            "target_type": "all_customers",
            "target_date": None,
            "status": "sent",
            "total_recipients": 50,
            "sent_sms": 48,
            "sent_email": 0,
            "failed_count": 2,
            "created_by": "admin",
            "created_at": None,
            "started_at": None,
            "completed_at": None,
        }
        cur = _make_cursor(rows=[row])

        with patch(
            "app.routers.broadcasts.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.get("/api/broadcasts/")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1


# ---------------------------------------------------------------------------
# TestRecipientStatus
# ---------------------------------------------------------------------------

class TestRecipientStatus:
    """Tests for POST /recipients/{id}/sent and /recipients/{id}/failed."""

    def test_mark_sent(self, client):
        """POST /recipients/1/sent marks the recipient as sent and returns status ok."""
        cur = MagicMock()
        # First fetchone: UPDATE recipients...RETURNING → job_id, channel
        # Second fetchone: _maybe_complete_job COUNT(*) AS remaining
        cur.fetchone.side_effect = [
            {"job_id": 101, "channel": "sms"},
            {"remaining": 0},
        ]

        with patch(
            "app.routers.broadcasts.get_cursor",
            side_effect=lambda commit=True: _cursor_ctx(cur),
        ):
            resp = client.post("/api/broadcasts/recipients/1/sent")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_mark_failed(self, client):
        """POST /recipients/1/failed marks the recipient as failed and returns status ok."""
        cur = MagicMock()
        # First fetchone: UPDATE recipients...RETURNING → job_id
        # Second fetchone: _maybe_complete_job COUNT(*) AS remaining
        cur.fetchone.side_effect = [
            {"job_id": 101},
            {"remaining": 1},
        ]

        with patch(
            "app.routers.broadcasts.get_cursor",
            side_effect=lambda commit=True: _cursor_ctx(cur),
        ):
            resp = client.post("/api/broadcasts/recipients/1/failed", json={})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# TestGetBroadcast
# ---------------------------------------------------------------------------

class TestGetBroadcast:
    """Tests for GET /{job_id}."""

    def test_get_job(self, client):
        """GET /{job_id} returns job details with progress."""
        job_row = {
            "id": 101,
            "title": "Eid Special",
            "broadcast_type": "promotional",
            "channels": ["sms"],
            "target_type": "all_customers",
            "target_date": None,
            "status": "sent",
            "total_recipients": 50,
            "sent_sms": 48,
            "sent_email": 0,
            "failed_count": 2,
            "created_by": "admin",
            "created_at": None,
            "started_at": None,
            "completed_at": None,
        }

        call_count = [0]

        @contextmanager
        def _multi_cursor(commit=False):
            call_count[0] += 1
            cur = MagicMock()
            if call_count[0] == 1:
                cur.fetchone.return_value = job_row
                cur.fetchall.return_value = []
            else:
                cur.fetchone.return_value = None
                cur.fetchall.return_value = []
            yield cur

        with patch(
            "app.routers.broadcasts.get_cursor",
            side_effect=_multi_cursor,
        ):
            resp = client.get("/api/broadcasts/101")

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 101
        assert data["title"] == "Eid Special"
        assert "progress" in data

    def test_get_job_not_found(self, client):
        """GET /{job_id} returns 404 when job does not exist."""
        cur = MagicMock()
        cur.fetchone.return_value = None

        with patch(
            "app.routers.broadcasts.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.get("/api/broadcasts/9999")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# TestCreateBroadcastValidation
# ---------------------------------------------------------------------------

class TestCreateBroadcastValidation:
    """Additional validation tests for POST /."""

    def test_invalid_broadcast_type_returns_400(self, client):
        """Unknown broadcast_type should return 400."""
        resp = client.post("/api/broadcasts/", json={
            "title": "Bad Blast",
            "broadcast_type": "fax_blast",
            "channels": ["sms"],
            "target_type": "all_customers",
        })
        assert resp.status_code == 400

    def test_invalid_target_type_returns_400(self, client):
        """Unknown target_type should return 400."""
        resp = client.post("/api/broadcasts/", json={
            "title": "Bad Target",
            "broadcast_type": "promotional",
            "channels": ["sms"],
            "target_type": "unknown_target",
        })
        assert resp.status_code == 400

    def test_invalid_channel_returns_400(self, client):
        """Unknown channel name should return 400."""
        resp = client.post("/api/broadcasts/", json={
            "title": "Bad Channel",
            "broadcast_type": "promotional",
            "channels": ["fax"],
            "target_type": "all_customers",
        })
        assert resp.status_code == 400

    def test_delay_alert_with_all_customers_target_returns_400(self, client):
        """delay_alert broadcast_type must use target_type='active_orders'."""
        resp = client.post("/api/broadcasts/", json={
            "title": "Storm",
            "broadcast_type": "delay_alert",
            "channels": ["sms"],
            "target_type": "all_customers",
            "target_date": "2026-02-26",
        })
        assert resp.status_code == 400

    def test_active_orders_without_target_date_returns_400(self, client):
        """active_orders target_type requires target_date."""
        resp = client.post("/api/broadcasts/", json={
            "title": "No Date",
            "broadcast_type": "delay_alert",
            "channels": ["sms"],
            "target_type": "active_orders",
        })
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# TestPendingRecipients
# ---------------------------------------------------------------------------

class TestPendingRecipients:
    """Tests for GET /pending-recipients."""

    def test_returns_empty_list_when_no_pending(self, client):
        """Should return [] when no pending recipients exist."""
        cur = MagicMock()
        cur.fetchall.return_value = []

        with patch(
            "app.routers.broadcasts.get_cursor",
            side_effect=lambda commit=True: _cursor_ctx(cur),
        ):
            resp = client.get("/api/broadcasts/pending-recipients")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_recipients_batch(self, client):
        """Should claim and return recipients with full contact context."""
        claimed_rows = [
            {"recipient_id": 1, "job_id": 5, "contact_id": 100, "channel": "sms"},
        ]
        detail_rows = [
            {
                "recipient_id": 1, "job_id": 5, "channel": "sms",
                "contact_id": 100, "first_name": "Alice", "email": "a@b.com",
                "phone": "+12345678901", "sms_message": "Hi there!",
                "email_subject": None, "email_body": None,
                "broadcast_type": "promotional", "job_title": "Promo Q1",
            }
        ]
        cur = MagicMock()
        cur.fetchall.side_effect = [claimed_rows, detail_rows]

        with patch(
            "app.routers.broadcasts.get_cursor",
            side_effect=lambda commit=True: _cursor_ctx(cur),
        ):
            resp = client.get("/api/broadcasts/pending-recipients")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["channel"] == "sms"


# ---------------------------------------------------------------------------
# TestQueueBroadcastAlreadyQueued
# ---------------------------------------------------------------------------

class TestQueueBroadcastAlreadyQueued:
    """POST /{job_id}/queue when job is already queued should return 400."""

    def test_queue_already_queued_job_returns_400(self, client):
        """Re-queuing a job that is not in draft status should return 400."""
        job_row = {
            "id": 200, "status": "queued", "target_type": "all_customers",
            "channels": ["sms"], "target_date": None,
        }
        cur = _make_cursor(fetchone_val=job_row)
        with patch(
            "app.routers.broadcasts.get_cursor",
            side_effect=lambda commit=True: _cursor_ctx(cur),
        ):
            resp = client.post("/api/broadcasts/200/queue")
        assert resp.status_code == 400
