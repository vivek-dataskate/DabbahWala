-- 022_vector_search.sql
-- pgvector extension for RAG-based semantic intent mining.
--
-- WHY VECTORS?
-- Signal 4 (reorder intent) uses ILIKE keyword matching which misses:
--   "I want to try the new menu"
--   "my wife loved the biryani"
--   "same thing next week please"
--   "when can I get another batch"
--
-- With embeddings, we match MEANING not keywords. This directly generates
-- more orders by catching intent that keyword matching misses.
--
-- ARCHITECTURE:
--   1. Embeddings table stores vectors for transcripts, SMS bodies, delivery notes
--   2. Python/n8n calls an embedding API (OpenAI, Cohere, etc.) for new content
--   3. detect_reorder_intent_semantic() replaces keyword ILIKE with vector similarity
--   4. find_similar_customers() finds customers with profiles like top converters
--
-- REQUIRES: CREATE EXTENSION vector; (run as superuser if needed)

SET search_path TO dabbahwala;

-- Enable pgvector (1536 dimensions = OpenAI ada-002, adjust for your model)
CREATE EXTENSION IF NOT EXISTS vector;


-- EMBEDDINGS TABLE — stores vectors for any content type
CREATE TABLE IF NOT EXISTS content_embeddings (
    id              BIGSERIAL PRIMARY KEY,
    source_type     TEXT NOT NULL CHECK (source_type IN ('transcript', 'sms', 'delivery_note', 'customer_profile')),
    source_id       BIGINT NOT NULL,
    contact_id      BIGINT NOT NULL REFERENCES contacts(id),
    content_text    TEXT NOT NULL,
    embedding       vector(1536),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_embeddings_source ON content_embeddings (source_type, source_id);
CREATE INDEX idx_embeddings_contact ON content_embeddings (contact_id);

-- HNSW index for fast approximate nearest neighbor search
CREATE INDEX idx_embeddings_vector ON content_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);


-- INTENT PHRASES TABLE — seed vectors representing purchase intent
-- These are compared against transcript/SMS embeddings to find buying signals
CREATE TABLE IF NOT EXISTS intent_phrases (
    id          SERIAL PRIMARY KEY,
    phrase      TEXT NOT NULL,
    intent_type TEXT NOT NULL CHECK (intent_type IN ('reorder', 'positive_feedback', 'referral', 'upsell', 'complaint')),
    embedding   vector(1536),
    weight      NUMERIC(3,2) NOT NULL DEFAULT 1.0
);


-- STORE EMBEDDING
CREATE OR REPLACE FUNCTION store_embedding(
    p_source_type TEXT,
    p_source_id BIGINT,
    p_contact_id BIGINT,
    p_content_text TEXT,
    p_embedding vector(1536)
)
RETURNS BIGINT
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_id BIGINT;
BEGIN
    INSERT INTO content_embeddings (source_type, source_id, contact_id, content_text, embedding)
    VALUES (p_source_type, p_source_id, p_contact_id, p_content_text, p_embedding)
    ON CONFLICT DO NOTHING
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;


-- SEMANTIC INTENT DETECTION — replaces keyword ILIKE with vector similarity
-- Finds transcripts/SMS semantically similar to purchase-intent phrases
CREATE OR REPLACE FUNCTION detect_reorder_intent_semantic(
    p_intent_embedding vector(1536),
    p_similarity_threshold NUMERIC DEFAULT 0.78,
    p_days INT DEFAULT 3
)
RETURNS TABLE(
    contact_id BIGINT, email TEXT, first_name TEXT, last_name TEXT, phone TEXT,
    lifecycle_segment lifecycle_segment, total_orders INT, last_order_at TIMESTAMPTZ,
    content_text TEXT, similarity NUMERIC, source_type TEXT
)
LANGUAGE sql
SET search_path TO dabbahwala
AS $$
    SELECT DISTINCT ON (c.id)
           c.id AS contact_id, c.email, c.first_name, c.last_name, c.phone,
           c.lifecycle_segment, c.total_orders, c.last_order_at,
           ce.content_text,
           (1 - (ce.embedding <=> p_intent_embedding))::numeric AS similarity,
           ce.source_type
    FROM content_embeddings ce
    JOIN contacts c ON c.id = ce.contact_id
    WHERE ce.source_type IN ('transcript', 'sms')
      AND ce.created_at > now() - (p_days || ' days')::interval
      AND (1 - (ce.embedding <=> p_intent_embedding)) > p_similarity_threshold
      AND NOT EXISTS (
          SELECT 1 FROM opportunities o
          WHERE o.contact_id = c.id AND o.status IN ('pending', 'dispatched')
      )
    ORDER BY c.id, similarity DESC;
$$;


-- FIND SIMILAR CUSTOMERS — finds contacts with profiles like top converters
-- Use case: embed a "ideal customer" profile, find similar ones to target
CREATE OR REPLACE FUNCTION find_similar_customers(
    p_profile_embedding vector(1536),
    p_limit INT DEFAULT 20,
    p_exclude_optout BOOLEAN DEFAULT true
)
RETURNS TABLE(
    contact_id BIGINT, email TEXT, first_name TEXT, last_name TEXT,
    lifecycle_segment lifecycle_segment, total_orders INT,
    similarity NUMERIC
)
LANGUAGE sql
SET search_path TO dabbahwala
AS $$
    SELECT c.id AS contact_id, c.email, c.first_name, c.last_name,
           c.lifecycle_segment, c.total_orders,
           (1 - (ce.embedding <=> p_profile_embedding))::numeric AS similarity
    FROM content_embeddings ce
    JOIN contacts c ON c.id = ce.contact_id
    WHERE ce.source_type = 'customer_profile'
      AND (NOT p_exclude_optout OR c.lifecycle_segment != 'optout')
    ORDER BY ce.embedding <=> p_profile_embedding
    LIMIT p_limit;
$$;


-- SEARCH CONTENT BY MEANING — general-purpose semantic search across all content
CREATE OR REPLACE FUNCTION search_content_semantic(
    p_query_embedding vector(1536),
    p_source_type TEXT DEFAULT NULL,
    p_limit INT DEFAULT 20
)
RETURNS TABLE(
    id BIGINT, source_type TEXT, source_id BIGINT, contact_id BIGINT,
    content_text TEXT, similarity NUMERIC, created_at TIMESTAMPTZ
)
LANGUAGE sql
SET search_path TO dabbahwala
AS $$
    SELECT ce.id, ce.source_type, ce.source_id, ce.contact_id,
           ce.content_text,
           (1 - (ce.embedding <=> p_query_embedding))::numeric AS similarity,
           ce.created_at
    FROM content_embeddings ce
    WHERE (p_source_type IS NULL OR ce.source_type = p_source_type)
    ORDER BY ce.embedding <=> p_query_embedding
    LIMIT p_limit;
$$;


-- SEED INTENT PHRASES (embeddings to be populated by the embedding pipeline)
INSERT INTO intent_phrases (phrase, intent_type, weight) VALUES
    ('I want to reorder', 'reorder', 1.0),
    ('Can I get the same thing again', 'reorder', 1.0),
    ('Same order next week', 'reorder', 1.0),
    ('When is the next delivery', 'reorder', 0.9),
    ('I need more of the same', 'reorder', 1.0),
    ('My family loved it', 'positive_feedback', 0.8),
    ('The food was amazing', 'positive_feedback', 0.8),
    ('Best biryani I have had', 'positive_feedback', 0.9),
    ('I told my friends about you', 'referral', 0.9),
    ('My neighbor wants to order too', 'referral', 1.0),
    ('Can I try the other menu items', 'upsell', 0.85),
    ('What else do you have', 'upsell', 0.8),
    ('Do you have a family pack', 'upsell', 0.9)
ON CONFLICT DO NOTHING;
