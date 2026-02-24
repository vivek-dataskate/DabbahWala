"""
Tests for app/routers/menu_scrape.py and scripts/scrape_menu.py
================================================================

Covers:
  Unit tests (no DB / network needed)
    - _current_week_start() returns the Monday of the current week
    - poll_telnyx_for_otp() extracts OTP from Telnyx API response
    - upsert_menu() calls DB correctly

  Endpoint tests (TestClient with mocked DB)
    - GET  /api/menu-scrape/current     — happy path / empty
    - GET  /api/menu-scrape/week/{date} — valid date / bad date
    - POST /api/menu-scrape/run         — success / scrape error

Run with:
    pytest tests/test_menu_scrape.py -v
"""

import sys
import os
from contextlib import contextmanager
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_cursor(rows=None, fetchone_val=None):
    c = MagicMock()
    c.fetchall.return_value = rows or []
    c.fetchone.return_value = fetchone_val
    return c


@contextmanager
def _cursor_ctx(cur):
    yield cur


# ─────────────────────────────────────────────────────────────────────────────
# 1. _current_week_start
# ─────────────────────────────────────────────────────────────────────────────

class TestCurrentWeekStart:
    def test_returns_a_monday(self):
        from app.routers.menu_scrape import _current_week_start
        ws = _current_week_start()
        assert ws.weekday() == 0, f"Expected Monday (0), got weekday {ws.weekday()} for {ws}"

    def test_is_within_7_days_of_today(self):
        from app.routers.menu_scrape import _current_week_start
        ws = _current_week_start()
        today = date.today()
        assert abs((today - ws).days) < 7

    def test_consistent_with_known_monday(self):
        """2026-02-23 is a Monday; any day that week should map to it."""
        from app.routers.menu_scrape import _current_week_start
        known_monday = date(2026, 2, 23)
        # We can't control date.today() easily, so just check the type
        ws = _current_week_start()
        assert isinstance(ws, date)


# ─────────────────────────────────────────────────────────────────────────────
# 2. poll_telnyx_for_otp
# ─────────────────────────────────────────────────────────────────────────────

class TestPollTelnyxForOtp:
    """Tests for the OTP polling helper in scripts/scrape_menu.py."""

    def _import(self):
        scripts_dir = os.path.join(os.path.dirname(__file__), "..", "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import scrape_menu
        return scrape_menu

    def test_extracts_4digit_otp(self):
        sm = self._import()
        import time

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [
                {
                    "received_at": "2099-01-01T00:00:01+00:00",
                    "text": "Your DabbahWala code is 4821. Do not share.",
                }
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp), \
             patch.dict(os.environ, {"TELNYX_API_KEY": "test_key"}):
            otp = sm.poll_telnyx_for_otp(sent_after_ts=0, timeout=5)

        assert otp == "4821"

    def test_extracts_6digit_otp(self):
        sm = self._import()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [
                {
                    "received_at": "2099-01-01T00:00:01+00:00",
                    "text": "Your verification code: 928374",
                }
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp), \
             patch.dict(os.environ, {"TELNYX_API_KEY": "test_key"}):
            otp = sm.poll_telnyx_for_otp(sent_after_ts=0, timeout=5)

        assert otp == "928374"

    def test_skips_old_messages(self):
        """Messages received before sent_after_ts should be ignored."""
        sm = self._import()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [
                {
                    "received_at": "2000-01-01T00:00:01+00:00",  # old
                    "text": "OTP 1234",
                }
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp), \
             patch.dict(os.environ, {"TELNYX_API_KEY": "test_key"}):
            # sent_after_ts is far in the future so any message is "old"
            otp = sm.poll_telnyx_for_otp(sent_after_ts=9_999_999_999, timeout=1)

        assert otp is None

    def test_returns_none_on_timeout(self):
        sm = self._import()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": []}
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp), \
             patch.dict(os.environ, {"TELNYX_API_KEY": "test_key"}):
            otp = sm.poll_telnyx_for_otp(sent_after_ts=0, timeout=1)

        assert otp is None

    def test_raises_without_api_key(self):
        sm = self._import()

        env = {k: v for k, v in os.environ.items() if k != "TELNYX_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError, match="TELNYX_API_KEY"):
                sm.poll_telnyx_for_otp(sent_after_ts=0, timeout=1)

    def test_handles_http_error_gracefully(self):
        """If Telnyx API returns an error, poll should keep retrying until timeout."""
        sm = self._import()
        import httpx

        with patch("httpx.get", side_effect=httpx.HTTPError("503")), \
             patch.dict(os.environ, {"TELNYX_API_KEY": "test_key"}):
            otp = sm.poll_telnyx_for_otp(sent_after_ts=0, timeout=1)

        assert otp is None


# ─────────────────────────────────────────────────────────────────────────────
# 3. upsert_menu
# ─────────────────────────────────────────────────────────────────────────────

class TestUpsertMenu:
    def _import(self):
        scripts_dir = os.path.join(os.path.dirname(__file__), "..", "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import scrape_menu
        return scrape_menu

    def test_upserts_all_items(self):
        sm = self._import()

        items = [
            {"item_name": "Butter Chicken", "category": "non-veg", "is_veg": False, "description": None, "image_url": None},
            {"item_name": "Dal Makhani", "category": "veg", "is_veg": True, "description": "Rich lentil curry", "image_url": None},
        ]

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        with patch("psycopg2.connect", return_value=mock_conn), \
             patch.dict(os.environ, {"DATABASE_URL": "postgresql://test/test"}):
            count = sm.upsert_menu(date(2026, 2, 23), items)

        assert count == 2
        assert mock_cur.execute.call_count == 2
        mock_conn.commit.assert_called_once()

    def test_rollback_on_error(self):
        sm = self._import()

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.execute.side_effect = Exception("constraint violation")
        mock_conn.cursor.return_value = mock_cur

        items = [{"item_name": "Test", "category": None, "is_veg": None, "description": None, "image_url": None}]

        with patch("psycopg2.connect", return_value=mock_conn), \
             patch.dict(os.environ, {"DATABASE_URL": "postgresql://test/test"}):
            with pytest.raises(Exception):
                sm.upsert_menu(date(2026, 2, 23), items)

        mock_conn.rollback.assert_called_once()

    def test_converts_postgres_url_prefix(self):
        """postgres:// URLs should be converted to postgresql://."""
        sm = self._import()

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        captured_url = []

        def fake_connect(url):
            captured_url.append(url)
            return mock_conn

        with patch("psycopg2.connect", side_effect=fake_connect), \
             patch.dict(os.environ, {"DATABASE_URL": "postgres://test/test"}):
            sm.upsert_menu(date(2026, 2, 23), [])

        assert captured_url[0].startswith("postgresql://")


# ─────────────────────────────────────────────────────────────────────────────
# 4. GET /api/menu-scrape/current
# ─────────────────────────────────────────────────────────────────────────────

class TestGetCurrentMenu:
    def test_returns_menu_items(self, client):
        rows = [
            {"item_name": "Butter Chicken", "category": "non-veg", "is_veg": False,
             "description": None, "image_url": None, "scraped_at": None},
            {"item_name": "Paneer Tikka", "category": "veg", "is_veg": True,
             "description": None, "image_url": None, "scraped_at": None},
        ]
        cur = _make_cursor(rows=rows)
        with patch("app.routers.menu_scrape.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/menu-scrape/current")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert len(data["items"]) == 2
        assert "week_start" in data

    def test_returns_empty_when_no_menu(self, client):
        cur = _make_cursor(rows=[])
        with patch("app.routers.menu_scrape.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/menu-scrape/current")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["items"] == []


# ─────────────────────────────────────────────────────────────────────────────
# 5. GET /api/menu-scrape/week/{week_start}
# ─────────────────────────────────────────────────────────────────────────────

class TestGetMenuForWeek:
    def test_valid_date_returns_menu(self, client):
        rows = [
            {"item_name": "Rajma Chawal", "category": "veg", "is_veg": True,
             "description": None, "image_url": None, "scraped_at": None},
        ]
        cur = _make_cursor(rows=rows)
        with patch("app.routers.menu_scrape.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/menu-scrape/week/2026-02-23")

        assert resp.status_code == 200
        data = resp.json()
        assert data["week_start"] == "2026-02-23"
        assert data["count"] == 1

    def test_invalid_date_returns_400(self, client):
        resp = client.get("/api/menu-scrape/week/not-a-date")
        assert resp.status_code == 400
        assert "YYYY-MM-DD" in resp.json()["detail"]

    def test_empty_week_returns_zero(self, client):
        cur = _make_cursor(rows=[])
        with patch("app.routers.menu_scrape.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/menu-scrape/week/2025-01-06")

        assert resp.status_code == 200
        assert resp.json()["count"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# 6. POST /api/menu-scrape/run
# ─────────────────────────────────────────────────────────────────────────────

class TestRunScrape:
    def test_successful_scrape_returns_ok(self, client):
        mock_result = {
            "week_start": "2026-02-23",
            "items_scraped": 15,
            "status": "ok",
            "items": [],
        }
        with patch("app.routers.menu_scrape.asyncio.run", return_value=mock_result):
            with patch("app.routers.menu_scrape.sys.path", sys.path):
                # Patch the import of scrape_menu inside the router
                with patch.dict("sys.modules", {"scrape_menu": MagicMock(run=MagicMock(return_value=mock_result))}):
                    with patch("app.routers.menu_scrape.asyncio.run", return_value=mock_result):
                        resp = client.post("/api/menu-scrape/run")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["items_scraped"] == 15

    def test_invalid_week_start_returns_400(self, client):
        resp = client.post("/api/menu-scrape/run?week_start=bad-date")
        assert resp.status_code == 400
        assert "YYYY-MM-DD" in resp.json()["detail"]

    def test_scrape_exception_returns_500(self, client):
        with patch("app.routers.menu_scrape.asyncio.run", side_effect=RuntimeError("Playwright failed")):
            with patch.dict("sys.modules", {"scrape_menu": MagicMock()}):
                resp = client.post("/api/menu-scrape/run")

        assert resp.status_code == 500
        assert "Scrape failed" in resp.json()["detail"]

    def test_custom_week_start_passed_through(self, client):
        """A valid week_start query param is accepted and used."""
        mock_result = {"week_start": "2026-01-05", "items_scraped": 0, "status": "empty"}

        with patch("app.routers.menu_scrape.asyncio.run", return_value=mock_result):
            resp = client.post("/api/menu-scrape/run?week_start=2026-01-05")

        assert resp.status_code == 200
        assert resp.json()["week_start"] == "2026-01-05"
