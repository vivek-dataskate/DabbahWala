"""
Tests for app/routers/shipday_sync.py
======================================

Covers:
  Unit tests  (no HTTP/DB needed)
    - classify_sentiment logic (Python mirror of the SQL function)
    - _fetch_order_detail  (mocked httpx)
    - _store_order_communications logic (mocked DB)
    - _create_feedback_opportunity logic (mocked DB)
    - _run_feedback_sync state machine (mocked DB + httpx)

  Endpoint tests  (TestClient with mocked DB)
    - POST /api/shipday/sync-feedback  — start / already_running / sync
    - GET  /api/shipday/feedback-stats — happy path / empty / DB error

Run with:
    pip install -r requirements-dev.txt
    pytest tests/test_shipday_sync.py -v
"""

import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch, call

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Helpers / stubs
# ─────────────────────────────────────────────────────────────────────────────

SAMPLE_ORDER_WITH_FEEDBACK = {
    "orderId": "SD-001",
    "orderNumber": "ORD-001",
    "feedback": "Great food, loved it!",
    "deliveryInstruction": "Leave at door please",
    "proofOfDelivery": {
        "signaturePath": "https://cdn.shipday.com/sig/001.png",
        "picturePaths": ["https://cdn.shipday.com/pic/001a.jpg"],
        "lat": 25.2048,
        "lng": 55.2708,
    },
}

SAMPLE_ORDER_NO_FEEDBACK = {
    "orderId": "SD-002",
    "orderNumber": "ORD-002",
    "feedback": "",
    "deliveryInstruction": "",
    "proofOfDelivery": {},
}

SAMPLE_ORDER_NEGATIVE = {
    "orderId": "SD-003",
    "orderNumber": "ORD-003",
    "feedback": "Food was cold and late, terrible experience.",
    "deliveryInstruction": "",
    "proofOfDelivery": {},
}


def _make_cursor(rows=None, fetchone_val=None):
    c = MagicMock()
    c.fetchall.return_value = rows or []
    c.fetchone.return_value = fetchone_val
    return c


@contextmanager
def _cursor_ctx(cur):
    yield cur


# ─────────────────────────────────────────────────────────────────────────────
# 1. classify_sentiment  (Python mirror of the SQL keyword logic)
# ─────────────────────────────────────────────────────────────────────────────

import re

POSITIVE_PATTERN = re.compile(
    r"(great|amazing|excellent|perfect|love|happy|delicious|fantastic|wonderful|"
    r"thank|awesome|best|fresh|on.?time|early|quick|fast|friendly|kind|helpful|"
    r"pleased|satisfied|enjoyed|superb|incredible|outstanding)"
)
NEGATIVE_PATTERN = re.compile(
    r"(bad|terrible|awful|horrible|late|cold|wrong|disappoint|never|complain|"
    r"rude|slow|missing|poor|broken|dirty|spoiled|damaged|worst|unacceptable|"
    r"not good|not great|never again|refund|complaint)"
)


def py_classify_sentiment(text: str) -> str:
    """Python mirror of the classify_sentiment() SQL function."""
    lower = (text or "").lower()
    if POSITIVE_PATTERN.search(lower):
        return "positive"
    if NEGATIVE_PATTERN.search(lower):
        return "negative"
    return "neutral"


class TestClassifySentiment:
    def test_positive_keywords(self):
        assert py_classify_sentiment("Great food, loved it!") == "positive"
        assert py_classify_sentiment("Amazing delivery, thank you!") == "positive"
        assert py_classify_sentiment("On time and fresh") == "positive"
        assert py_classify_sentiment("I enjoyed the meal, it was excellent") == "positive"

    def test_negative_keywords(self):
        assert py_classify_sentiment("Food was cold and late") == "negative"
        assert py_classify_sentiment("Terrible experience, never again") == "negative"
        assert py_classify_sentiment("Awful, food was wrong and horrible") == "negative"
        assert py_classify_sentiment("Missing items, very disappointed") == "negative"

    def test_neutral_keywords(self):
        assert py_classify_sentiment("Delivered.") == "neutral"
        assert py_classify_sentiment("OK") == "neutral"
        assert py_classify_sentiment("") == "neutral"

    def test_empty_and_none(self):
        assert py_classify_sentiment("") == "neutral"
        assert py_classify_sentiment(None) == "neutral"  # type: ignore

    def test_case_insensitive(self):
        assert py_classify_sentiment("GREAT FOOD") == "positive"
        assert py_classify_sentiment("TERRIBLE") == "negative"

    def test_positive_wins_if_listed_first(self):
        # "great" appears → positive (positive pattern checked first)
        result = py_classify_sentiment("Great but also a complaint noted")
        assert result == "positive"


# ─────────────────────────────────────────────────────────────────────────────
# 2. _fetch_order_detail
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchOrderDetail:
    def setup_method(self):
        # Import lazily so monkeypatching has already happened
        from app.routers.shipday_sync import _fetch_order_detail
        self._fn = _fetch_order_detail

    def test_returns_order_on_200(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = SAMPLE_ORDER_WITH_FEEDBACK
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as MockClient:
            mock_client_instance = MagicMock()
            MockClient.return_value.__enter__ = MagicMock(return_value=mock_client_instance)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            mock_client_instance.get.return_value = mock_resp

            result = self._fn("test_key", "SD-001")

        assert result == SAMPLE_ORDER_WITH_FEEDBACK
        mock_client_instance.get.assert_called_once_with(
            "https://api.shipday.com/orders/SD-001",
            headers={"Authorization": "Basic test_key", "Content-Type": "application/json"},
        )

    def test_returns_none_on_404(self):
        import httpx

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch("httpx.Client") as MockClient:
            mock_client_instance = MagicMock()
            MockClient.return_value.__enter__ = MagicMock(return_value=mock_client_instance)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            mock_client_instance.get.return_value = mock_resp

            result = self._fn("test_key", "SD-MISSING")

        assert result is None

    def test_returns_none_on_http_error(self):
        import httpx

        with patch("httpx.Client") as MockClient:
            mock_client_instance = MagicMock()
            MockClient.return_value.__enter__ = MagicMock(return_value=mock_client_instance)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            mock_client_instance.get.side_effect = httpx.TimeoutException("timeout")

            result = self._fn("test_key", "SD-TIMEOUT")

        assert result is None

    def test_returns_none_on_network_error(self):
        with patch("httpx.Client") as MockClient:
            mock_client_instance = MagicMock()
            MockClient.return_value.__enter__ = MagicMock(return_value=mock_client_instance)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            mock_client_instance.get.side_effect = Exception("connection refused")

            result = self._fn("test_key", "SD-ERR")

        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# 3. _store_order_communications
# ─────────────────────────────────────────────────────────────────────────────

class TestStoreOrderCommunications:
    def setup_method(self):
        from app.routers.shipday_sync import _store_order_communications
        self._fn = _store_order_communications

    def _make_db_patch(self, cur):
        return patch("app.routers.shipday_sync.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur))

    def test_stores_feedback_instruction_and_pod(self):
        cur = _make_cursor()
        with self._make_db_patch(cur):
            result = self._fn("SD-001", SAMPLE_ORDER_WITH_FEEDBACK, "2026-02-18T14:00:00Z")

        assert "customer_feedback" in result
        assert "delivery_instruction" in result
        assert "proof_of_delivery" in result
        assert cur.execute.call_count == 3

    def test_stores_only_feedback_when_no_pod_or_instr(self):
        cur = _make_cursor()
        order = {"orderId": "SD-X", "feedback": "Loved it!", "deliveryInstruction": "", "proofOfDelivery": {}}
        with self._make_db_patch(cur):
            result = self._fn("SD-X", order, None)

        assert result == ["customer_feedback"]
        assert cur.execute.call_count == 1

    def test_stores_nothing_when_all_empty(self):
        cur = _make_cursor()
        with self._make_db_patch(cur):
            result = self._fn("SD-002", SAMPLE_ORDER_NO_FEEDBACK, None)

        assert result == []
        cur.execute.assert_not_called()

    def test_stores_pod_with_only_picture_paths(self):
        cur = _make_cursor()
        order = {
            "orderId": "SD-Y",
            "feedback": "",
            "deliveryInstruction": "",
            "proofOfDelivery": {"picturePaths": ["https://cdn.shipday.com/pic.jpg"], "signaturePath": ""},
        }
        with self._make_db_patch(cur):
            result = self._fn("SD-Y", order, None)

        assert "proof_of_delivery" in result

    def test_feedback_sql_call_contains_correct_type(self):
        cur = _make_cursor()
        with self._make_db_patch(cur):
            self._fn("SD-001", SAMPLE_ORDER_WITH_FEEDBACK, None)

        # First execute call should be for customer_feedback
        first_call_args = cur.execute.call_args_list[0][0]
        assert "customer_feedback" in first_call_args[0]


# ─────────────────────────────────────────────────────────────────────────────
# 4. _create_feedback_opportunity
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateFeedbackOpportunity:
    def setup_method(self):
        from app.routers.shipday_sync import _create_feedback_opportunity
        self._fn = _create_feedback_opportunity

    def _make_db_patch(self, cur):
        return patch("app.routers.shipday_sync.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur))

    def test_creates_hot_opportunity_for_negative(self):
        cur = _make_cursor()
        with self._make_db_patch(cur):
            self._fn(42, "SD-003", "negative")

        cur.execute.assert_called_once()
        sql_args = cur.execute.call_args[0]
        assert "hot" in sql_args[1]
        assert "apolog" in sql_args[1][2].lower() or "recover" in sql_args[1][2].lower()

    def test_creates_warm_opportunity_for_positive(self):
        cur = _make_cursor()
        with self._make_db_patch(cur):
            self._fn(42, "SD-001", "positive")

        cur.execute.assert_called_once()
        sql_args = cur.execute.call_args[0]
        assert "warm" in sql_args[1]

    def test_skips_neutral_sentiment(self):
        cur = _make_cursor()
        with self._make_db_patch(cur):
            self._fn(42, "SD-999", "neutral")

        # No DB call for neutral
        cur.execute.assert_not_called()

    def test_handles_db_exception_gracefully(self):
        cur = _make_cursor()
        cur.execute.side_effect = Exception("DB error")
        with self._make_db_patch(cur):
            # Should not raise — exception is caught and logged
            self._fn(42, "SD-003", "negative")


# ─────────────────────────────────────────────────────────────────────────────
# 5. Endpoint: POST /api/shipday/sync-feedback
# ─────────────────────────────────────────────────────────────────────────────

class TestSyncFeedbackEndpoint:
    def test_starts_background_sync_by_default(self, client):
        """Default run_in_background=True → returns 'started' immediately."""
        with patch("app.routers.shipday_sync._run_feedback_sync") as mock_run, \
             patch("app.routers.shipday_sync._sync_state", {"running": False}):
            resp = client.post("/api/shipday/sync-feedback", json={"days_back": 3})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert data["days_back"] == 3

    def test_returns_already_running_if_in_progress(self, client):
        """While a sync is running, subsequent calls return 'already_running'."""
        running_state = {
            "running": True,
            "started_at": "2026-02-20T10:00:00Z",
            "orders_checked": 5,
        }
        with patch("app.routers.shipday_sync._sync_state", running_state):
            resp = client.post("/api/shipday/sync-feedback", json={"days_back": 3})

        assert resp.status_code == 200
        assert resp.json()["status"] == "already_running"

    def test_synchronous_mode_blocks_and_returns_complete(self, client):
        """run_in_background=False → sync runs inline, returns complete."""
        with patch("app.routers.shipday_sync._run_feedback_sync") as mock_run, \
             patch("app.routers.shipday_sync._sync_state", {"running": False, "orders_checked": 2}):
            resp = client.post(
                "/api/shipday/sync-feedback",
                json={"days_back": 7, "run_in_background": False},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "complete"
        mock_run.assert_called_once_with(7)

    def test_missing_api_key_during_background_run(self, monkeypatch):
        """If SHIPDAY_API_KEY is not set, the background worker logs and exits cleanly."""
        import importlib
        import app.routers.shipday_sync as module

        monkeypatch.delenv("SHIPDAY_API_KEY", raising=False)
        monkeypatch.delenv("SHIPDAY_KEY", raising=False)

        # Reset state
        module._sync_state["running"] = False

        # Call _run_feedback_sync directly (no HTTP, no DB needed)
        module._run_feedback_sync(days_back=1)

        assert module._sync_state["running"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 6. Endpoint: GET /api/shipday/feedback-stats
# ─────────────────────────────────────────────────────────────────────────────

class TestFeedbackStatsEndpoint:
    def test_returns_breakdown_and_recent(self, client):
        breakdown_rows = [
            {"comm_type": "customer_feedback", "sentiment": "positive", "count": 10, "latest": None},
            {"comm_type": "customer_feedback", "sentiment": "negative", "count": 3,  "latest": None},
        ]
        recent_rows = [
            {
                "id": 1, "shipday_order_id": "SD-001", "order_number": "ORD-001",
                "content": "Great!", "sentiment": "positive", "occurred_at": None,
                "email": "test@example.com", "full_name": "Test User", "phone": "+971501234567",
            }
        ]

        # We need fetchall to return different values on consecutive calls
        cur = MagicMock()
        cur.fetchall.side_effect = [breakdown_rows, recent_rows]

        with patch(
            "app.routers.shipday_sync.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.get("/api/shipday/feedback-stats")

        assert resp.status_code == 200
        data = resp.json()
        assert "breakdown" in data
        assert "recent_feedback" in data
        assert "sync_state" in data
        assert len(data["breakdown"]) == 2
        assert len(data["recent_feedback"]) == 1
        assert data["recent_feedback"][0]["sentiment"] == "positive"

    def test_returns_empty_lists_when_no_data(self, client):
        cur = MagicMock()
        cur.fetchall.side_effect = [[], []]

        with patch(
            "app.routers.shipday_sync.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.get("/api/shipday/feedback-stats")

        assert resp.status_code == 200
        data = resp.json()
        assert data["breakdown"] == []
        assert data["recent_feedback"] == []

    def test_returns_500_on_db_error(self, client):
        with patch(
            "app.routers.shipday_sync.get_cursor",
            side_effect=Exception("connection refused"),
        ):
            resp = client.get("/api/shipday/feedback-stats")

        assert resp.status_code == 500
        assert "detail" in resp.json()


# ─────────────────────────────────────────────────────────────────────────────
# 7. _run_feedback_sync  — integration-style unit test
# ─────────────────────────────────────────────────────────────────────────────

class TestRunFeedbackSync:
    """
    Test the background worker end-to-end with mocked DB and Shipday HTTP.
    Does NOT hit Postgres or the real Shipday API.
    """

    def _pending_rows(self):
        """Simulate two delivered orders pending feedback sync."""
        from datetime import datetime, timezone

        class FakeRow(dict):
            pass

        row1 = FakeRow({"shipday_order_id": "SD-001", "contact_id": 1, "actual_delivery": None})
        row2 = FakeRow({"shipday_order_id": "SD-002", "contact_id": 2, "actual_delivery": None})
        return [row1, row2]

    def test_syncs_two_orders_with_positive_feedback(self):
        import app.routers.shipday_sync as module

        module._sync_state["running"] = False

        pending = self._pending_rows()

        # DB cursor for the SELECT (returns pending orders)
        select_cur = MagicMock()
        select_cur.fetchall.return_value = pending
        select_cur.__enter__ = MagicMock(return_value=select_cur)
        select_cur.__exit__ = MagicMock(return_value=False)

        # DB cursor for the store + sentiment SELECT + opportunity INSERT
        store_cur = MagicMock()
        store_cur.fetchone.return_value = {"sentiment": "positive"}
        store_cur.__enter__ = MagicMock(return_value=store_cur)
        store_cur.__exit__ = MagicMock(return_value=False)

        call_count = [0]

        @contextmanager
        def _multi_cursor(commit=False):
            call_count[0] += 1
            if call_count[0] == 1:
                yield select_cur
            else:
                yield store_cur

        positive_order = {
            "orderId": "SD-001",
            "feedback": "Amazing food, loved it!",
            "deliveryInstruction": "",
            "proofOfDelivery": {},
        }

        with patch("app.routers.shipday_sync.get_cursor", side_effect=_multi_cursor), \
             patch("app.routers.shipday_sync._fetch_order_detail", return_value=positive_order), \
             patch("app.routers.shipday_sync._store_order_communications",
                   return_value=["customer_feedback"]), \
             patch("app.routers.shipday_sync._create_feedback_opportunity") as mock_opp:
            module._run_feedback_sync(days_back=3)

        assert module._sync_state["running"] is False
        assert module._sync_state["orders_checked"] == 2
        # Both orders had feedback → opportunities created for each
        assert mock_opp.call_count == 2

    def test_handles_fetch_failure_gracefully(self):
        import app.routers.shipday_sync as module

        module._sync_state["running"] = False

        pending = [{"shipday_order_id": "SD-FAIL", "contact_id": 10, "actual_delivery": None}]

        @contextmanager
        def _multi_cursor(commit=False):
            cur = MagicMock()
            cur.fetchall.return_value = pending
            yield cur

        with patch("app.routers.shipday_sync.get_cursor", side_effect=_multi_cursor), \
             patch("app.routers.shipday_sync._fetch_order_detail", return_value=None):
            module._run_feedback_sync(days_back=1)

        assert module._sync_state["running"] is False
        assert module._sync_state["errors"] == 0       # None-return is not an error
        assert module._sync_state["orders_with_comms"] == 0

    def test_increments_error_count_on_store_exception(self):
        import app.routers.shipday_sync as module

        module._sync_state["running"] = False

        pending = [{"shipday_order_id": "SD-ERR", "contact_id": 11, "actual_delivery": None}]

        @contextmanager
        def _multi_cursor(commit=False):
            cur = MagicMock()
            cur.fetchall.return_value = pending
            yield cur

        with patch("app.routers.shipday_sync.get_cursor", side_effect=_multi_cursor), \
             patch("app.routers.shipday_sync._fetch_order_detail", return_value={"orderId": "SD-ERR", "feedback": "ok"}), \
             patch("app.routers.shipday_sync._store_order_communications",
                   side_effect=Exception("DB write failed")):
            module._run_feedback_sync(days_back=1)

        assert module._sync_state["running"] is False
        assert module._sync_state["errors"] == 1
