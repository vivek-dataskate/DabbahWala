-- 043_campaign_stats.sql
-- Add performance tracking columns to instantly_campaigns.
-- Populated every 2 hours by n8n → Instantly analytics API → Render.

SET search_path TO dabbahwala;

ALTER TABLE instantly_campaigns
  ADD COLUMN IF NOT EXISTS leads_count    INTEGER,
  ADD COLUMN IF NOT EXISTS emails_sent    INTEGER,
  ADD COLUMN IF NOT EXISTS unique_opens   INTEGER,
  ADD COLUMN IF NOT EXISTS opens          INTEGER,
  ADD COLUMN IF NOT EXISTS replies        INTEGER,
  ADD COLUMN IF NOT EXISTS clicks         INTEGER,
  ADD COLUMN IF NOT EXISTS bounces        INTEGER,
  ADD COLUMN IF NOT EXISTS unsubscribes   INTEGER,
  ADD COLUMN IF NOT EXISTS open_rate      NUMERIC(5,2),
  ADD COLUMN IF NOT EXISTS reply_rate     NUMERIC(5,2),
  ADD COLUMN IF NOT EXISTS stats_synced_at TIMESTAMPTZ;
