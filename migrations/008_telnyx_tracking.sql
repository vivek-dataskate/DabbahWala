-- 008_telnyx_tracking.sql
-- SMS messages, voice calls with transcripts, and delivery status
-- All linked to contacts for agent analysis and opportunity detection

-- SMS messages (inbound and outbound, including delivery staff comms)
CREATE TABLE telnyx_messages (
    id              BIGSERIAL PRIMARY KEY,
    contact_id      BIGINT NOT NULL REFERENCES contacts(id),
    direction       TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    from_number     TEXT NOT NULL,
    to_number       TEXT NOT NULL,
    body            TEXT,
    telnyx_msg_id   TEXT,                       -- Telnyx message ID for tracking
    status          TEXT,                        -- sent, delivered, failed
    is_delivery_staff BOOLEAN DEFAULT false,     -- true if from delivery person
    metadata        JSONB DEFAULT '{}',
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_telnyx_msg_contact ON telnyx_messages (contact_id, sent_at DESC);
CREATE INDEX idx_telnyx_msg_delivery ON telnyx_messages (is_delivery_staff, sent_at DESC)
    WHERE is_delivery_staff = true;

-- Voice calls with transcripts
CREATE TABLE telnyx_calls (
    id                BIGSERIAL PRIMARY KEY,
    contact_id        BIGINT NOT NULL REFERENCES contacts(id),
    direction         TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    from_number       TEXT NOT NULL,
    to_number         TEXT NOT NULL,
    duration_sec      INT,
    recording_url     TEXT,
    transcript        TEXT,                       -- full call transcript for agent analysis
    summary           TEXT,                       -- agent-generated call summary
    is_delivery_staff BOOLEAN DEFAULT false,
    metadata          JSONB DEFAULT '{}',
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at          TIMESTAMPTZ
);

CREATE INDEX idx_telnyx_call_contact ON telnyx_calls (contact_id, started_at DESC);

-- Delivery status tracking
CREATE TABLE delivery_status (
    id            BIGSERIAL PRIMARY KEY,
    contact_id    BIGINT NOT NULL REFERENCES contacts(id),
    order_ref     TEXT,                           -- reference to the order
    status        delivery_status_type NOT NULL,
    updated_by    TEXT,                           -- delivery person name/ID
    notes         TEXT,
    location      TEXT,
    metadata      JSONB DEFAULT '{}',
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_delivery_contact ON delivery_status (contact_id, occurred_at DESC);
CREATE INDEX idx_delivery_order ON delivery_status (order_ref);
