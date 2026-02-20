"""
Pytest fixtures for DabbahWala tests.

Strategy
--------
The app talks to Postgres via app.db.get_cursor (a context-manager that
yields a psycopg2 DictCursor-like object).  We monkeypatch it globally so
no real DB connection is required.

The startup events (ensure_schema, chatbot sync) also call get_cursor /
touch the filesystem, so they are patched at the app-import level before
TestClient spins up.
"""

import os
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ─── DB cursor factory ────────────────────────────────────────────────────

def make_cursor_mock(rows=None, fetchone_result=None):
    """
    Return a mock that behaves like a psycopg2 DictCursor inside
    a `with get_cursor(commit=…) as cur:` block.

    :param rows: list of dicts returned by cur.fetchall()
    :param fetchone_result: dict returned by cur.fetchone()
    """
    cur = MagicMock()
    cur.fetchall.return_value = rows or []
    cur.fetchone.return_value = fetchone_result
    return cur


@contextmanager
def _mock_get_cursor(commit=False, cur=None):
    """A get_cursor replacement that yields *cur* (or a fresh mock)."""
    yield cur if cur is not None else MagicMock()


# ─── Environment defaults ─────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    """Ensure all tests have the required env vars without touching real values."""
    monkeypatch.setenv("SHIPDAY_API_KEY", "test_shipday_key_abc123")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setenv("ADMIN_SECRET", "test_admin_secret")


# ─── Shared DB mock ───────────────────────────────────────────────────────

@pytest.fixture
def mock_db_cursor():
    """
    Patches app.db.get_cursor globally.  Returns a factory so individual
    tests can customise rows / fetchone:

        def test_foo(mock_db_cursor):
            cur = mock_db_cursor(rows=[{"id": 1}])
            # The next get_cursor() call inside the code will yield `cur`
    """
    cursors = {}

    def _factory(rows=None, fetchone_result=None):
        c = make_cursor_mock(rows=rows, fetchone_result=fetchone_result)
        cursors["cur"] = c
        return c

    def _ctx(commit=False):
        return _mock_get_cursor(commit=commit, cur=cursors.get("cur"))

    with patch("app.db.get_cursor", side_effect=_ctx):
        yield _factory


# ─── FastAPI TestClient ───────────────────────────────────────────────────

@pytest.fixture
def client(mock_db_cursor):
    """
    TestClient with DB + startup events patched out.
    Depends on mock_db_cursor so the DB is already patched when the app
    processes any startup event.
    """
    # Patch startup events that need real resources
    with patch("app.routers.chatbot.sync_docs_on_startup"), \
         patch("app.db.get_cursor", side_effect=lambda commit=False: _mock_get_cursor(commit=commit)):
        from app.main import app
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c
