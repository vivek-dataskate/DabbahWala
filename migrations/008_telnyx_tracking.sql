-- 008_telnyx_tracking.sql
-- SMS messages, voice calls with transcripts, and delivery status

SET search_path TO dabbahwala;

CREATE TABLE telnyx_messages (
    id              BIGSERIAL PRIMARY KEY,
    contact_id      BIGINT NOT NULL REFERENCES contacts(id),
    direction       TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    from_number     TEXT NOT NULL,
    to_number       TEXT NOT NULL,
    body            TEXT,
    telnyx_msg_id   TEXT,
    status          TEXT,
    is_delivery_staff BOOLEAN DEFAULT false,
    metadata        JSONB DEFAULT '{}',
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_telnyx_msg_contact ON telnyx_messages (contact_id, sent_at DESC);
CREATE INDEX idx_telnyx_msg_delivery ON telnyx_messages (is_delivery_staff, sent_at DESC)
    WHERE is_delivery_staff = true;

CREATE TABLE telnyx_calls (
    id                BIGSERIAL PRIMARY KEY,
    contact_id        BIGINT NOT NULL REFERENCES contacts(id),
    direction         TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    from_number       TEXT NOT NULL,
    to_number         TEXT NOT NULL,
    duration_sec      INT,
    recording_url     TEXT,
    transcript        TEXT,
    summary           TEXT,
    is_delivery_staff BOOLEAN DEFAULT false,
    metadata          JSONB DEFAULT '{}',
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at          TIMESTAMPTZ
);

CREATE INDEX idx_telnyx_call_contact ON telnyx_calls (contact_id, started_at DESC);

CREATE TABLE delivery_status (
    id            BIGSERIAL PRIMARY KEY,
    contact_id    BIGINT NOT NULL REFERENCES contacts(id),
    order_ref     TEXT,
    status        delivery_status_type NOT NULL,
    updated_by    TEXT,
    notes         TEXT,
    location      TEXT,
    metadata      JSONB DEFAULT '{}',
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_delivery_contact ON delivery_status (contact_id, occurred_at DESC);
CREATE INDEX idx_delivery_order ON delivery_status (order_ref);
