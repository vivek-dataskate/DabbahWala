-- 007_campaign_queue.sql
-- Pending campaign moves for n8n to execute on Instantly

SET search_path TO dabbahwala;

CREATE TABLE campaign_queue (
    id            BIGSERIAL PRIMARY KEY,
    contact_id    BIGINT NOT NULL REFERENCES contacts(id),
    from_campaign campaign_name,
    to_campaign   campaign_name NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'executed', 'failed')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    executed_at   TIMESTAMPTZ
);

CREATE INDEX idx_campaign_queue_pending ON campaign_queue (status) WHERE status = 'pending';
