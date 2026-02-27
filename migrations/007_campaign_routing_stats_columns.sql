-- 007_campaign_routing_stats_columns.sql
-- Add campaign performance stats columns to campaign_routing.
-- These were defined in 002_tables.sql but never applied to the live DB
-- because the table was created before these columns were added.

SET search_path TO dabbahwala;

ALTER TABLE campaign_routing ADD COLUMN IF NOT EXISTS emails_sent     INTEGER DEFAULT 0;
ALTER TABLE campaign_routing ADD COLUMN IF NOT EXISTS unique_opens    INTEGER DEFAULT 0;
ALTER TABLE campaign_routing ADD COLUMN IF NOT EXISTS opens           INTEGER DEFAULT 0;
ALTER TABLE campaign_routing ADD COLUMN IF NOT EXISTS replies         INTEGER DEFAULT 0;
ALTER TABLE campaign_routing ADD COLUMN IF NOT EXISTS clicks          INTEGER DEFAULT 0;
ALTER TABLE campaign_routing ADD COLUMN IF NOT EXISTS bounces         INTEGER DEFAULT 0;
ALTER TABLE campaign_routing ADD COLUMN IF NOT EXISTS unsubscribes    INTEGER DEFAULT 0;
ALTER TABLE campaign_routing ADD COLUMN IF NOT EXISTS open_rate       NUMERIC(5,2) DEFAULT 0;
ALTER TABLE campaign_routing ADD COLUMN IF NOT EXISTS reply_rate      NUMERIC(5,2) DEFAULT 0;
ALTER TABLE campaign_routing ADD COLUMN IF NOT EXISTS stats_synced_at TIMESTAMPTZ;
