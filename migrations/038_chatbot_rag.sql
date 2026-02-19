-- 038_chatbot_rag.sql
-- RAG chatbot: documentation chunks + interaction history

-- Document chunks from markdown files (knowledge base)
CREATE TABLE IF NOT EXISTS chatbot_doc_chunks (
    id           SERIAL PRIMARY KEY,
    source_file  TEXT    NOT NULL,
    chunk_index  INTEGER NOT NULL,
    content      TEXT    NOT NULL,
    content_tsv  TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_doc_chunks_tsv    ON chatbot_doc_chunks USING GIN (content_tsv);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_source ON chatbot_doc_chunks (source_file);
CREATE UNIQUE INDEX IF NOT EXISTS idx_doc_chunks_source_chunk ON chatbot_doc_chunks (source_file, chunk_index);

-- Q&A interaction history (RAG memory store)
CREATE TABLE IF NOT EXISTS chatbot_interactions (
    id         SERIAL PRIMARY KEY,
    question   TEXT        NOT NULL,
    answer     TEXT        NOT NULL,
    sources    TEXT[]      DEFAULT '{}',
    model      TEXT        DEFAULT 'claude-sonnet-4-5-20250929',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chatbot_interactions_created ON chatbot_interactions (created_at DESC);
