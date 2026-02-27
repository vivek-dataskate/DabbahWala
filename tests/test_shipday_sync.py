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
        from app.routers.orders import _fetch_order_detail
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
        from app.routers.orders import _store_order_communications
        self._fn = _store_order_communications

    def _make_db_patch(self, cur):
        return patch("app.routers.orders.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur))

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
        from app.routers.orders import _create_feedback_opportunity
        self._fn = _create_feedback_opportunity

    def _make_db_patch(self, cur):
        return patch("app.routers.orders.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur))

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
        with patch("app.routers.orders._run_feedback_sync") as mock_run, \
             patch("app.routers.orders._feedback_sync_state", {"running": False}):
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
        with patch("app.routers.orders._feedback_sync_state", running_state):
            resp = client.post("/api/shipday/sync-feedback", json={"days_back": 3})

        assert resp.status_code == 200
        assert resp.json()["status"] == "already_running"

    def test_synchronous_mode_blocks_and_returns_complete(self, client):
        """run_in_background=False → sync runs inline, returns complete."""
        with patch("app.routers.orders._run_feedback_sync") as mock_run, \
             patch("app.routers.orders._feedback_sync_state", {"running": False, "orders_checked": 2}):
            resp = client.post(
                "/api/shipday/sync-feedback",
                json={"days_back": 7, "run_in_background": False},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "complete"
        mock_run.assert_called_once_with(7, False)

    def test_missing_api_key_during_background_run(self, monkeypatch):
        """If SHIPDAY_API_KEY is not set, the background worker logs and exits cleanly."""
        import importlib
        import app.routers.orders as module

        monkeypatch.delenv("SHIPDAY_API_KEY", raising=False)
        monkeypatch.delenv("SHIPDAY_KEY", raising=False)

        # Reset state
        module._feedback_sync_state["running"] = False

        # Call _run_feedback_sync directly (no HTTP, no DB needed)
        module._run_feedback_sync(days_back=1)

        assert module._feedback_sync_state["running"] is False


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
            "app.routers.orders.get_cursor",
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
            "app.routers.orders.get_cursor",
            side_effect=lambda commit=False: _cursor_ctx(cur),
        ):
            resp = client.get("/api/shipday/feedback-stats")

        assert resp.status_code == 200
        data = resp.json()
        assert data["breakdown"] == []
        assert data["recent_feedback"] == []

    def test_returns_500_on_db_error(self, client):
        with patch(
            "app.routers.orders.get_cursor",
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
        import app.routers.orders as module

        module._feedback_sync_state["running"] = False

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

        with patch("app.routers.orders.get_cursor", side_effect=_multi_cursor), \
             patch("app.routers.orders._fetch_order_detail", return_value=positive_order), \
             patch("app.routers.orders._store_order_communications",
                   return_value=["customer_feedback"]), \
             patch("app.routers.orders._create_feedback_opportunity") as mock_opp:
            module._run_feedback_sync(days_back=3)

        assert module._feedback_sync_state["running"] is False
        assert module._feedback_sync_state["orders_checked"] == 2
        # Both orders had feedback → opportunities created for each
        assert mock_opp.call_count == 2

    def test_handles_fetch_failure_gracefully(self):
        import app.routers.orders as module

        module._feedback_sync_state["running"] = False

        pending = [{"shipday_order_id": "SD-FAIL", "contact_id": 10, "actual_delivery": None}]

        @contextmanager
        def _multi_cursor(commit=False):
            cur = MagicMock()
            cur.fetchall.return_value = pending
            yield cur

        with patch("app.routers.orders.get_cursor", side_effect=_multi_cursor), \
             patch("app.routers.orders._fetch_order_detail", return_value=None):
            module._run_feedback_sync(days_back=1)

        assert module._feedback_sync_state["running"] is False
        assert module._feedback_sync_state["errors"] == 0       # None-return is not an error
        assert module._feedback_sync_state["orders_with_comms"] == 0

    def test_increments_error_count_on_store_exception(self):
        import app.routers.orders as module

        module._feedback_sync_state["running"] = False

        pending = [{"shipday_order_id": "SD-ERR", "contact_id": 11, "actual_delivery": None}]

        @contextmanager
        def _multi_cursor(commit=False):
            cur = MagicMock()
            cur.fetchall.return_value = pending
            yield cur

        with patch("app.routers.orders.get_cursor", side_effect=_multi_cursor), \
             patch("app.routers.orders._fetch_order_detail", return_value={"orderId": "SD-ERR", "feedback": "ok"}), \
             patch("app.routers.orders._store_order_communications",
                   side_effect=Exception("DB write failed")):
            module._run_feedback_sync(days_back=1)

        assert module._feedback_sync_state["running"] is False
        assert module._feedback_sync_state["errors"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# 8. Additional unit tests for uncovered paths
# ─────────────────────────────────────────────────────────────────────────────

class TestGetShipdayKeyRaises:
    def test_get_shipday_key_raises_when_no_key(self, monkeypatch):
        """_get_shipday_key raises HTTPException when no API key env var is set."""
        from fastapi import HTTPException as FastAPIHTTPException
        monkeypatch.delenv("SHIPDAY_API_KEY", raising=False)
        monkeypatch.delenv("SHIPDAY_KEY", raising=False)
        from app.routers.shipday_sync import _get_shipday_key
        with pytest.raises(FastAPIHTTPException) as exc_info:
            _get_shipday_key()
        assert exc_info.value.status_code == 500

    def test_get_shipday_key_returns_key_when_set(self, monkeypatch):
        """_get_shipday_key returns the key when SHIPDAY_API_KEY is set."""
        monkeypatch.setenv("SHIPDAY_API_KEY", "test-shipday-key-123")
        from app.routers.shipday_sync import _get_shipday_key
        assert _get_shipday_key() == "test-shipday-key-123"


class TestFetchOrderDetailHTTPStatusError:
    def test_returns_none_on_http_status_error(self):
        """_fetch_order_detail returns None when HTTPStatusError is raised."""
        import httpx
        from app.routers.shipday_sync import _fetch_order_detail

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        error_resp = MagicMock()
        error_resp.status_code = 500
        error_resp.text = "Internal Server Error"

        mock_client = MagicMock()
        mock_client.get.return_value = MagicMock(
            status_code=500,
            raise_for_status=MagicMock(
                side_effect=httpx.HTTPStatusError(
                    "Server Error", request=MagicMock(), response=error_resp
                )
            )
        )
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch("app.routers.shipday_sync.httpx.Client", return_value=mock_client):
            result = _fetch_order_detail("test-key", "SD-500")

        assert result is None


class TestRunFeedbackSyncSentimentCounts:
    def test_positive_negative_neutral_counters_incremented(self):
        """_run_feedback_sync increments positive/negative/neutral counters correctly."""
        import app.routers.shipday_sync as module

        module._sync_state["running"] = False

        pending = [
            {"shipday_order_id": "SD-POS", "contact_id": 1, "actual_delivery": None},
            {"shipday_order_id": "SD-NEG", "contact_id": 2, "actual_delivery": None},
            {"shipday_order_id": "SD-NEU", "contact_id": 3, "actual_delivery": None},
        ]

        sentiments = {"SD-POS": "positive", "SD-NEG": "negative", "SD-NEU": "neutral"}
        fetchone_returns = {
            "SD-POS": {"sentiment": "positive"},
            "SD-NEG": {"sentiment": "negative"},
            "SD-NEU": {"sentiment": "neutral"},
        }

        call_count = [0]

        @contextmanager
        def _multi_cursor(commit=False):
            cur = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                cur.fetchall.return_value = pending
            else:
                # Determine which order we're on
                idx = call_count[0] - 2
                order_id = pending[idx % len(pending)]["shipday_order_id"]
                cur.fetchone.return_value = fetchone_returns.get(order_id, {"sentiment": "neutral"})
            yield cur

        def fake_fetch(api_key, order_id):
            return {"orderId": order_id, "feedback": "Some feedback"}

        def fake_store(order_id, order, occurred_at):
            return ["customer_feedback"]

        with patch("app.routers.shipday_sync.get_cursor", side_effect=_multi_cursor), \
             patch("app.routers.shipday_sync._fetch_order_detail", side_effect=fake_fetch), \
             patch("app.routers.shipday_sync._store_order_communications", side_effect=fake_store), \
             patch("app.routers.shipday_sync._create_feedback_opportunity"), \
             patch("app.routers.shipday_sync.time.sleep"):
            module._run_feedback_sync(days_back=7)

        assert module._sync_state["running"] is False
        assert module._sync_state["feedback_positive"] == 1
        assert module._sync_state["feedback_negative"] == 1
        assert module._sync_state["feedback_neutral"] == 1
        assert module._sync_state["opportunities_created"] == 2  # pos + neg


class TestRunFeedbackSyncOuterException:
    def test_outer_exception_is_caught_and_logged(self):
        """_run_feedback_sync catches unexpected exception from outer DB query."""
        import app.routers.shipday_sync as module

        module._sync_state["running"] = False

        @contextmanager
        def _crashing_cursor(commit=False):
            raise Exception("Outer DB crash!")
            yield  # pragma: no cover

        with patch("app.routers.shipday_sync.get_cursor", side_effect=_crashing_cursor), \
             patch.dict("os.environ", {"SHIPDAY_API_KEY": "test-key"}):
            module._run_feedback_sync(days_back=7)

        assert module._sync_state["running"] is False
        assert module._sync_state["errors"] >= 1


class TestShipdaySyncEndpoints:
    """Test the endpoints in shipday_sync.py via a dedicated TestClient."""

    @pytest.fixture
    def shipday_client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.routers.shipday_sync import router
        app = FastAPI()
        app.include_router(router)
        return TestClient(app, raise_server_exceptions=False)

    def test_sync_feedback_already_running(self, shipday_client):
        """sync_feedback returns already_running when _sync_state running=True."""
        import app.routers.shipday_sync as module
        module._sync_state["running"] = True
        try:
            resp = shipday_client.post("/sync-feedback", json={"days_back": 3})
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "already_running"
        finally:
            module._sync_state["running"] = False

    def test_sync_feedback_starts_background(self, shipday_client):
        """sync_feedback with run_in_background=True returns 'started'."""
        import app.routers.shipday_sync as module
        module._sync_state["running"] = False
        with patch("app.routers.shipday_sync._run_feedback_sync"):
            resp = shipday_client.post("/sync-feedback", json={"days_back": 5})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert data["days_back"] == 5

    def test_sync_feedback_synchronous_mode(self, shipday_client):
        """sync_feedback with run_in_background=False runs inline."""
        import app.routers.shipday_sync as module
        module._sync_state["running"] = False
        with patch("app.routers.shipday_sync._run_feedback_sync") as mock_run:
            resp = shipday_client.post(
                "/sync-feedback",
                json={"days_back": 3, "run_in_background": False}
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "complete"
        mock_run.assert_called_once_with(3, False)

    def test_feedback_stats_returns_data(self, shipday_client):
        """feedback_stats returns breakdown and recent_feedback."""
        breakdown_rows = [
            {"comm_type": "customer_feedback", "sentiment": "positive", "count": 5, "latest": None}
        ]
        recent_rows = [
            {"id": 1, "shipday_order_id": "SD-1", "order_number": "ORD-1",
             "content": "Great!", "sentiment": "positive", "occurred_at": None,
             "email": "a@b.com", "full_name": "Ali Khan", "phone": "+14041234567"}
        ]
        cur = MagicMock()
        cur.fetchall.side_effect = [breakdown_rows, recent_rows]

        with patch("app.routers.shipday_sync.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = shipday_client.get("/feedback-stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "breakdown" in data
        assert "recent_feedback" in data

    def test_feedback_stats_empty(self, shipday_client):
        """feedback_stats with empty DB returns empty lists."""
        cur = MagicMock()
        cur.fetchall.side_effect = [[], []]
        with patch("app.routers.shipday_sync.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = shipday_client.get("/feedback-stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["breakdown"] == []
        assert data["recent_feedback"] == []

    def test_feedback_stats_db_error_returns_500(self, shipday_client):
        """feedback_stats with DB error returns 500."""
        with patch("app.routers.shipday_sync.get_cursor",
                   side_effect=Exception("DB failure")):
            resp = shipday_client.get("/feedback-stats")
        assert resp.status_code == 500

    def test_feedback_stats_serializes_datetimes(self, shipday_client):
        """feedback_stats converts datetime objects to ISO strings."""
        from datetime import datetime, timezone as tz
        dt = datetime(2026, 1, 15, 10, 0, 0, tzinfo=tz.utc)
        breakdown_rows = [
            {"comm_type": "customer_feedback", "sentiment": "positive",
             "count": 3, "latest": dt}   # datetime → triggers isoformat()
        ]
        recent_rows = [
            {"id": 1, "shipday_order_id": "SD-1", "order_number": "ORD-1",
             "content": "Good!", "sentiment": "positive", "occurred_at": dt,
             "email": "a@b.com", "full_name": "Test User", "phone": "+14041234567"}
        ]
        cur = MagicMock()
        cur.fetchall.side_effect = [breakdown_rows, recent_rows]

        with patch("app.routers.shipday_sync.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = shipday_client.get("/feedback-stats")
        assert resp.status_code == 200
        data = resp.json()
        # The datetime should have been serialized as a string
        assert isinstance(data["breakdown"][0]["latest"], str)
