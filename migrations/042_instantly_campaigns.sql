-- 042_instantly_campaigns.sql
-- Registry of Instantly campaigns associated with DabbahWala.
-- Populated automatically from hardcoded IDs + tag discovery + webhook events.

SET search_path TO dabbahwala;

CREATE TABLE IF NOT EXISTS instantly_campaigns (
    id              BIGSERIAL PRIMARY KEY,
    campaign_id     TEXT UNIQUE NOT NULL,
    campaign_name   TEXT,
    tags            TEXT[],
    status          TEXT,           -- 'active', 'paused', 'completed', etc. (from Instantly API)
    source          TEXT NOT NULL DEFAULT 'hardcoded',  -- 'hardcoded', 'tag_discovered', 'webhook'
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_instantly_campaigns_id ON instantly_campaigns (campaign_id);
