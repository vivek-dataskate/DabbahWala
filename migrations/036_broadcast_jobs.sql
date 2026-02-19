-- 036_broadcast_jobs.sql
-- Bulk messaging system for two scenarios:
--   1. Promotional / event / festival blasts → all opted-in customers
--   2. Delay alerts → contacts with active orders on a specific date

SET search_path TO dabbahwala;

-- Master job record for each bulk send
CREATE TABLE IF NOT EXISTS broadcast_jobs (
    id                  SERIAL PRIMARY KEY,
    title               TEXT NOT NULL,
    broadcast_type      TEXT NOT NULL CHECK (broadcast_type IN ('promotional', 'delay_alert')),
    channels            TEXT[] NOT NULL DEFAULT ARRAY['sms', 'email'],
    sms_message         TEXT,
    email_subject       TEXT,
    email_body          TEXT,
    target_type         TEXT NOT NULL CHECK (target_type IN ('all_customers', 'active_orders')),
    target_date         DATE,       -- for delay_alert: which date's orders to notify
    status              TEXT NOT NULL DEFAULT 'draft'
                            CHECK (status IN ('draft', 'queued', 'sending', 'completed', 'failed')),
    total_recipients    INT NOT NULL DEFAULT 0,
    sent_sms            INT NOT NULL DEFAULT 0,
    sent_email          INT NOT NULL DEFAULT 0,
    failed_count        INT NOT NULL DEFAULT 0,
    created_by          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ
);

-- Per-contact, per-channel send tracking
CREATE TABLE IF NOT EXISTS broadcast_recipients (
    id              SERIAL PRIMARY KEY,
    job_id          INT NOT NULL REFERENCES broadcast_jobs(id) ON DELETE CASCADE,
    contact_id      BIGINT NOT NULL REFERENCES contacts(id),
    channel         TEXT NOT NULL CHECK (channel IN ('sms', 'email')),
    status          TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'sending', 'sent', 'failed', 'skipped')),
    error_message   TEXT,
    sent_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (job_id, contact_id, channel)
);

CREATE INDEX IF NOT EXISTS idx_broadcast_recipients_pending
    ON broadcast_recipients (status, job_id)
    WHERE status IN ('pending', 'sending');

CREATE INDEX IF NOT EXISTS idx_broadcast_jobs_status
    ON broadcast_jobs (status, created_at DESC);
