-- 016_opportunities.sql
-- Agent-predicted opportunities for high-intent outreach
-- Synced to Airtable for field sales team visibility

CREATE TABLE opportunities (
    id                  BIGSERIAL PRIMARY KEY,
    contact_id          BIGINT NOT NULL REFERENCES contacts(id),
    action              opportunity_action NOT NULL,
    priority            TEXT NOT NULL CHECK (priority IN ('hot', 'warm', 'cold')),
    reason              TEXT NOT NULL,                -- agent's explanation
    suggested_message   TEXT,                         -- SMS text or call talking points
    confidence_score    NUMERIC(3,2),                 -- agent confidence 0.00-1.00
    status              opportunity_status NOT NULL DEFAULT 'pending',
    airtable_record_id  TEXT,                         -- Airtable record ID once synced
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    dispatched_at       TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    outcome             TEXT                          -- ordered, not_interested, no_answer, callback
);

CREATE INDEX idx_opportunities_pending ON opportunities (status) WHERE status = 'pending';
CREATE INDEX idx_opportunities_contact ON opportunities (contact_id, created_at DESC);
