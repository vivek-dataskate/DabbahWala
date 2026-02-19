-- 029_team_content.sql
-- Google Docs ingestion: field notes, ad copies, and team observations.
--
-- Content flows:
--   1. n8n polls Google Drive folder → reads docs → POST /api/team-content/sync → stored here
--   2. Team submits via n8n form → POST /api/team-content/submit → stored here + Airtable
--   3. Query form categories browse/search this table via stored procs
--   4. Free-form Claude queries include recent team content as context

SET search_path TO dabbahwala;

-- ---------------------------------------------------------------------------
-- Table
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS team_content (
    id                   BIGSERIAL PRIMARY KEY,
    google_doc_id        TEXT,                    -- NULL for form submissions
    content_type         TEXT NOT NULL CHECK (content_type IN
                           ('ground_note', 'ad_copy', 'observation', 'question')),
    title                TEXT NOT NULL,
    body                 TEXT NOT NULL,
    author               TEXT,
    source               TEXT NOT NULL DEFAULT 'google_docs',  -- google_docs | form_submission
    google_last_modified TIMESTAMPTZ,
    doc_url              TEXT,
    tags                 TEXT[] DEFAULT '{}',
    is_active            BOOLEAN NOT NULL DEFAULT true,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Prevent duplicate syncs of the same Google Doc
CREATE UNIQUE INDEX IF NOT EXISTS idx_team_content_gdoc
    ON team_content (google_doc_id) WHERE google_doc_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_team_content_type ON team_content (content_type);
CREATE INDEX IF NOT EXISTS idx_team_content_active ON team_content (is_active, content_type);
CREATE INDEX IF NOT EXISTS idx_team_content_created ON team_content (created_at DESC);

-- Full-text search index for keyword queries
CREATE INDEX IF NOT EXISTS idx_team_content_fts
    ON team_content USING gin (to_tsvector('english', title || ' ' || body));

-- ---------------------------------------------------------------------------
-- Extend content_embeddings to accept team_content source type
-- ---------------------------------------------------------------------------
ALTER TABLE content_embeddings
    DROP CONSTRAINT IF EXISTS content_embeddings_source_type_check;

ALTER TABLE content_embeddings
    ADD CONSTRAINT content_embeddings_source_type_check
    CHECK (source_type IN ('transcript', 'sms', 'delivery_note', 'customer_profile', 'team_content'));

-- ---------------------------------------------------------------------------
-- Search team content — full-text search with relevance ranking
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION search_team_content(
    p_content_type TEXT DEFAULT NULL,
    p_search_query TEXT DEFAULT NULL,
    p_limit INT DEFAULT 20,
    p_offset INT DEFAULT 0
)
RETURNS TABLE(
    id BIGINT,
    content_type TEXT,
    title TEXT,
    body TEXT,
    author TEXT,
    source TEXT,
    tags TEXT[],
    created_at TIMESTAMPTZ,
    doc_url TEXT,
    relevance REAL
)
LANGUAGE sql
STABLE
SET search_path TO dabbahwala
AS $$
    SELECT
        tc.id,
        tc.content_type,
        tc.title,
        tc.body,
        tc.author,
        tc.source,
        tc.tags,
        tc.created_at,
        tc.doc_url,
        CASE
            WHEN p_search_query IS NOT NULL AND p_search_query != ''
            THEN ts_rank(to_tsvector('english', tc.title || ' ' || tc.body),
                         plainto_tsquery('english', p_search_query))
            ELSE 0.0
        END AS relevance
    FROM team_content tc
    WHERE tc.is_active = true
      AND (p_content_type IS NULL OR tc.content_type = p_content_type)
      AND (
          p_search_query IS NULL
          OR p_search_query = ''
          OR to_tsvector('english', tc.title || ' ' || tc.body)
             @@ plainto_tsquery('english', p_search_query)
      )
    ORDER BY
        CASE WHEN p_search_query IS NOT NULL AND p_search_query != ''
             THEN ts_rank(to_tsvector('english', tc.title || ' ' || tc.body),
                          plainto_tsquery('english', p_search_query))
             ELSE 0 END DESC,
        tc.created_at DESC
    LIMIT p_limit
    OFFSET p_offset;
$$;

-- ---------------------------------------------------------------------------
-- Browse recent team content — simple list with body preview
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION get_recent_team_content(
    p_content_type TEXT DEFAULT NULL,
    p_days INT DEFAULT 30,
    p_limit INT DEFAULT 25
)
RETURNS TABLE(
    id BIGINT,
    content_type TEXT,
    title TEXT,
    body_preview TEXT,
    author TEXT,
    created_at TIMESTAMPTZ,
    doc_url TEXT
)
LANGUAGE sql
STABLE
SET search_path TO dabbahwala
AS $$
    SELECT
        tc.id,
        tc.content_type,
        tc.title,
        LEFT(tc.body, 300) AS body_preview,
        tc.author,
        tc.created_at,
        tc.doc_url
    FROM team_content tc
    WHERE tc.is_active = true
      AND (p_content_type IS NULL OR tc.content_type = p_content_type)
      AND tc.created_at > now() - (p_days || ' days')::interval
    ORDER BY tc.created_at DESC
    LIMIT p_limit;
$$;
