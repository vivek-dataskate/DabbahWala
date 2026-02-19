-- Migration 040: pre-computed cache for suggestion-chip answers
-- Stores one canonical answer per chip question so repeated clicks never hit the AI.

CREATE TABLE IF NOT EXISTS chatbot_canned_qa (
    question    TEXT PRIMARY KEY,
    answer      TEXT        NOT NULL,
    sources     TEXT[]      DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_canned_qa_lower
    ON chatbot_canned_qa (lower(question));
