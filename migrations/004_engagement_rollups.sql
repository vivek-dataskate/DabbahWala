-- 004_engagement_rollups.sql
-- Pre-computed 7-day rolling metrics per contact
-- This is the "Evidence" layer: raw events aggregated into actionable metrics
-- Refreshed before each rule evaluation cycle

CREATE TABLE engagement_rollups (
    contact_id    BIGINT PRIMARY KEY REFERENCES contacts(id),
    opens_7d      INT NOT NULL DEFAULT 0,
    clicks_7d     INT NOT NULL DEFAULT 0,
    sms_sent_7d   INT NOT NULL DEFAULT 0,
    sms_clicks_7d INT NOT NULL DEFAULT 0,
    orders_7d     INT NOT NULL DEFAULT 0,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
