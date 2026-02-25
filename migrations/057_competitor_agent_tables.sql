-- Competitor Research Agent Tables
-- Tracks weekly competitor intelligence runs and the hypotheses injected into goal_experiments.
-- The competitor agent parses .eml samples + scrapes competitor websites + uses food-subscription
-- best practices to generate experiment ideas targeting 100% repeat order rate.

-- Track which source generated each goal_experiment
ALTER TABLE goal_experiments ADD COLUMN IF NOT EXISTS source VARCHAR(50) DEFAULT 'goal_agent';
CREATE INDEX IF NOT EXISTS idx_goal_experiments_source ON goal_experiments(source);

-- Audit log for competitor agent runs
CREATE TABLE IF NOT EXISTS competitor_agent_runs (
    id                  SERIAL PRIMARY KEY,
    sources_processed   INTEGER NOT NULL DEFAULT 0,
    email_samples_parsed INTEGER NOT NULL DEFAULT 0,
    websites_scraped    INTEGER NOT NULL DEFAULT 0,
    hypotheses_queued   INTEGER NOT NULL DEFAULT 0,
    status              VARCHAR(20) NOT NULL DEFAULT 'completed', -- completed | failed
    summary             TEXT,
    error_detail        TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_competitor_agent_runs_created_at ON competitor_agent_runs(created_at);
