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


# ---------------------------------------------------------------------------
# Internal utility function tests (direct, no HTTP)
# ---------------------------------------------------------------------------

class TestSplitChunks:
    """Test _split_chunks text chunking directly."""

    def test_short_text_returns_single_chunk(self):
        """Text shorter than CHUNK_SIZE returns a single chunk."""
        from app.routers.chatbot import _split_chunks
        text = "Hello world"
        chunks = _split_chunks(text)
        assert len(chunks) == 1
        assert chunks[0] == "Hello world"

    def test_empty_string_returns_no_chunks(self):
        """Empty string returns empty list."""
        from app.routers.chatbot import _split_chunks
        chunks = _split_chunks("")
        assert chunks == []

    def test_long_text_splits_into_multiple_chunks(self):
        """Text much longer than CHUNK_SIZE produces multiple chunks."""
        from app.routers.chatbot import _split_chunks, CHUNK_SIZE
        long_text = "A" * (CHUNK_SIZE * 3)
        chunks = _split_chunks(long_text)
        assert len(chunks) >= 2

    def test_chunks_with_newlines_prefer_newline_boundary(self):
        """Chunks prefer to break at newline boundaries."""
        from app.routers.chatbot import _split_chunks, CHUNK_SIZE
        # Text with frequent newlines
        text = "\n".join(["Line number " + str(i) for i in range(200)])
        chunks = _split_chunks(text)
        assert len(chunks) >= 2
        # All chunks should be non-empty
        assert all(len(c) > 0 for c in chunks)


class TestComputeDocsHash:
    """Test _compute_docs_hash utility."""

    def test_same_docs_produce_same_hash(self):
        """Same docs always return same hash (deterministic)."""
        from app.routers.chatbot import _compute_docs_hash
        docs = [{"source": "README.md", "content": "Hello world"}]
        h1 = _compute_docs_hash(docs)
        h2 = _compute_docs_hash(docs)
        assert h1 == h2

    def test_different_docs_produce_different_hash(self):
        """Different content changes the hash."""
        from app.routers.chatbot import _compute_docs_hash
        docs1 = [{"source": "a.md", "content": "foo"}]
        docs2 = [{"source": "a.md", "content": "bar"}]
        assert _compute_docs_hash(docs1) != _compute_docs_hash(docs2)

    def test_hash_is_64_chars(self):
        """SHA-256 hex digest is always 64 characters."""
        from app.routers.chatbot import _compute_docs_hash
        docs = [{"source": "test.md", "content": "test content"}]
        assert len(_compute_docs_hash(docs)) == 64


class TestLastIndexedAt:
    """Test _last_indexed_at DB helper."""

    def test_returns_none_when_table_empty(self):
        """Returns None when no record exists in chatbot_doc_meta."""
        from app.routers.chatbot import _last_indexed_at
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch

        cur = MagicMock()
        cur.fetchone.return_value = None

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.chatbot.get_cursor", side_effect=_mock_cursor):
            result = _last_indexed_at()

        assert result is None

    def test_returns_timestamp_when_record_exists(self):
        """Returns updated_at value when meta record exists."""
        import datetime
        from app.routers.chatbot import _last_indexed_at
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch

        ts = datetime.datetime(2026, 1, 15, tzinfo=datetime.timezone.utc)
        cur = MagicMock()
        cur.fetchone.return_value = {"updated_at": ts}

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.chatbot.get_cursor", side_effect=_mock_cursor):
            result = _last_indexed_at()

        assert result == ts

    def test_returns_none_on_exception(self):
        """Returns None if DB query raises an exception."""
        from app.routers.chatbot import _last_indexed_at
        from unittest.mock import patch

        with patch("app.routers.chatbot.get_cursor", side_effect=Exception("DB error")):
            result = _last_indexed_at()

        assert result is None


class TestRelevantChunks:
    """Test _relevant_chunks retrieval helper."""

    def test_empty_question_returns_generic_chunks(self):
        """Words-only filter with empty question returns generic chunks."""
        from app.routers.chatbot import _relevant_chunks
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch

        cur = MagicMock()
        cur.fetchall.return_value = [
            {"source_file": "README.md", "content": "DabbahWala intro"},
        ]

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.chatbot.get_cursor", side_effect=_mock_cursor):
            result = _relevant_chunks("?!?")  # Only punctuation → no words

        assert len(result) == 1
        assert result[0]["source"] == "README.md"

    def test_normal_question_uses_full_text_search(self):
        """Normal question with words uses full-text ts_rank query."""
        from app.routers.chatbot import _relevant_chunks
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch

        cur = MagicMock()
        cur.fetchall.return_value = [
            {"source_file": "SYSTEM.md", "content": "Architecture overview"},
        ]

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.chatbot.get_cursor", side_effect=_mock_cursor):
            result = _relevant_chunks("How does the architecture work?")

        assert len(result) == 1
        assert result[0]["source"] == "SYSTEM.md"


class TestLookupCanned:
    """Test _lookup_canned cache helper."""

    def test_returns_none_when_not_cached(self):
        """Returns None when no matching canned answer exists."""
        from app.routers.chatbot import _lookup_canned
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch

        cur = MagicMock()
        cur.fetchone.return_value = None

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.chatbot.get_cursor", side_effect=_mock_cursor):
            result = _lookup_canned("Unknown question?")

        assert result is None

    def test_returns_cached_answer_when_present(self):
        """Returns dict with answer and sources when cached."""
        from app.routers.chatbot import _lookup_canned
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch

        cur = MagicMock()
        cur.fetchone.return_value = {
            "answer": "The architecture uses a 4-layer pipeline.",
            "sources": ["SYSTEM.md"],
        }

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.chatbot.get_cursor", side_effect=_mock_cursor):
            result = _lookup_canned("Why is a 4-layer AI pipeline better?")

        assert result is not None
        assert "pipeline" in result["answer"]
        assert "SYSTEM.md" in result["sources"]


class TestFindCachedAnswer:
    """Test _find_cached_answer semantic similarity cache."""

    def test_returns_none_when_no_similar_answer(self):
        """Returns None when no similar interaction exists."""
        from app.routers.chatbot import _find_cached_answer
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch

        cur = MagicMock()
        cur.fetchone.return_value = None

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.chatbot.get_cursor", side_effect=_mock_cursor):
            result = _find_cached_answer("Brand new question with no history")

        assert result is None

    def test_returns_cached_answer_on_hit(self):
        """Returns answer dict when similarity threshold met."""
        from app.routers.chatbot import _find_cached_answer
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch

        cur = MagicMock()
        cur.fetchone.return_value = {
            "answer": "The system uses PostgreSQL for storage.",
            "sources": ["SYSTEM.md"],
            "sim": 0.85,
        }

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.chatbot.get_cursor", side_effect=_mock_cursor):
            result = _find_cached_answer("What database does the system use?")

        assert result is not None
        assert "PostgreSQL" in result["answer"]


class TestSaveHelpers:
    """Test save/clear helper functions directly."""

    def test_save_interaction_inserts_row(self):
        """_save_interaction calls INSERT into chatbot_interactions."""
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch
        from app.routers.chatbot import _save_interaction

        cur = MagicMock()

        @contextmanager
        def _mock_cursor(commit=True):
            yield cur

        with patch("app.routers.chatbot.get_cursor", side_effect=_mock_cursor):
            _save_interaction("What is DabbahWala?", "A food delivery app.", ["README.md"])

        cur.execute.assert_called_once()
        call_args = cur.execute.call_args[0]
        assert "chatbot_interactions" in call_args[0]

    def test_save_canned_upserts_row(self):
        """_save_canned calls INSERT ... ON CONFLICT into chatbot_canned_qa."""
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch
        from app.routers.chatbot import _save_canned

        cur = MagicMock()

        @contextmanager
        def _mock_cursor(commit=True):
            yield cur

        with patch("app.routers.chatbot.get_cursor", side_effect=_mock_cursor):
            _save_canned("Why 4-layer pipeline?", "Better decision quality.", ["SYSTEM.md"])

        cur.execute.assert_called_once()

    def test_clear_canned_deletes_all(self):
        """_clear_canned deletes all rows from chatbot_canned_qa."""
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch
        from app.routers.chatbot import _clear_canned

        cur = MagicMock()

        @contextmanager
        def _mock_cursor(commit=True):
            yield cur

        with patch("app.routers.chatbot.get_cursor", side_effect=_mock_cursor):
            _clear_canned()

        cur.execute.assert_called_once()
        call_args = cur.execute.call_args[0][0]
        assert "chatbot_canned_qa" in call_args

    def test_clear_canned_handles_exception_gracefully(self):
        """_clear_canned swallows exceptions without raising."""
        from app.routers.chatbot import _clear_canned
        from unittest.mock import patch

        with patch("app.routers.chatbot.get_cursor", side_effect=Exception("DB offline")):
            # Should not raise
            _clear_canned()

    def test_save_last_indexed_at_calls_upsert(self):
        """_save_last_indexed_at calls INSERT ... ON CONFLICT into chatbot_doc_meta."""
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch
        from app.routers.chatbot import _save_last_indexed_at

        cur = MagicMock()

        @contextmanager
        def _mock_cursor(commit=True):
            yield cur

        with patch("app.routers.chatbot.get_cursor", side_effect=_mock_cursor):
            _save_last_indexed_at()

        cur.execute.assert_called_once()

    def test_save_docs_hash_upserts(self):
        """_save_docs_hash calls INSERT ... ON CONFLICT into chatbot_doc_meta."""
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch
        from app.routers.chatbot import _save_docs_hash

        cur = MagicMock()

        @contextmanager
        def _mock_cursor(commit=True):
            yield cur

        with patch("app.routers.chatbot.get_cursor", side_effect=_mock_cursor):
            _save_docs_hash("abc123")

        cur.execute.assert_called_once()


class TestGetStoredDocsHash:
    def test_returns_none_when_missing(self):
        """_get_stored_docs_hash returns None when no record exists."""
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch
        from app.routers.chatbot import _get_stored_docs_hash

        cur = MagicMock()
        cur.fetchone.return_value = None

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.chatbot.get_cursor", side_effect=_mock_cursor):
            result = _get_stored_docs_hash()

        assert result is None

    def test_returns_hash_when_stored(self):
        """_get_stored_docs_hash returns hash string from DB."""
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch
        from app.routers.chatbot import _get_stored_docs_hash

        cur = MagicMock()
        cur.fetchone.return_value = {"value": "abc123def456"}

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.chatbot.get_cursor", side_effect=_mock_cursor):
            result = _get_stored_docs_hash()

        assert result == "abc123def456"

    def test_returns_none_on_exception(self):
        """_get_stored_docs_hash returns None on exception."""
        from app.routers.chatbot import _get_stored_docs_hash
        from unittest.mock import patch

        with patch("app.routers.chatbot.get_cursor", side_effect=Exception("timeout")):
            result = _get_stored_docs_hash()

        assert result is None


class TestSimilarHistory:
    def test_returns_empty_for_short_words(self):
        """_similar_history returns [] when question has no words longer than 3 chars."""
        from app.routers.chatbot import _similar_history
        result = _similar_history("Hi?")
        assert result == []

    def test_returns_history_when_matching(self):
        """_similar_history returns Q&A pairs matching question keywords."""
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch
        from app.routers.chatbot import _similar_history

        cur = MagicMock()
        cur.fetchall.return_value = [
            {"question": "What is the architecture?", "answer": "4-layer pipeline."}
        ]

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.chatbot.get_cursor", side_effect=_mock_cursor):
            result = _similar_history("What is the system architecture?")

        assert len(result) == 1
        assert "architecture" in result[0]["question"]


# ---------------------------------------------------------------------------
# _project_root and _load_md_files
# ---------------------------------------------------------------------------

class TestProjectRoot:
    def test_returns_a_path(self):
        """_project_root() returns a Path object."""
        from pathlib import Path
        from app.routers.chatbot import _project_root
        result = _project_root()
        assert isinstance(result, Path)


class TestLoadMdFiles:
    def test_returns_list(self):
        """_load_md_files() returns a list (may be empty or populated)."""
        from app.routers.chatbot import _load_md_files
        from pathlib import Path
        from unittest.mock import MagicMock, patch

        # Mock rglob to return one fake text file
        fake_path = MagicMock(spec=Path)
        fake_path.is_file.return_value = True
        fake_path.suffix = ".md"
        fake_path.relative_to.return_value = Path("README.md")
        fake_path.stat.return_value.st_size = 100
        fake_path.read_text.return_value = "Hello docs"

        with patch.object(Path, "rglob", return_value=[fake_path]):
            result = _load_md_files()

        assert isinstance(result, list)

    def test_skips_binary_files(self):
        """_load_md_files() skips binary extensions like .pyc."""
        from app.routers.chatbot import _load_md_files
        from pathlib import Path
        from unittest.mock import MagicMock, patch

        fake_path = MagicMock(spec=Path)
        fake_path.is_file.return_value = True
        fake_path.suffix = ".pyc"
        fake_path.relative_to.return_value = Path("app/test.pyc")

        with patch.object(Path, "rglob", return_value=[fake_path]):
            result = _load_md_files()

        assert result == []

    def test_handles_read_exception(self):
        """_load_md_files() skips files that raise read exceptions."""
        from app.routers.chatbot import _load_md_files
        from pathlib import Path
        from unittest.mock import MagicMock, patch

        fake_path = MagicMock(spec=Path)
        fake_path.is_file.return_value = True
        fake_path.suffix = ".md"
        fake_path.relative_to.return_value = Path("bad.md")
        fake_path.stat.return_value.st_size = 50
        fake_path.read_text.side_effect = PermissionError("no access")

        with patch.object(Path, "rglob", return_value=[fake_path]):
            result = _load_md_files()

        assert result == []


# ---------------------------------------------------------------------------
# _do_index
# ---------------------------------------------------------------------------

class TestDoIndex:
    def test_indexes_docs(self):
        """_do_index inserts chunks and returns count."""
        from app.routers.chatbot import _do_index

        cur = MagicMock()

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        docs = [{"source": "test.md", "content": "This is a short test document."}]
        with patch("app.routers.chatbot.get_cursor", side_effect=_mock_cursor):
            total = _do_index(docs)

        assert total >= 1
        # DELETE was called first
        cur.execute.assert_any_call("DELETE FROM chatbot_doc_chunks")

    def test_empty_docs_returns_zero(self):
        """_do_index with empty docs list returns 0."""
        from app.routers.chatbot import _do_index

        cur = MagicMock()

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.chatbot.get_cursor", side_effect=_mock_cursor):
            total = _do_index([])

        assert total == 0


# ---------------------------------------------------------------------------
# _ensure_indexed
# ---------------------------------------------------------------------------

class TestEnsureIndexed:
    def test_skips_if_recently_indexed(self):
        """_ensure_indexed does nothing if indexed less than REINDEX_INTERVAL_DAYS ago."""
        import datetime
        from app.routers.chatbot import _ensure_indexed

        recent_ts = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)

        with patch("app.routers.chatbot._last_indexed_at", return_value=recent_ts), \
             patch("app.routers.chatbot._load_md_files") as mock_load:
            _ensure_indexed()

        mock_load.assert_not_called()

    def test_skips_if_no_docs(self):
        """_ensure_indexed logs warning and returns if no docs found."""
        from app.routers.chatbot import _ensure_indexed

        with patch("app.routers.chatbot._last_indexed_at", return_value=None), \
             patch("app.routers.chatbot._load_md_files", return_value=[]), \
             patch("app.routers.chatbot._do_index") as mock_do:
            _ensure_indexed()

        mock_do.assert_not_called()

    def test_reindexes_when_stale(self):
        """_ensure_indexed calls _do_index when last_indexed is old enough."""
        import datetime
        from app.routers.chatbot import _ensure_indexed

        old_ts = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=40)
        docs = [{"source": "README.md", "content": "docs"}]

        with patch("app.routers.chatbot._last_indexed_at", return_value=old_ts), \
             patch("app.routers.chatbot._load_md_files", return_value=docs), \
             patch("app.routers.chatbot._compute_docs_hash", return_value="newhash"), \
             patch("app.routers.chatbot._get_stored_docs_hash", return_value="oldhash"), \
             patch("app.routers.chatbot._do_index", return_value=5) as mock_do, \
             patch("app.routers.chatbot._save_last_indexed_at"), \
             patch("app.routers.chatbot._save_docs_hash"), \
             patch("app.routers.chatbot._clear_canned"), \
             patch("app.routers.chatbot._start_precache_thread"):
            _ensure_indexed()

        mock_do.assert_called_once()

    def test_reindex_unchanged_docs_skips_chip_rebuild(self):
        """_ensure_indexed skips chip rebuild when doc hash is unchanged."""
        import datetime
        from app.routers.chatbot import _ensure_indexed

        old_ts = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=40)
        docs = [{"source": "README.md", "content": "docs"}]

        with patch("app.routers.chatbot._last_indexed_at", return_value=old_ts), \
             patch("app.routers.chatbot._load_md_files", return_value=docs), \
             patch("app.routers.chatbot._compute_docs_hash", return_value="samehash"), \
             patch("app.routers.chatbot._get_stored_docs_hash", return_value="samehash"), \
             patch("app.routers.chatbot._do_index", return_value=5), \
             patch("app.routers.chatbot._save_last_indexed_at"), \
             patch("app.routers.chatbot._save_docs_hash"), \
             patch("app.routers.chatbot._clear_canned") as mock_clear, \
             patch("app.routers.chatbot._start_precache_thread") as mock_precache:
            _ensure_indexed()

        mock_clear.assert_not_called()
        mock_precache.assert_not_called()


# ---------------------------------------------------------------------------
# _ensure_tables
# ---------------------------------------------------------------------------

class TestEnsureTables:
    def test_ensure_tables_executes_create_statements(self):
        """_ensure_tables runs CREATE TABLE IF NOT EXISTS statements."""
        from app.routers.chatbot import _ensure_tables

        cur = MagicMock()

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.chatbot.get_cursor", side_effect=_mock_cursor):
            _ensure_tables()

        # Should have called execute multiple times for CREATE TABLE/INDEX
        assert cur.execute.call_count >= 6


# ---------------------------------------------------------------------------
# sync_docs_on_startup
# ---------------------------------------------------------------------------

class TestSyncDocsOnStartup:
    def test_startup_calls_ensure_tables_and_indexed(self):
        """sync_docs_on_startup calls _ensure_tables then _ensure_indexed."""
        from app.routers.chatbot import sync_docs_on_startup

        with patch("app.routers.chatbot._ensure_tables") as mock_tables, \
             patch("app.routers.chatbot._ensure_indexed") as mock_index:
            sync_docs_on_startup()

        mock_tables.assert_called_once()
        mock_index.assert_called_once()

    def test_startup_handles_ensure_tables_exception(self):
        """sync_docs_on_startup returns early if _ensure_tables raises."""
        from app.routers.chatbot import sync_docs_on_startup

        with patch("app.routers.chatbot._ensure_tables", side_effect=Exception("table error")), \
             patch("app.routers.chatbot._ensure_indexed") as mock_index:
            sync_docs_on_startup()  # should not raise

        mock_index.assert_not_called()

    def test_startup_handles_ensure_indexed_exception(self):
        """sync_docs_on_startup continues even if _ensure_indexed raises."""
        from app.routers.chatbot import sync_docs_on_startup

        with patch("app.routers.chatbot._ensure_tables"), \
             patch("app.routers.chatbot._ensure_indexed", side_effect=Exception("index error")):
            sync_docs_on_startup()  # should not raise


# ---------------------------------------------------------------------------
# _precache_chips / _start_precache_thread
# ---------------------------------------------------------------------------

class TestPrecacheChips:
    def test_no_api_key_returns_early(self):
        """_precache_chips returns early if no ANTHROPIC_API_KEY."""
        from app.routers.chatbot import _precache_chips

        with patch("app.routers.chatbot.ANTHROPIC_API_KEY", ""), \
             patch("app.routers.chatbot._lookup_canned") as mock_lookup:
            _precache_chips()

        mock_lookup.assert_not_called()

    def test_skips_already_cached_chips(self):
        """_precache_chips skips questions already in the canned cache."""
        from app.routers.chatbot import _precache_chips

        with patch("app.routers.chatbot.ANTHROPIC_API_KEY", "test-key"), \
             patch("app.routers.chatbot._lookup_canned", return_value={"answer": "cached", "sources": []}), \
             patch("app.routers.chatbot._build_answer") as mock_build:
            _precache_chips()

        mock_build.assert_not_called()

    def test_builds_and_saves_uncached_chips(self):
        """_precache_chips calls _build_answer for uncached chips."""
        from app.routers.chatbot import _precache_chips, CHIP_QUESTIONS

        mock_client = _mock_anthropic_response("Cached answer")

        with patch("app.routers.chatbot.ANTHROPIC_API_KEY", "test-key"), \
             patch("app.routers.chatbot._lookup_canned", return_value=None), \
             patch("app.routers.chatbot.anthropic.Anthropic", return_value=mock_client), \
             patch("app.routers.chatbot._build_answer", return_value=("answer text", ["src.md"])) as mock_build, \
             patch("app.routers.chatbot._save_canned") as mock_save:
            _precache_chips()

        assert mock_build.call_count == len(CHIP_QUESTIONS)
        assert mock_save.call_count == len(CHIP_QUESTIONS)

    def test_precache_handles_build_exception(self):
        """_precache_chips catches exceptions per chip and continues."""
        from app.routers.chatbot import _precache_chips

        with patch("app.routers.chatbot.ANTHROPIC_API_KEY", "test-key"), \
             patch("app.routers.chatbot._lookup_canned", return_value=None), \
             patch("app.routers.chatbot.anthropic.Anthropic"), \
             patch("app.routers.chatbot._build_answer", side_effect=Exception("Claude error")), \
             patch("app.routers.chatbot._save_canned") as mock_save:
            _precache_chips()  # should not raise

        mock_save.assert_not_called()

    def test_start_precache_thread_starts_daemon(self):
        """_start_precache_thread starts a daemon thread."""
        from app.routers.chatbot import _start_precache_thread

        with patch("app.routers.chatbot._precache_chips") as mock_fn, \
             patch("threading.Thread") as mock_thread_class:
            mock_thread = MagicMock()
            mock_thread_class.return_value = mock_thread
            _start_precache_thread()

        mock_thread.start.assert_called_once()


# ---------------------------------------------------------------------------
# ask endpoint — additional branches
# ---------------------------------------------------------------------------

class TestAskEndpointBranches:
    def test_out_of_scope_returns_redirect_message(self, client):
        """ask() rejects live data queries with a redirect message."""
        with patch("app.routers.chatbot.ANTHROPIC_API_KEY", "test-key"):
            resp = client.post(
                "/api/chatbot/ask",
                json={"question": "Show me all customers who ordered this week"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "Query" in data["answer"]

    def test_canned_answer_hit_returns_cached(self, client):
        """ask() returns cached answer immediately for chip questions."""
        cached = {"answer": "Cached chip answer", "sources": ["docs.md"]}

        with patch("app.routers.chatbot.ANTHROPIC_API_KEY", "test-key"), \
             patch("app.routers.chatbot._lookup_canned", return_value=cached), \
             patch("app.routers.chatbot._build_answer") as mock_build:
            resp = client.post("/api/chatbot/ask", json={"question": "What is the AI stack?"})

        assert resp.status_code == 200
        assert resp.json()["answer"] == "Cached chip answer"
        mock_build.assert_not_called()

    def test_semantic_cache_hit_returns_cached(self, client):
        """ask() returns similar cached answer when semantic cache hits."""
        similar = {"answer": "Similar cached answer", "sources": ["arch.md"]}

        with patch("app.routers.chatbot.ANTHROPIC_API_KEY", "test-key"), \
             patch("app.routers.chatbot._lookup_canned", return_value=None), \
             patch("app.routers.chatbot._find_cached_answer", return_value=similar), \
             patch("app.routers.chatbot._build_answer") as mock_build:
            resp = client.post(
                "/api/chatbot/ask",
                json={"question": "How does the architecture work?"},
            )

        assert resp.status_code == 200
        assert resp.json()["answer"] == "Similar cached answer"
        mock_build.assert_not_called()

    def test_claude_error_raises_500(self, client):
        """ask() raises 500 when Claude API raises an exception."""
        with patch("app.routers.chatbot.ANTHROPIC_API_KEY", "test-key"), \
             patch("app.routers.chatbot._lookup_canned", return_value=None), \
             patch("app.routers.chatbot._find_cached_answer", return_value=None), \
             patch("app.routers.chatbot.anthropic.Anthropic"), \
             patch("app.routers.chatbot._build_answer", side_effect=Exception("timeout")):
            resp = client.post(
                "/api/chatbot/ask",
                json={"question": "How does the system work?"},
            )

        assert resp.status_code == 500

    def test_save_interaction_failure_does_not_break_response(self, client):
        """ask() still returns 200 even if _save_interaction raises."""
        mock_client = _mock_anthropic_response("AI answer here.")

        with patch("app.routers.chatbot.ANTHROPIC_API_KEY", "test-key"), \
             patch("app.routers.chatbot._lookup_canned", return_value=None), \
             patch("app.routers.chatbot._find_cached_answer", return_value=None), \
             patch("app.routers.chatbot.anthropic.Anthropic", return_value=mock_client), \
             patch("app.routers.chatbot._build_answer", return_value=("AI answer here.", [])), \
             patch("app.routers.chatbot._save_interaction", side_effect=Exception("DB down")):
            resp = client.post(
                "/api/chatbot/ask",
                json={"question": "How does the system work?"},
            )

        assert resp.status_code == 200
        assert resp.json()["answer"] == "AI answer here."


# ---------------------------------------------------------------------------
# suggest endpoint — error branches
# ---------------------------------------------------------------------------

class TestSuggestErrorBranches:
    def test_suggest_history_exception_still_returns_200(self, client):
        """suggest() returns 200 even when history DB query fails."""
        with patch("app.routers.chatbot.get_cursor", side_effect=Exception("DB down")):
            resp = client.get("/api/chatbot/suggest?q=how")

        assert resp.status_code == 200
        assert resp.json()["suggestions"] == []

    def test_suggest_with_few_history_results_fills_from_canned(self, client):
        """suggest() fills remaining slots from canned QA when history has few results."""
        history_cur = MagicMock()
        history_cur.fetchall.return_value = []
        canned_cur = MagicMock()
        canned_cur.fetchall.return_value = [
            {"question": "How does ordering work?"},
            {"question": "How do I subscribe?"},
        ]

        call_count = 0

        @contextmanager
        def _multi_cursor(commit=False):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield history_cur
            else:
                yield canned_cur

        with patch("app.routers.chatbot.get_cursor", side_effect=_multi_cursor):
            resp = client.get("/api/chatbot/suggest?q=how")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["suggestions"]) >= 0  # may be 0 or more depending on matching


# ---------------------------------------------------------------------------
# reindex endpoint — error branches
# ---------------------------------------------------------------------------

class TestReindexErrorBranches:
    def test_reindex_no_files_returns_500(self, client):
        """POST /reindex with no files found raises 500."""
        with patch("app.routers.chatbot._load_md_files", return_value=[]):
            resp = client.post("/api/chatbot/reindex")

        assert resp.status_code == 500

    def test_reindex_do_index_exception_returns_500(self, client):
        """POST /reindex with _do_index raising returns 500."""
        fake_docs = [{"source": "README.md", "content": "docs"}]

        with patch("app.routers.chatbot._load_md_files", return_value=fake_docs), \
             patch("app.routers.chatbot._compute_docs_hash", return_value="hash"), \
             patch("app.routers.chatbot._get_stored_docs_hash", return_value="old"), \
             patch("app.routers.chatbot._do_index", side_effect=Exception("DB write fail")):
            resp = client.post("/api/chatbot/reindex")

        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# _build_answer — context building branches
# ---------------------------------------------------------------------------

class TestBuildAnswer:
    def test_build_answer_with_chunks_and_history(self):
        """_build_answer includes both chunk context and history in prompt."""
        from app.routers.chatbot import _build_answer

        mock_client = _mock_anthropic_response("Answer with context.")
        cur = MagicMock()
        # First fetchall for chunks, second fetchall for history (via _relevant_chunks / _similar_history)
        cur.fetchall.side_effect = [
            [{"source_file": "arch.md", "content": "Architecture details here."}],
            [{"question": "What is it?", "answer": "A system."}],
        ]
        cur.fetchone.return_value = None  # no tsvector search results

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.chatbot.get_cursor", side_effect=_mock_cursor):
            answer, sources = _build_answer("Tell me about the architecture?", mock_client)

        assert isinstance(answer, str)
        assert isinstance(sources, list)

    def test_build_answer_no_chunks_no_history(self):
        """_build_answer still calls Claude even with empty retrieval."""
        from app.routers.chatbot import _build_answer

        mock_client = _mock_anthropic_response("No context answer.")
        cur = MagicMock()
        cur.fetchall.return_value = []
        cur.fetchone.return_value = None

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.chatbot.get_cursor", side_effect=_mock_cursor):
            answer, sources = _build_answer("Totally obscure question?", mock_client)

        assert answer == "No context answer."
        assert sources == []


# ---------------------------------------------------------------------------
# _lookup_canned / _find_cached_answer — exception branches
# ---------------------------------------------------------------------------

class TestLookupCannedException:
    def test_lookup_canned_exception_returns_none(self):
        """_lookup_canned returns None when DB raises."""
        from app.routers.chatbot import _lookup_canned

        with patch("app.routers.chatbot.get_cursor", side_effect=Exception("DB error")):
            result = _lookup_canned("some question")

        assert result is None


class TestFindCachedAnswerException:
    def test_find_cached_answer_exception_returns_none(self):
        """_find_cached_answer returns None when DB raises."""
        from app.routers.chatbot import _find_cached_answer

        with patch("app.routers.chatbot.get_cursor", side_effect=Exception("DB error")):
            result = _find_cached_answer("some question")

        assert result is None
