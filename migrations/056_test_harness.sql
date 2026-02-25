-- Migration 056: Test Harness
-- Stores test run metadata and results for the daily E2E test suite.

CREATE TABLE IF NOT EXISTS test_runs (
    id           TEXT        PRIMARY KEY,          -- UUID string
    started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    triggered_by TEXT        NOT NULL DEFAULT 'manual',  -- 'manual' | 'n8n_daily'
    status       TEXT        NOT NULL DEFAULT 'running',  -- 'running' | 'completed' | 'error'
    total_tests  INT,
    passed       INT,
    failed       INT,
    skipped      INT,
    summary      JSONB,      -- { total, passed, failed, skipped }
    results      JSONB,      -- full array of TestResult objects
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_test_runs_started_at ON test_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_test_runs_status     ON test_runs(status);
