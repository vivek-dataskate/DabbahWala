-- 055_growth_experiments.sql
-- Growth hacker agent: experiment tracking tables
-- The agent invents new ways to generate orders, runs them on small cohorts,
-- measures results, and learns what works.

SET search_path TO dabbahwala;

-- One row per experiment the growth agent designs
CREATE TABLE IF NOT EXISTS experiments (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    hypothesis      TEXT NOT NULL,          -- "We think that X will cause Y because Z"
    experiment_type TEXT NOT NULL,          -- 'timing' | 'offer' | 'message_angle' | 'channel_sequence'
    cohort_size     INT NOT NULL DEFAULT 0,
    channel         TEXT,                   -- 'sms' | 'email' | 'phone' | 'sequence'
    message_template TEXT,                  -- the message/script used
    offer_detail    TEXT,                   -- e.g. "free dessert on next order"
    metadata        JSONB NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'running',  -- 'running' | 'measuring' | 'complete' | 'failed'
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    measure_at      TIMESTAMPTZ,            -- when to evaluate results (usually 7d after start)
    measured_at     TIMESTAMPTZ,
    -- Results
    contacts_reached    INT DEFAULT 0,
    orders_won          INT DEFAULT 0,
    conversion_rate     NUMERIC(5,4) DEFAULT 0,
    is_winner           BOOLEAN,            -- did it beat the baseline?
    learnings           TEXT,               -- Claude's post-experiment analysis
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments (status, measure_at);
CREATE INDEX IF NOT EXISTS idx_experiments_type   ON experiments (experiment_type);

-- Tracks which contacts are in which experiment and their outcome
CREATE TABLE IF NOT EXISTS experiment_contacts (
    id              BIGSERIAL PRIMARY KEY,
    experiment_id   BIGINT NOT NULL REFERENCES experiments(id),
    contact_id      BIGINT NOT NULL REFERENCES contacts(id),
    opportunity_id  BIGINT,                 -- the opportunity created for this contact
    action_sent     TEXT,                   -- 'sms' | 'email' | 'call'
    message_sent    TEXT,
    sent_at         TIMESTAMPTZ,
    -- Outcome
    ordered         BOOLEAN NOT NULL DEFAULT false,
    order_id        BIGINT REFERENCES orders(id),
    ordered_at      TIMESTAMPTZ,
    -- Attribution
    days_to_order   INT,                    -- how many days between send and order
    UNIQUE (experiment_id, contact_id)
);

CREATE INDEX IF NOT EXISTS idx_exp_contacts_exp     ON experiment_contacts (experiment_id);
CREATE INDEX IF NOT EXISTS idx_exp_contacts_contact ON experiment_contacts (contact_id);
CREATE INDEX IF NOT EXISTS idx_exp_contacts_ordered ON experiment_contacts (experiment_id, ordered);

-- Baseline conversion rate (updated by the growth agent for comparison)
CREATE TABLE IF NOT EXISTS growth_baseline (
    id                  BIGSERIAL PRIMARY KEY,
    measured_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    period_days         INT NOT NULL DEFAULT 7,
    total_contacts      INT NOT NULL DEFAULT 0,
    total_orders        INT NOT NULL DEFAULT 0,
    baseline_conv_rate  NUMERIC(5,4) NOT NULL DEFAULT 0,
    notes               TEXT
);
