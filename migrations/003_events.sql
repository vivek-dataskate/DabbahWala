-- 003_events.sql
-- Raw event intake: every open, click, order, call, SMS lands here
-- This is the "Data Intake" layer of the pipeline

CREATE TABLE events (
    id          BIGSERIAL PRIMARY KEY,
    contact_id  BIGINT NOT NULL REFERENCES contacts(id),
    event_type  event_type NOT NULL,
    metadata    JSONB DEFAULT '{}',
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_events_contact_time ON events (contact_id, occurred_at DESC);
CREATE INDEX idx_events_type_time ON events (event_type, occurred_at DESC);
