-- 033_field_agent_sms.sql
-- Track SMS sent by field agents from personal phones against the customer record.
-- Adds source + agent_name columns to telnyx_messages, updates the store SP
-- and get_communication_history to expose them to the inference agents.

SET search_path TO dabbahwala;


-- ─── Schema changes ────────────────────────────────────────────────────────

ALTER TABLE telnyx_messages
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'telnyx_auto'
        CHECK (source IN ('telnyx_auto', 'field_agent', 'delivery_staff')),
    ADD COLUMN IF NOT EXISTS agent_name TEXT;

-- Backfill existing delivery-staff rows
UPDATE telnyx_messages SET source = 'delivery_staff' WHERE is_delivery_staff = true AND source = 'telnyx_auto';

-- Let field agent SMS be queried efficiently
CREATE INDEX IF NOT EXISTS idx_telnyx_msg_source ON telnyx_messages (source, sent_at DESC);


-- ─── Updated store_telnyx_message ─────────────────────────────────────────
-- Adds p_source and p_agent_name at the end so existing 9-arg calls still work.
-- Also accepts an optional p_sent_at so agents can log messages after the fact.

CREATE OR REPLACE FUNCTION store_telnyx_message(
    p_contact_email     TEXT,
    p_direction         TEXT,
    p_from_number       TEXT,
    p_to_number         TEXT,
    p_body              TEXT,
    p_telnyx_msg_id     TEXT         DEFAULT NULL,
    p_status            TEXT         DEFAULT 'sent',
    p_is_delivery_staff BOOLEAN      DEFAULT false,
    p_metadata          JSONB        DEFAULT '{}',
    p_source            TEXT         DEFAULT 'telnyx_auto',
    p_agent_name        TEXT         DEFAULT NULL,
    p_sent_at           TIMESTAMPTZ  DEFAULT NULL
)
RETURNS BIGINT
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_contact_id BIGINT;
    v_message_id BIGINT;
    v_event_type event_type;
    v_sent_at    TIMESTAMPTZ;
BEGIN
    SELECT id INTO v_contact_id FROM contacts WHERE email = p_contact_email;
    IF v_contact_id IS NULL THEN
        RAISE EXCEPTION 'Contact not found: %', p_contact_email;
    END IF;

    v_sent_at := COALESCE(p_sent_at, now());

    INSERT INTO telnyx_messages
        (contact_id, direction, from_number, to_number, body,
         telnyx_msg_id, status, is_delivery_staff, metadata,
         source, agent_name, sent_at)
    VALUES (v_contact_id, p_direction, p_from_number, p_to_number, p_body,
            p_telnyx_msg_id, p_status, p_is_delivery_staff, p_metadata,
            p_source, p_agent_name, v_sent_at)
    RETURNING id INTO v_message_id;

    v_event_type := CASE WHEN p_direction = 'inbound' THEN 'sms_received' ELSE 'sms_sent' END;

    INSERT INTO events (contact_id, event_type, metadata, occurred_at)
    VALUES (v_contact_id, v_event_type,
            jsonb_build_object(
                'source',      p_source,
                'agent_name',  COALESCE(p_agent_name, ''),
                'telnyx_msg_id', COALESCE(p_telnyx_msg_id, '')
            ),
            v_sent_at);

    RETURN v_message_id;
END;
$$;


-- ─── Updated get_communication_history ────────────────────────────────────
-- Now includes source + agent_name so inference agents know who sent each SMS.

CREATE OR REPLACE FUNCTION get_communication_history(p_contact_id BIGINT, p_days INT DEFAULT 30)
RETURNS JSONB
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_sms        JSONB;
    v_calls      JSONB;
    v_deliveries JSONB;
BEGIN
    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_sms
    FROM (
        SELECT id, direction, from_number, to_number, body, status,
               source, agent_name, is_delivery_staff, sent_at
        FROM telnyx_messages
        WHERE contact_id = p_contact_id
          AND sent_at > now() - (p_days || ' days')::interval
        ORDER BY sent_at DESC
    ) t;

    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_calls
    FROM (
        SELECT id, direction, from_number, to_number, duration_sec,
               transcript, summary, is_delivery_staff, started_at, ended_at
        FROM telnyx_calls
        WHERE contact_id = p_contact_id
          AND started_at > now() - (p_days || ' days')::interval
        ORDER BY started_at DESC
    ) t;

    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_deliveries
    FROM (
        SELECT id, order_ref, status, updated_by, notes, location, occurred_at
        FROM delivery_status
        WHERE contact_id = p_contact_id
          AND occurred_at > now() - (p_days || ' days')::interval
        ORDER BY occurred_at DESC
    ) t;

    RETURN jsonb_build_object(
        'contact_id',      p_contact_id,
        'days',            p_days,
        'sms_messages',    v_sms,
        'voice_calls',     v_calls,
        'delivery_updates', v_deliveries
    );
END;
$$;
