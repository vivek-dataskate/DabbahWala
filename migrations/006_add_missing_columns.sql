-- 006_add_missing_columns.sql
-- Add columns that were in 002_tables.sql CREATE TABLE definitions but were
-- never applied to the live DB because the table already existed.

SET search_path TO dabbahwala;

ALTER TABLE campaign_routing ADD COLUMN IF NOT EXISTS leads_count INTEGER DEFAULT 0;
