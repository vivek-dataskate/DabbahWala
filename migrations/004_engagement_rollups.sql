-- 004_engagement_rollups.sql
-- Pre-computed 7-day rolling metrics per contact

SET search_path TO dabbahwala;

CREATE TABLE engagement_rollups (
    contact_id    BIGINT PRIMARY KEY REFERENCES contacts(id),
    opens_7d      INT NOT NULL DEFAULT 0,
    clicks_7d     INT NOT NULL DEFAULT 0,
    sms_sent_7d   INT NOT NULL DEFAULT 0,
    sms_clicks_7d INT NOT NULL DEFAULT 0,
    orders_7d     INT NOT NULL DEFAULT 0,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
