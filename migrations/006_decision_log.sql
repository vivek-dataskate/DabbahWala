-- 006_decision_log.sql
-- Audit trail: every state transition recorded

SET search_path TO dabbahwala;

CREATE TABLE decision_log (
    id              BIGSERIAL PRIMARY KEY,
    contact_id      BIGINT NOT NULL REFERENCES contacts(id),
    rule_id         INT REFERENCES rules(id),
    prev_lifecycle  lifecycle_segment,
    new_lifecycle   lifecycle_segment,
    changes_applied JSONB NOT NULL,
    decided_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_decision_log_contact ON decision_log (contact_id, decided_at DESC);
CREATE INDEX idx_decision_log_time ON decision_log (decided_at DESC);
