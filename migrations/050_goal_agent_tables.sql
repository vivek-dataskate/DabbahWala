-- Goal-Oriented Agent Tables
-- Tracks experiments the agent generates, runs, measures, and converts to signals.
-- The goal-oriented agent doesn't wait for signals — it generates hypotheses,
-- tests them against real contact cohorts, and turns proven ones into new signals.

-- Experiment hypotheses and their lifecycle
CREATE TABLE IF NOT EXISTS goal_experiments (
    id                      SERIAL PRIMARY KEY,
    hypothesis              TEXT NOT NULL,
    experiment_type         VARCHAR(50),           -- cohort_message, timing_test, offer_test, channel_test, reactivation
    status                  VARCHAR(20) NOT NULL DEFAULT 'pending',
                                                   -- pending | running | measuring | concluded
    cohort_description      TEXT,                  -- Human-readable description of who to target
    cohort_filter           JSONB,                 -- Structured filter (lifecycle_segment, days_since_order, etc.)
    cohort_sql              TEXT,                  -- Actual SQL used to select the cohort
    action_type             VARCHAR(30) DEFAULT 'send_sms',
    message_template        TEXT,                  -- SMS template (may use {first_name} etc.)
    success_metric          VARCHAR(50) DEFAULT 'orders_placed_72h',
    success_threshold       FLOAT DEFAULT 0.10,    -- Experiment is "proven" if conversion >= this
    cohort_size             INTEGER DEFAULT 0,
    enrolled_count          INTEGER DEFAULT 0,
    measurement_window_hours INTEGER DEFAULT 72,
    started_at              TIMESTAMPTZ,
    measurement_due_at      TIMESTAMPTZ,
    concluded_at            TIMESTAMPTZ,
    result_conversion_rate  FLOAT,
    result_success_count    INTEGER,
    result_sample_size      INTEGER,
    conclusion              VARCHAR(20),           -- proven | disproven | inconclusive
    conclusion_notes        TEXT,
    generated_signal_id     INTEGER,               -- FK to discovered_signals once signal is created
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_goal_experiments_status ON goal_experiments(status);
CREATE INDEX IF NOT EXISTS idx_goal_experiments_created_at ON goal_experiments(created_at);

-- Individual contacts enrolled in an experiment
CREATE TABLE IF NOT EXISTS goal_experiment_contacts (
    id              SERIAL PRIMARY KEY,
    experiment_id   INTEGER NOT NULL REFERENCES goal_experiments(id) ON DELETE CASCADE,
    contact_id      INTEGER NOT NULL REFERENCES contacts(id),
    action_queue_id INTEGER,                       -- FK to action_queue row that was created
    message_sent    TEXT,                          -- Actual message body sent
    enrolled_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    converted       BOOLEAN,                       -- NULL = not yet measured; TRUE = ordered; FALSE = did not
    conversion_at   TIMESTAMPTZ,
    outcome_checked_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_goal_experiment_contacts_experiment_id ON goal_experiment_contacts(experiment_id);
CREATE INDEX IF NOT EXISTS idx_goal_experiment_contacts_contact_id ON goal_experiment_contacts(contact_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_goal_experiment_contacts_unique ON goal_experiment_contacts(experiment_id, contact_id);

-- Signals discovered from proven experiments — reusable intelligence for the system
CREATE TABLE IF NOT EXISTS discovered_signals (
    id                    SERIAL PRIMARY KEY,
    signal_name           VARCHAR(100) NOT NULL UNIQUE,
    signal_description    TEXT,
    source_experiment_id  INTEGER REFERENCES goal_experiments(id),
    detection_sql         TEXT,                   -- SQL returning contact_ids that match this signal
    confidence            FLOAT,                  -- Conversion rate from source experiment
    activation_count      INTEGER NOT NULL DEFAULT 0,  -- How many times this signal has triggered actions
    is_active             BOOLEAN NOT NULL DEFAULT TRUE,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_discovered_signals_is_active ON discovered_signals(is_active);

-- Audit log for each goal agent run
CREATE TABLE IF NOT EXISTS goal_agent_runs (
    id                      SERIAL PRIMARY KEY,
    run_type                VARCHAR(50),           -- full | hypothesize | experiment | measure | harvest
    experiments_created     INTEGER NOT NULL DEFAULT 0,
    experiments_started     INTEGER NOT NULL DEFAULT 0,
    contacts_enrolled       INTEGER NOT NULL DEFAULT 0,
    experiments_concluded   INTEGER NOT NULL DEFAULT 0,
    signals_discovered      INTEGER NOT NULL DEFAULT 0,
    orders_attributed       INTEGER NOT NULL DEFAULT 0,
    reasoning               TEXT,
    error_detail            TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
