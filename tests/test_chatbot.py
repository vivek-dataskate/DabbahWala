"""
Tests for app/routers/chatbot.py
=================================

Covers:
  Endpoint tests (TestClient with mocked DB)
    - POST /api/chatbot/ask        — happy path, empty question, missing API key
    - GET  /api/chatbot/history    — empty result, with data
    - GET  /api/chatbot/suggest    — empty q, with q
    - POST /api/chatbot/reindex    — happy path

Run with:
    pytest tests/test_chatbot.py -v
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


def _mock_anthropic_response(text: str = "Here is your answer."):
    """Return a mock anthropic client whose messages.create returns `text`."""
    mock_content = MagicMock()
    mock_content.text = text
    mock_resp = MagicMock()
    mock_resp.content = [mock_content]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_resp
    return mock_client


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/chatbot/ask
# ─────────────────────────────────────────────────────────────────────────────

class TestAskChatbot:
    def test_ask_question(self, client):
        """POST a real question — Claude is called, answer returned — 200."""
        cur = _make_cursor(rows=[], fetchone_val=None)
        mock_anthropic = _mock_anthropic_response("We are open 10am–10pm daily.")

        with patch("app.routers.chatbot.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("app.routers.chatbot.ANTHROPIC_API_KEY", "test-key-123"), \
             patch("app.routers.chatbot.anthropic.Anthropic", return_value=mock_anthropic), \
             patch("app.routers.chatbot._save_interaction"):
            resp = client.post("/api/chatbot/ask", json={"question": "What hours are you open?"})

        assert resp.status_code == 200
        data = resp.json()
        assert "answer" in data
        assert data["question"] == "What hours are you open?"
        assert isinstance(data["sources"], list)

    def test_ask_empty_question(self, client):
        """POST with empty question — 400 (empty question rejected)."""
        with patch("app.routers.chatbot.ANTHROPIC_API_KEY", "test-key-123"):
            resp = client.post("/api/chatbot/ask", json={"question": ""})

        assert resp.status_code == 400

    def test_ask_no_api_key(self, client, monkeypatch):
        """No ANTHROPIC_API_KEY set — 500."""
        with patch("app.routers.chatbot.ANTHROPIC_API_KEY", ""):
            resp = client.post("/api/chatbot/ask", json={"question": "What is DabbahWala?"})

        assert resp.status_code == 500


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/chatbot/history
# ─────────────────────────────────────────────────────────────────────────────

class TestChatHistory:
    def test_history_empty(self, client):
        """GET /api/chatbot/history with no rows — 200 {interactions:[], count:0}."""
        cur = _make_cursor(rows=[])

        with patch("app.routers.chatbot.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/chatbot/history")

        assert resp.status_code == 200
        data = resp.json()
        assert data["interactions"] == []
        assert data["count"] == 0

    def test_history_with_data(self, client):
        """GET /api/chatbot/history returns rows — 200 with interactions list."""
        rows = [{"id": 1, "question": "Q?", "answer": "A.", "sources": [], "created_at": None}]
        cur = _make_cursor(rows=rows)

        with patch("app.routers.chatbot.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/chatbot/history")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["interactions"][0]["question"] == "Q?"


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/chatbot/suggest
# ─────────────────────────────────────────────────────────────────────────────

class TestSuggestQueries:
    def test_suggest_empty_q(self, client):
        """GET /api/chatbot/suggest?q= — empty query returns empty suggestions."""
        resp = client.get("/api/chatbot/suggest?q=")

        assert resp.status_code == 200
        data = resp.json()
        assert data["suggestions"] == []

    def test_suggest_with_q(self, client):
        """GET /api/chatbot/suggest?q=how — returns matching past questions."""
        rows = [{"question": "How much does delivery cost?"}]
        cur = _make_cursor(rows=rows)

        with patch("app.routers.chatbot.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/chatbot/suggest?q=how")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["suggestions"], list)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/chatbot/reindex
# ─────────────────────────────────────────────────────────────────────────────

class TestReindex:
    def test_reindex(self, client):
        """POST /api/chatbot/reindex — returns status ok with indexing results."""
        fake_docs = [{"source": "README.md", "content": "DabbahWala system docs."}]
        cur = _make_cursor(fetchone_val=None)

        with patch("app.routers.chatbot._load_md_files", return_value=fake_docs), \
             patch("app.routers.chatbot._do_index", return_value=3), \
             patch("app.routers.chatbot._save_last_indexed_at"), \
             patch("app.routers.chatbot._save_docs_hash"), \
             patch("app.routers.chatbot._get_stored_docs_hash", return_value=None), \
             patch("app.routers.chatbot._clear_canned"), \
             patch("app.routers.chatbot._start_precache_thread"), \
             patch("app.routers.chatbot.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/chatbot/reindex")

        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "total_chunks" in data
        assert isinstance(data["files_indexed"], list)
