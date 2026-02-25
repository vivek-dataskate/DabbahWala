-- Migration 055: Add airtable_record_id to weekly_menu_schedule
-- Allows bidirectional sync between Airtable and Postgres
ALTER TABLE weekly_menu_schedule
    ADD COLUMN IF NOT EXISTS airtable_record_id TEXT,
    ADD COLUMN IF NOT EXISTS active              BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS price               NUMERIC(8,2);

CREATE UNIQUE INDEX IF NOT EXISTS idx_weekly_menu_airtable_id
    ON weekly_menu_schedule (airtable_record_id)
    WHERE airtable_record_id IS NOT NULL;
