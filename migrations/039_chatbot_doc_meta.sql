-- 039_chatbot_doc_meta.sql
-- Key/value store for chatbot metadata (e.g. docs content hash for change detection)

CREATE TABLE IF NOT EXISTS chatbot_doc_meta (
    key        TEXT PRIMARY KEY,
    value      TEXT        NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
