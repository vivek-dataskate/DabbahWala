-- Migration 032: Agent Intelligence Tables
-- Adds tables for inference results, decision recommendations,
-- action queue, customer goals, and orchestrator audit log.

-- 1. Customer goals — one active goal per contact
CREATE TABLE IF NOT EXISTS customer_goals (
    id              SERIAL PRIMARY KEY,
    contact_id      INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    goal            TEXT    NOT NULL CHECK (goal IN ('convert_to_order', 'retain', 'reactivate')),
    deadline        DATE,
    status          TEXT    NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'achieved', 'expired', 'failed')),
    converted       BOOLEAN NOT NULL DEFAULT FALSE,
    progress_notes  TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_customer_goals_contact ON customer_goals(contact_id);
CREATE INDEX IF NOT EXISTS idx_customer_goals_status  ON customer_goals(status);

-- 2. Inference results — output of the 3 inference agents per run
CREATE TABLE IF NOT EXISTS inference_results (
    id                      SERIAL PRIMARY KEY,
    contact_id              INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    goal_id                 INTEGER REFERENCES customer_goals(id),
    run_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Sentiment agent
    sentiment               TEXT CHECK (sentiment IN ('positive', 'neutral', 'negative')),
    sentiment_confidence    FLOAT,
    sentiment_summary       TEXT,
    -- Intent agent
    intent                  TEXT CHECK (intent IN ('ready_to_order', 'needs_info', 'price_sensitive', 'not_interested', 'unknown')),
    intent_signals          JSONB DEFAULT '[]',
    intent_confidence       FLOAT,
    -- Engagement agent
    engagement_score        FLOAT CHECK (engagement_score BETWEEN 0 AND 1),
    engagement_trend        TEXT CHECK (engagement_trend IN ('rising', 'falling', 'flat')),
    last_touch_hours_ago    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_inference_contact ON inference_results(contact_id);
CREATE INDEX IF NOT EXISTS idx_inference_run_at  ON inference_results(run_at DESC);

-- 3. Decision recommendations — output of the 4 decision agents per run
CREATE TABLE IF NOT EXISTS decision_recommendations (
    id                      SERIAL PRIMARY KEY,
    contact_id              INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    inference_result_id     INTEGER REFERENCES inference_results(id),
    run_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Stage transition agent
    recommended_stage       TEXT,
    stage_confidence        FLOAT,
    stage_reason            TEXT,
    -- Channel agent
    recommended_channel     TEXT CHECK (recommended_channel IN ('sms', 'email', 'call', 'none')),
    channel_timing          TEXT CHECK (channel_timing IN ('immediate', 'tomorrow', '3days', 'none')),
    channel_reason          TEXT,
    -- Offer agent
    offer_type              TEXT CHECK (offer_type IN ('discount', 'reminder', 'social_proof', 'none')),
    suggested_copy          TEXT,
    offer_reason            TEXT,
    -- Escalation agent
    should_escalate         BOOLEAN NOT NULL DEFAULT FALSE,
    escalation_urgency      TEXT CHECK (escalation_urgency IN ('high', 'medium', 'none')),
    escalation_reason       TEXT
);
CREATE INDEX IF NOT EXISTS idx_decision_contact ON decision_recommendations(contact_id);
CREATE INDEX IF NOT EXISTS idx_decision_run_at  ON decision_recommendations(run_at DESC);

-- 4. Orchestrator log — audit trail of every orchestrator decision
CREATE TABLE IF NOT EXISTS orchestrator_log (
    id                          SERIAL PRIMARY KEY,
    contact_id                  INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    decision_recommendation_id  INTEGER REFERENCES decision_recommendations(id),
    goal_id                     INTEGER REFERENCES customer_goals(id),
    run_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    chosen_action               TEXT,
    chosen_channel              TEXT,
    reasoning                   TEXT,
    guardrails_applied          JSONB DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_orch_log_contact ON orchestrator_log(contact_id);
CREATE INDEX IF NOT EXISTS idx_orch_log_run_at  ON orchestrator_log(run_at DESC);

-- 5. Action queue — approved actions waiting for n8n executors
CREATE TABLE IF NOT EXISTS action_queue (
    id                  SERIAL PRIMARY KEY,
    contact_id          INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    orchestrator_log_id INTEGER REFERENCES orchestrator_log(id),
    action_type         TEXT NOT NULL CHECK (action_type IN ('send_sms', 'move_campaign', 'escalate_airtable', 'none')),
    payload             JSONB NOT NULL DEFAULT '{}',
    status              TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'executing', 'done', 'failed')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    executed_at         TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_action_queue_status  ON action_queue(status);
CREATE INDEX IF NOT EXISTS idx_action_queue_contact ON action_queue(contact_id);

-- Helper: auto-update updated_at on customer_goals
CREATE OR REPLACE FUNCTION update_customer_goals_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_customer_goals_updated_at ON customer_goals;
CREATE TRIGGER trg_customer_goals_updated_at
    BEFORE UPDATE ON customer_goals
    FOR EACH ROW EXECUTE FUNCTION update_customer_goals_updated_at();
