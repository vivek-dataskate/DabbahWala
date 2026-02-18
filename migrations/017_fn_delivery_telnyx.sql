-- 017_fn_delivery_telnyx.sql
-- Stored functions for delivery status and Telnyx message/call storage.
-- Python layer calls these instead of raw SQL.

SET search_path TO dabbahwala;


-- DELIVERY STATUS UPDATE
-- Resolves contact by email, inserts delivery_status + delivery_update event atomically.
CREATE OR REPLACE FUNCTION update_delivery_status(
    p_contact_email TEXT,
    p_order_ref TEXT,
    p_status delivery_status_type,
    p_updated_by TEXT,
    p_notes TEXT DEFAULT NULL,
    p_location TEXT DEFAULT NULL,
    p_metadata JSONB DEFAULT '{}'
)
RETURNS BIGINT
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_contact_id BIGINT;
    v_delivery_id BIGINT;
BEGIN
    SELECT id INTO v_contact_id FROM contacts WHERE email = p_contact_email;
    IF v_contact_id IS NULL THEN
        RAISE EXCEPTION 'Contact not found: %', p_contact_email;
    END IF;

    INSERT INTO delivery_status (contact_id, order_ref, status, updated_by, notes, location, metadata)
    VALUES (v_contact_id, p_order_ref, p_status, p_updated_by, p_notes, p_location, p_metadata)
    RETURNING id INTO v_delivery_id;

    INSERT INTO events (contact_id, event_type, metadata)
    VALUES (v_contact_id, 'delivery_update',
            jsonb_build_object('delivery_status', p_status::text, 'order_ref', p_order_ref));

    RETURN v_delivery_id;
END;
$$;


-- STORE TELNYX MESSAGE
-- Resolves contact by email, inserts message + sms_sent/sms_received event.
CREATE OR REPLACE FUNCTION store_telnyx_message(
    p_contact_email TEXT,
    p_direction TEXT,
    p_from_number TEXT,
    p_to_number TEXT,
    p_body TEXT,
    p_telnyx_msg_id TEXT DEFAULT NULL,
    p_status TEXT DEFAULT 'sent',
    p_is_delivery_staff BOOLEAN DEFAULT false,
    p_metadata JSONB DEFAULT '{}'
)
RETURNS BIGINT
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_contact_id BIGINT;
    v_message_id BIGINT;
    v_event_type event_type;
BEGIN
    SELECT id INTO v_contact_id FROM contacts WHERE email = p_contact_email;
    IF v_contact_id IS NULL THEN
        RAISE EXCEPTION 'Contact not found: %', p_contact_email;
    END IF;

    INSERT INTO telnyx_messages
        (contact_id, direction, from_number, to_number, body,
         telnyx_msg_id, status, is_delivery_staff, metadata)
    VALUES (v_contact_id, p_direction, p_from_number, p_to_number, p_body,
            p_telnyx_msg_id, p_status, p_is_delivery_staff, p_metadata)
    RETURNING id INTO v_message_id;

    v_event_type := CASE WHEN p_direction = 'inbound' THEN 'sms_received' ELSE 'sms_sent' END;

    INSERT INTO events (contact_id, event_type, metadata)
    VALUES (v_contact_id, v_event_type,
            jsonb_build_object('telnyx_msg_id', COALESCE(p_telnyx_msg_id, '')));

    RETURN v_message_id;
END;
$$;


-- STORE TELNYX CALL
-- Resolves contact by email, inserts call record + call_completed event.
CREATE OR REPLACE FUNCTION store_telnyx_call(
    p_contact_email TEXT,
    p_direction TEXT,
    p_from_number TEXT,
    p_to_number TEXT,
    p_duration_sec INT DEFAULT 0,
    p_recording_url TEXT DEFAULT NULL,
    p_transcript TEXT DEFAULT NULL,
    p_summary TEXT DEFAULT NULL,
    p_is_delivery_staff BOOLEAN DEFAULT false,
    p_metadata JSONB DEFAULT '{}',
    p_started_at TIMESTAMPTZ DEFAULT now(),
    p_ended_at TIMESTAMPTZ DEFAULT NULL
)
RETURNS BIGINT
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_contact_id BIGINT;
    v_call_id BIGINT;
BEGIN
    SELECT id INTO v_contact_id FROM contacts WHERE email = p_contact_email;
    IF v_contact_id IS NULL THEN
        RAISE EXCEPTION 'Contact not found: %', p_contact_email;
    END IF;

    INSERT INTO telnyx_calls
        (contact_id, direction, from_number, to_number, duration_sec,
         recording_url, transcript, summary, is_delivery_staff, metadata,
         started_at, ended_at)
    VALUES (v_contact_id, p_direction, p_from_number, p_to_number, p_duration_sec,
            p_recording_url, p_transcript, p_summary, p_is_delivery_staff, p_metadata,
            p_started_at, p_ended_at)
    RETURNING id INTO v_call_id;

    INSERT INTO events (contact_id, event_type, metadata)
    VALUES (v_contact_id, 'call_completed',
            jsonb_build_object('call_id', v_call_id, 'duration_sec', p_duration_sec));

    RETURN v_call_id;
END;
$$;
