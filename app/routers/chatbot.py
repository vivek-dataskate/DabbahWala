"""
AI Chatbot — RAG-powered Q&A over system documentation.

Indexes markdown files (README.md, ARCHITECTURE.md, IMPLEMENTATION_PLAN.md, data/sql/README.md)
into PostgreSQL full-text search chunks. Each user question retrieves relevant chunks plus
similar previous interactions, builds a context-rich prompt for Claude, and saves the
Q&A pair to chatbot_interactions for future retrieval.
"""
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

# Markdown files to index — paths relative to project root
MD_FILES = [
    "README.md",
    "ARCHITECTURE.md",
    "IMPLEMENTATION_PLAN.md",
    "data/sql/README.md",
]

CHUNK_SIZE = 900       # characters per chunk
CHUNK_OVERLAP = 120    # overlap between consecutive chunks


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
    """Load each markdown file and return [{source, content}]."""
    base = _project_root()
    docs = []
    for rel in MD_FILES:
        path = base / rel
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8")
                docs.append({"source": rel, "content": content})
                logger.info("Loaded doc %s (%d chars)", rel, len(content))
            except Exception as exc:
                logger.warning("Cannot read %s: %s", rel, exc)
        else:
            logger.warning("Doc not found: %s", path)
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


def _ensure_indexed() -> None:
    """Index markdown docs into chatbot_doc_chunks if the table is empty."""
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT COUNT(*) FROM chatbot_doc_chunks")
        if cur.fetchone()[0] > 0:
            return  # Already indexed

    docs = _load_md_files()
    if not docs:
        logger.warning("No markdown docs to index")
        return

    with get_cursor(commit=True) as cur:
        for doc in docs:
            chunks = _split_chunks(doc["content"])
            for idx, chunk in enumerate(chunks):
                cur.execute(
                    """
                    INSERT INTO chatbot_doc_chunks (source_file, chunk_index, content)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (source_file, chunk_index)
                    DO UPDATE SET content = EXCLUDED.content
                    """,
                    (doc["source"], idx, chunk),
                )
            logger.info("Indexed %d chunks from %s", len(chunks), doc["source"])


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
            return [{"source": r[0], "content": r[1]} for r in cur.fetchall()]

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
        return [{"source": r[0], "content": r[1]} for r in rows]

    # Fallback: ILIKE keyword search
    top_words = words[:5]
    conditions = " OR ".join(["content ILIKE %s"] * len(top_words))
    params: list = [f"%{w}%" for w in top_words] + [limit]
    with get_cursor(commit=False) as cur:
        cur.execute(
            f"SELECT DISTINCT source_file, content FROM chatbot_doc_chunks WHERE {conditions} LIMIT %s",
            params,
        )
        return [{"source": r[0], "content": r[1]} for r in cur.fetchall()]


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
        return [{"question": r[0], "answer": r[1]} for r in cur.fetchall()]


def _save_interaction(question: str, answer: str, sources: list[str]) -> None:
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO chatbot_interactions (question, answer, sources, model)
            VALUES (%s, %s, %s, %s)
            """,
            (question, answer, sources, CLAUDE_MODEL),
        )


# Patterns that signal a customer/order data request rather than a system question
_OUT_OF_SCOPE_PATTERNS = re.compile(
    r"\b("
    r"customer|contact|order|invoice|delivery|shipment|purchase|transaction|"
    r"email address|phone number|revenue|sales data|campaign stat|"
    r"who ordered|what did .{0,30} order|show me .{0,30} order|"
    r"look up|lookup|find me|search for|list (all |the )?(customer|contact|order)|"
    r"how many order|lapsed customer|active customer|lifecycle stage of|"
    r"pipeline snapshot|daily summary|communication history|sms log|call log"
    r")\b",
    re.IGNORECASE,
)


def _is_out_of_scope(question: str) -> bool:
    """Return True if the question is about customer/order data rather than system docs."""
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
                "This assistant is scoped to **system documentation only** — "
                "it explains how DabbahWala works (architecture, agent pipeline, "
                "API endpoints, lifecycle rules, workflows, etc.).\n\n"
                "For customer profiles, order history, campaign performance, or "
                "contact data, please use the **Query** tab instead."
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
        "You are an AI assistant for the DabbahWala marketing automation system. "
        "Your ONLY job is to explain how the SYSTEM works — its architecture, agent pipeline, "
        "API endpoints, database schema, lifecycle rules, n8n workflows, integrations, and "
        "configuration — based strictly on the provided system documentation.\n\n"
        "STRICT SCOPE RULES:\n"
        "- Answer ONLY questions about how the system is built and how it operates.\n"
        "- Do NOT answer questions about specific customers, contacts, orders, revenue figures, "
        "campaign statistics, or any live data. If asked, tell the user to use the Query tab.\n"
        "- Do NOT query or reference any database data; your knowledge comes only from the "
        "documentation context provided.\n\n"
        "Answer clearly and concisely. Use markdown formatting where it helps readability. "
        "If the documentation does not cover the question, say so honestly."
    )

    user_message = (
        f"Context from system documentation:\n\n{context}\n\n"
        f"Question: {question}\n\n"
        "Please answer the question based on the documentation above."
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
    """Clear and re-index all markdown documentation chunks."""
    with get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM chatbot_doc_chunks")

    try:
        _ensure_indexed()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Indexing failed: {exc}")

    with get_cursor(commit=False) as cur:
        cur.execute("SELECT COUNT(*) FROM chatbot_doc_chunks")
        total = cur.fetchone()[0]

    return {"status": "ok", "total_chunks": total}
