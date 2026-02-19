"""
AI Chatbot — RAG-powered Q&A over system documentation.

Indexes markdown files (README.md, ARCHITECTURE.md, IMPLEMENTATION_PLAN.md, data/sql/README.md)
into PostgreSQL full-text search chunks. Each user question retrieves relevant chunks plus
similar previous interactions, builds a context-rich prompt for Claude, and saves the
Q&A pair to chatbot_interactions for future retrieval.
"""
import datetime
import logging
import os
import re
from pathlib import Path

import anthropic
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_cursor

router = APIRouter()
logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-5-20250929"

CHUNK_SIZE = 900       # characters per chunk
CHUNK_OVERLAP = 120    # overlap between consecutive chunks
REINDEX_INTERVAL_DAYS = 30  # reindex at most once per month


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    question: str


class ChatResponse(BaseModel):
    question: str
    answer: str
    sources: list[str]


# ---------------------------------------------------------------------------
# Doc indexing helpers
# ---------------------------------------------------------------------------

def _project_root() -> Path:
    """Return project root (two levels above this file: app/routers/ → root)."""
    return Path(__file__).parent.parent.parent


def _load_md_files() -> list[dict]:
    """Discover and load all .md files (project-wide) and all migrations/*.sql files."""
    base = _project_root()
    docs = []

    # All markdown files recursively (skip hidden dirs and node_modules)
    for path in sorted(base.rglob("*.md")):
        parts = path.parts
        if any(p.startswith(".") or p == "node_modules" for p in parts):
            continue
        rel = str(path.relative_to(base))
        try:
            content = path.read_text(encoding="utf-8")
            docs.append({"source": rel, "content": content})
            logger.info("Loaded doc %s (%d chars)", rel, len(content))
        except Exception as exc:
            logger.warning("Cannot read %s: %s", rel, exc)

    # All SQL migration files
    migrations_dir = base / "migrations"
    if migrations_dir.is_dir():
        for path in sorted(migrations_dir.glob("*.sql")):
            rel = str(path.relative_to(base))
            try:
                content = path.read_text(encoding="utf-8")
                docs.append({"source": rel, "content": content})
                logger.info("Loaded doc %s (%d chars)", rel, len(content))
            except Exception as exc:
                logger.warning("Cannot read %s: %s", rel, exc)

    return docs


def _split_chunks(text: str) -> list[str]:
    """Split text into overlapping chunks, preferring newline boundaries."""
    chunks: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + CHUNK_SIZE, length)
        if end < length:
            # Try to break at a newline within the last 20% of the window
            search_from = max(start + int(CHUNK_SIZE * 0.8), start + 1)
            nl = text.rfind("\n", search_from, end)
            if nl > search_from:
                end = nl + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        next_start = end - CHUNK_OVERLAP
        if next_start <= start:
            next_start = start + 1
        start = next_start
    return chunks


def _last_indexed_at():
    """Return the datetime of the last successful index, or None."""
    try:
        with get_cursor(commit=False) as cur:
            cur.execute(
                "SELECT updated_at FROM chatbot_doc_meta WHERE key = 'last_indexed_at'",
            )
            row = cur.fetchone()
            return row["updated_at"] if row else None
    except Exception:
        return None


def _save_last_indexed_at() -> None:
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO chatbot_doc_meta (key, value, updated_at)
            VALUES ('last_indexed_at', 'ok', NOW())
            ON CONFLICT (key) DO UPDATE
              SET value = 'ok', updated_at = NOW()
            """,
        )


def _do_index(docs: list[dict]) -> int:
    """Replace all chunks and return total chunks written."""
    total = 0
    with get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM chatbot_doc_chunks")
        for doc in docs:
            chunks = _split_chunks(doc["content"])
            for idx, chunk in enumerate(chunks):
                cur.execute(
                    """
                    INSERT INTO chatbot_doc_chunks (source_file, chunk_index, content)
                    VALUES (%s, %s, %s)
                    """,
                    (doc["source"], idx, chunk),
                )
            total += len(chunks)
            logger.info("Indexed %d chunks from %s", len(chunks), doc["source"])
    return total


def _ensure_indexed() -> None:
    """Index all docs; reindex at most once per month."""
    last_at = _last_indexed_at()
    if last_at is not None:
        age_days = (datetime.datetime.now(datetime.timezone.utc) - last_at).days
        if age_days < REINDEX_INTERVAL_DAYS:
            logger.debug("Chatbot docs indexed %d days ago — skipping reindex", age_days)
            return

    docs = _load_md_files()
    if not docs:
        logger.warning("No docs to index")
        return

    logger.info("Reindexing chatbot docs (%d files)", len(docs))
    total = _do_index(docs)
    _save_last_indexed_at()
    logger.info("Reindex complete: %d total chunks from %d files", total, len(docs))


def _ensure_tables() -> None:
    """Create chatbot tables if they don't exist (guards against skipped migrations)."""
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS chatbot_doc_chunks (
                id           SERIAL PRIMARY KEY,
                source_file  TEXT    NOT NULL,
                chunk_index  INTEGER NOT NULL,
                content      TEXT    NOT NULL,
                content_tsv  TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
                created_at   TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_doc_chunks_tsv ON chatbot_doc_chunks USING GIN (content_tsv)"
        )
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_doc_chunks_source_chunk ON chatbot_doc_chunks (source_file, chunk_index)"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS chatbot_doc_meta (
                key        TEXT PRIMARY KEY,
                value      TEXT        NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS chatbot_interactions (
                id         SERIAL PRIMARY KEY,
                question   TEXT        NOT NULL,
                answer     TEXT        NOT NULL,
                sources    TEXT[]      DEFAULT '{}',
                model      TEXT        DEFAULT 'claude-sonnet-4-5-20250929',
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_chatbot_interactions_created ON chatbot_interactions (created_at DESC)"
        )


def sync_docs_on_startup() -> None:
    """Called from FastAPI startup event to auto-reindex if docs have changed."""
    try:
        _ensure_tables()
    except Exception as exc:
        logger.error("Failed to ensure chatbot tables: %s", exc)
        return
    try:
        _ensure_indexed()
    except Exception as exc:
        logger.error("Startup doc sync failed: %s", exc)


# ---------------------------------------------------------------------------
# Retrieval helpers
# ---------------------------------------------------------------------------

def _relevant_chunks(question: str, limit: int = 8) -> list[dict]:
    """Return doc chunks most relevant to the question via full-text search."""
    words = [w for w in re.sub(r"[^\w\s]", " ", question).split() if len(w) > 2]

    if not words:
        with get_cursor(commit=False) as cur:
            cur.execute(
                "SELECT source_file, content FROM chatbot_doc_chunks ORDER BY id LIMIT %s",
                (limit,),
            )
            return [{"source": r["source_file"], "content": r["content"]} for r in cur.fetchall()]

    tsquery = " | ".join(words[:12])
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT source_file, content,
                   ts_rank(content_tsv, to_tsquery('english', %s)) AS rank
            FROM chatbot_doc_chunks
            WHERE content_tsv @@ to_tsquery('english', %s)
            ORDER BY rank DESC
            LIMIT %s
            """,
            (tsquery, tsquery, limit),
        )
        rows = cur.fetchall()

    if rows:
        return [{"source": r["source_file"], "content": r["content"]} for r in rows]

    # Fallback: ILIKE keyword search
    top_words = words[:5]
    conditions = " OR ".join(["content ILIKE %s"] * len(top_words))
    params: list = [f"%{w}%" for w in top_words] + [limit]
    with get_cursor(commit=False) as cur:
        cur.execute(
            f"SELECT DISTINCT source_file, content FROM chatbot_doc_chunks WHERE {conditions} LIMIT %s",
            params,
        )
        return [{"source": r["source_file"], "content": r["content"]} for r in cur.fetchall()]


def _similar_history(question: str, limit: int = 3) -> list[dict]:
    """Return recent Q&A pairs whose question shares keywords with the current one."""
    words = [w for w in re.sub(r"[^\w\s]", " ", question).split() if len(w) > 3][:5]
    if not words:
        return []
    conditions = " OR ".join(["question ILIKE %s"] * len(words))
    params: list = [f"%{w}%" for w in words] + [limit]
    with get_cursor(commit=False) as cur:
        cur.execute(
            f"""
            SELECT question, answer FROM chatbot_interactions
            WHERE {conditions}
            ORDER BY created_at DESC
            LIMIT %s
            """,
            params,
        )
        return [{"question": r["question"], "answer": r["answer"]} for r in cur.fetchall()]


def _save_interaction(question: str, answer: str, sources: list[str]) -> None:
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO chatbot_interactions (question, answer, sources, model)
            VALUES (%s, %s, %s, %s)
            """,
            (question, answer, sources, CLAUDE_MODEL),
        )


# Patterns that signal a request for *specific live data* — not system/process questions.
# Business process questions ("how do we handle lapsed customers?") are IN scope.
# Specific data lookups ("show me orders for John", "how many sales today") are OUT of scope.
_OUT_OF_SCOPE_PATTERNS = re.compile(
    r"("
    r"show me .{0,40}(order|customer|contact|invoice|sale|revenue|stat)|"
    r"find me .{0,40}(customer|contact|order)|"
    r"look\s?up .{0,40}(customer|contact|order)|"
    r"list (all |the )?(customer|contact|order|sale)|"
    r"(how many|total number of) (order|sale|customer|contact|message|sms|email)s?\b(?! are |s? (handled|processed|trigger|sent by|defined|exist in the))|"
    r"what did .{0,30} (order|buy|purchase)|"
    r"(revenue|sales|gmv|aov).{0,30}(today|yesterday|this week|last week|last month)|"
    r"(email address|phone number) (of|for) .{1,40}|"
    r"pull (up |the )?(record|data|profile|history) (of|for)|"
    r"give me (the |a )?(data|record|profile|history|report) (of|for)"
    r")",
    re.IGNORECASE,
)


def _is_out_of_scope(question: str) -> bool:
    """Return True only for specific live-data lookups, not business/process questions."""
    return bool(_OUT_OF_SCOPE_PATTERNS.search(question))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/ask", response_model=ChatResponse)
async def ask(req: ChatRequest):
    """
    Answer a question about the DabbahWala system.

    1. Ensures markdown docs are indexed into chatbot_doc_chunks.
    2. Retrieves relevant chunks via PostgreSQL full-text search.
    3. Retrieves similar past Q&A pairs from chatbot_interactions.
    4. Builds a context-rich prompt and calls Claude.
    5. Saves the new Q&A pair for future retrieval.
    """
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty")
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    # Guard: reject customer/order data questions before touching Claude
    _out_of_scope = _is_out_of_scope(question)
    if _out_of_scope:
        return ChatResponse(
            question=question,
            answer=(
                "That looks like a request for **live data** (specific records, counts, or "
                "real-time stats). This assistant covers business strategy, marketing processes, "
                "system design, and how things work — but not live database queries.\n\n"
                "For customer profiles, order history, campaign stats, or contact data, "
                "please use the **Query** tab instead."
            ),
            sources=[],
        )

    # Ensure knowledge base is populated
    try:
        _ensure_indexed()
    except Exception as exc:
        logger.warning("Doc indexing failed: %s", exc)

    chunks = _relevant_chunks(question)
    history = _similar_history(question)
    sources = sorted({c["source"] for c in chunks})

    # Build context block
    context_lines: list[str] = []
    if chunks:
        context_lines.append("## Relevant Documentation\n")
        for c in chunks:
            context_lines.append(f"[{c['source']}]\n{c['content']}\n")
    if history:
        context_lines.append("\n## Related Previous Q&A\n")
        for h in history:
            context_lines.append(f"Q: {h['question']}\nA: {h['answer']}\n")

    context = "\n".join(context_lines)

    system_prompt = (
        "You are the AI assistant embedded in the DabbahWala marketing automation dashboard. "
        "DabbahWala is a fresh Indian food delivery service in Atlanta. This system is its "
        "fully automated, AI-driven marketing brain.\n\n"
        "You can answer questions at ANY level:\n"
        "- **Business purpose & strategy** — why this system exists, what problem it solves, "
        "what the marketing goals are, how the business approaches customer acquisition and retention.\n"
        "- **Functional & process level** — how lifecycle stages work, what triggers a re-engagement "
        "campaign, how a failed delivery is handled, when a contact gets escalated, how the "
        "email and SMS channels are coordinated, what the offer strategy looks like.\n"
        "- **Operational & technical level** — how the agent pipeline works, what n8n workflows "
        "run and when, how data flows between services, what the API endpoints do.\n\n"
        "WHAT TO AVOID:\n"
        "- Do NOT answer questions that require looking up *specific live records* — e.g. "
        "fetching a named customer's profile, today's order count, or real-time revenue figures. "
        "For those, tell the user to use the **Query** tab.\n"
        "- Do NOT make up facts not supported by the documentation. If the docs are silent on "
        "something, say so honestly and reason from what is documented.\n\n"
        "TONE: Speak as an expert who deeply understands both the business and the system. "
        "When asked 'why' questions (why this approach, why are you confident it works), "
        "draw on the documented design choices and reason through the logic — e.g. why a "
        "4-layer agent pipeline gives better decisions than a single call, why lifecycle "
        "segmentation improves conversion, why multi-channel coordination matters.\n\n"
        "Use markdown formatting. Be concise but thorough."
    )

    user_message = (
        f"Context from system documentation:\n\n{context}\n\n"
        f"Question: {question}\n\n"
        "Answer based on the documentation and your understanding of the system's purpose and design."
    )

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        answer = response.content[0].text
    except Exception as exc:
        logger.error("Claude API error: %s", exc)
        raise HTTPException(status_code=500, detail=f"AI error: {exc}")

    try:
        _save_interaction(question, answer, sources)
    except Exception as exc:
        logger.warning("Could not save interaction: %s", exc)

    return ChatResponse(question=question, answer=answer, sources=sources)


@router.get("/history")
async def history(limit: int = 20):
    """Return recent chatbot interactions (newest first)."""
    with get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT id, question, answer, sources, created_at
            FROM chatbot_interactions
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    return {"interactions": rows, "count": len(rows)}


@router.post("/reindex")
async def reindex():
    """Force a full re-index of all markdown documentation chunks and update stored hash."""
    docs = _load_md_files()
    if not docs:
        raise HTTPException(status_code=500, detail="No markdown files found to index")

    try:
        total = _do_index(docs)
        _save_last_indexed_at()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Indexing failed: {exc}")

    return {"status": "ok", "total_chunks": total, "files_indexed": [d["source"] for d in docs]}
