-- 007_create_missing_tables.sql
-- Creates tables that were added to 002_tables.sql AFTER the initial deploy.
-- The render_build.sh only applies each migration once, so tables added to
-- 002_tables.sql after its first apply are missing on production.
-- This migration is idempotent (uses IF NOT EXISTS throughout).

SET search_path TO public;

-- ---------------------------------------------------------------------------
-- Menu catalog (Airtable sync target for weekly menu)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS menu_catalog (
    id                 BIGSERIAL PRIMARY KEY,
    item_name          TEXT UNIQUE NOT NULL,
    category           TEXT,
    is_veg             BOOLEAN,
    description        TEXT,
    image_url          TEXT,
    price              NUMERIC(8,2),
    active             BOOLEAN NOT NULL DEFAULT TRUE,
    added_date         DATE,
    discarded_date     DATE,
    airtable_record_id TEXT,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_menu_catalog_airtable_id
    ON menu_catalog (airtable_record_id) WHERE airtable_record_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_menu_catalog_active ON menu_catalog (active);
CREATE INDEX IF NOT EXISTS idx_menu_catalog_name   ON menu_catalog (item_name);

CREATE TABLE IF NOT EXISTS menu_catalog_history (
    id              BIGSERIAL PRIMARY KEY,
    menu_catalog_id BIGINT NOT NULL REFERENCES menu_catalog(id),
    item_name       TEXT NOT NULL,
    change_type     TEXT NOT NULL,
    field_changed   TEXT,
    old_value       TEXT,
    new_value       TEXT,
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    source          TEXT DEFAULT 'airtable_sync'
);

CREATE INDEX IF NOT EXISTS idx_menu_history_item
    ON menu_catalog_history (menu_catalog_id, changed_at DESC);

-- ---------------------------------------------------------------------------
-- Broadcast jobs + recipients (promotional blasts and delay alerts)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS broadcast_jobs (
    id               SERIAL PRIMARY KEY,
    title            TEXT NOT NULL,
    broadcast_type   TEXT NOT NULL CHECK (broadcast_type IN (
                         'promo', 'announcement', 'reactivation', 'event', 'custom')),
    channels         TEXT[] NOT NULL DEFAULT ARRAY['sms', 'email'],
    sms_message      TEXT,
    email_subject    TEXT,
    email_body       TEXT,
    target_type      TEXT NOT NULL CHECK (target_type IN (
                         'all_active', 'lapsed', 'segment', 'manual_list')),
    target_date      DATE,
    status           TEXT NOT NULL DEFAULT 'draft'
                         CHECK (status IN ('draft', 'scheduled', 'running', 'completed', 'cancelled')),
    total_recipients INT NOT NULL DEFAULT 0,
    sent_sms         INT NOT NULL DEFAULT 0,
    sent_email       INT NOT NULL DEFAULT 0,
    failed_count     INT NOT NULL DEFAULT 0,
    created_by       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at       TIMESTAMPTZ,
    completed_at     TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS broadcast_recipients (
    id            SERIAL PRIMARY KEY,
    job_id        INT NOT NULL REFERENCES broadcast_jobs(id) ON DELETE CASCADE,
    contact_id    BIGINT NOT NULL REFERENCES contacts(id),
    channel       TEXT NOT NULL CHECK (channel IN ('sms', 'email')),
    status        TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'sent', 'failed', 'skipped')),
    error_message TEXT,
    sent_at       TIMESTAMPTZ,
    sms_message   TEXT,
    email_subject TEXT,
    email_body    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (job_id, contact_id, channel)
);

CREATE INDEX IF NOT EXISTS idx_broadcast_recipients_job    ON broadcast_recipients (job_id);
CREATE INDEX IF NOT EXISTS idx_broadcast_recipients_status ON broadcast_recipients (status);

-- ---------------------------------------------------------------------------
-- Chatbot doc chunks and interactions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS chatbot_doc_chunks (
    id           SERIAL PRIMARY KEY,
    source_file  TEXT NOT NULL,
    chunk_index  INTEGER NOT NULL,
    content      TEXT NOT NULL,
    content_tsv  TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chatbot_chunks_fts
    ON chatbot_doc_chunks USING gin (content_tsv);

CREATE TABLE IF NOT EXISTS chatbot_interactions (
    id         SERIAL PRIMARY KEY,
    question   TEXT NOT NULL,
    answer     TEXT NOT NULL,
    sources    TEXT[] DEFAULT '{}',
    model      TEXT DEFAULT 'claude-sonnet-4-5-20250929',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chatbot_doc_meta (
    id            SERIAL PRIMARY KEY,
    source_file   TEXT NOT NULL UNIQUE,
    last_modified TIMESTAMPTZ,
    chunk_count   INTEGER DEFAULT 0,
    indexed_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chatbot_canned_qa (
    id         SERIAL PRIMARY KEY,
    question   TEXT NOT NULL,
    answer     TEXT NOT NULL,
    tags       TEXT[] DEFAULT '{}',
    active     BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Content embeddings (for chatbot + team content search)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS content_embeddings (
    id           SERIAL PRIMARY KEY,
    content_id   INTEGER,
    content_type TEXT,
    content_text TEXT,
    embedding    FLOAT[],
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Goal experiments (Growth Agent)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS test_runs (
    id          SERIAL PRIMARY KEY,
    run_type    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'running',
    started_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    results     JSONB DEFAULT '{}',
    error       TEXT
);

CREATE TABLE IF NOT EXISTS goal_experiments (
    id                  SERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    hypothesis          TEXT,
    experiment_type     TEXT NOT NULL CHECK (experiment_type IN (
                            'menu_change', 'pricing', 'messaging', 'channel', 'promo',
                            'timing', 'other')),
    channel             TEXT,
    cohort_size         INTEGER DEFAULT 0,
    measure_at          DATE,
    measure_days        INTEGER DEFAULT 7,
    status              TEXT NOT NULL DEFAULT 'running'
                            CHECK (status IN ('running', 'measured', 'archived')),
    is_winner           BOOLEAN,
    baseline_rate       FLOAT,
    conversion_rate     FLOAT,
    orders_won          INTEGER DEFAULT 0,
    learnings           TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    measured_at         TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS goal_experiment_contacts (
    id            SERIAL PRIMARY KEY,
    experiment_id INTEGER NOT NULL REFERENCES goal_experiments(id) ON DELETE CASCADE,
    contact_id    INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    assigned_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    converted     BOOLEAN DEFAULT FALSE,
    UNIQUE (experiment_id, contact_id)
);

CREATE TABLE IF NOT EXISTS discovered_signals (
    id          SERIAL PRIMARY KEY,
    signal_type TEXT NOT NULL,
    signal_data JSONB NOT NULL DEFAULT '{}',
    source      TEXT,
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS goal_agent_runs (
    id          SERIAL PRIMARY KEY,
    run_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status      TEXT NOT NULL DEFAULT 'ok',
    summary     TEXT,
    actions_taken INTEGER DEFAULT 0,
    experiments_launched INTEGER DEFAULT 0,
    experiments_measured INTEGER DEFAULT 0,
    baseline_rate FLOAT,
    errors      TEXT[] DEFAULT '{}'
);

-- ---------------------------------------------------------------------------
-- Competitor agent runs + experiments
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS competitor_agent_runs (
    id           SERIAL PRIMARY KEY,
    run_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status       TEXT NOT NULL DEFAULT 'ok',
    insights     JSONB DEFAULT '{}',
    summary      TEXT,
    model_used   TEXT,
    error        TEXT
);

CREATE TABLE IF NOT EXISTS experiments (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    hypothesis      TEXT,
    experiment_type TEXT,
    channel         TEXT,
    cohort_size     INTEGER DEFAULT 0,
    measure_at      DATE,
    measure_days    INTEGER DEFAULT 7,
    status          TEXT NOT NULL DEFAULT 'running'
                        CHECK (status IN ('running', 'measured', 'archived')),
    is_winner       BOOLEAN,
    baseline_rate   FLOAT,
    conversion_rate FLOAT,
    orders_won      INTEGER DEFAULT 0,
    learnings       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    measured_at     TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS experiment_contacts (
    id            SERIAL PRIMARY KEY,
    experiment_id INTEGER NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    contact_id    INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    assigned_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    converted     BOOLEAN DEFAULT FALSE,
    UNIQUE (experiment_id, contact_id)
);

CREATE TABLE IF NOT EXISTS growth_baseline (
    id              SERIAL PRIMARY KEY,
    measured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    conv_rate       FLOAT NOT NULL,
    active_contacts INTEGER DEFAULT 0,
    orders_7d       INTEGER DEFAULT 0,
    orders_30d      INTEGER DEFAULT 0
);
