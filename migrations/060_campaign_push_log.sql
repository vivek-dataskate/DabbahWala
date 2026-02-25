-- 060_campaign_push_log.sql
-- Log every Instantly lead-push attempt so we can diagnose silent failures.
-- n8n calls POST /api/campaigns/log-push after each Add Lead to New Campaign node.

SET search_path TO dabbahwala;

CREATE TABLE IF NOT EXISTS campaign_push_log (
    id             BIGSERIAL PRIMARY KEY,
    queue_id       BIGINT,
    email          TEXT,
    to_campaign    TEXT,
    success        BOOLEAN NOT NULL,
    status_code    INT,
    error_message  TEXT,
    response_body  TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_campaign_push_log_queue_id   ON campaign_push_log (queue_id);
CREATE INDEX IF NOT EXISTS idx_campaign_push_log_success    ON campaign_push_log (success);
CREATE INDEX IF NOT EXISTS idx_campaign_push_log_created_at ON campaign_push_log (created_at DESC);
