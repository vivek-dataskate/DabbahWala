-- 020_fn_contacts_comms.sql
-- Stored functions for contact lookup, search, and communication history.
-- Python layer calls these instead of raw SQL.

SET search_path TO dabbahwala;


-- CONTACT DETAIL — returns full profile as JSONB (contact + events + decisions + rollups)
CREATE OR REPLACE FUNCTION get_contact_detail(p_email_or_id TEXT)
RETURNS JSONB
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_contact_id BIGINT;
    v_contact JSONB;
    v_events JSONB;
    v_decisions JSONB;
    v_rollups JSONB;
BEGIN
    -- Resolve contact
    IF p_email_or_id ~ '^\d+$' THEN
        SELECT id INTO v_contact_id FROM contacts WHERE id = p_email_or_id::bigint;
    ELSE
        SELECT id INTO v_contact_id FROM contacts WHERE email = p_email_or_id;
    END IF;

    IF v_contact_id IS NULL THEN
        RETURN jsonb_build_object('error', 'Contact not found: ' || p_email_or_id);
    END IF;

    SELECT row_to_json(c)::jsonb INTO v_contact FROM contacts c WHERE c.id = v_contact_id;

    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_events
    FROM (
        SELECT event_type, occurred_at, metadata
        FROM events WHERE contact_id = v_contact_id AND occurred_at > now() - interval '30 days'
        ORDER BY occurred_at DESC LIMIT 20
    ) t;

    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_decisions
    FROM (
        SELECT r.rule_name, dl.prev_lifecycle, dl.new_lifecycle, dl.changes_applied, dl.decided_at
        FROM decision_log dl
        LEFT JOIN rules r ON r.id = dl.rule_id
        WHERE dl.contact_id = v_contact_id
        ORDER BY dl.decided_at DESC LIMIT 10
    ) t;

    SELECT COALESCE(row_to_json(er)::jsonb, '{}'::jsonb) INTO v_rollups
    FROM engagement_rollups er WHERE er.contact_id = v_contact_id;

    RETURN jsonb_build_object(
        'contact', v_contact,
        'engagement_rollups', COALESCE(v_rollups, '{}'::jsonb),
        'recent_events', v_events,
        'recent_decisions', v_decisions
    );
END;
$$;


-- SEARCH CONTACTS with dynamic filters
CREATE OR REPLACE FUNCTION search_contacts(
    p_lifecycle_segment TEXT DEFAULT NULL,
    p_email_promo_enabled BOOLEAN DEFAULT NULL,
    p_sms_promo_enabled BOOLEAN DEFAULT NULL,
    p_min_orders INT DEFAULT NULL,
    p_max_orders INT DEFAULT NULL,
    p_limit INT DEFAULT 50
)
RETURNS TABLE(
    id BIGINT, email TEXT, first_name TEXT, last_name TEXT,
    lifecycle_segment lifecycle_segment, email_promo_enabled BOOLEAN,
    sms_promo_enabled BOOLEAN, sms_level SMALLINT,
    current_campaign campaign_name, total_orders INT, last_order_at TIMESTAMPTZ
)
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
BEGIN
    RETURN QUERY
    SELECT c.id, c.email, c.first_name, c.last_name,
           c.lifecycle_segment, c.email_promo_enabled,
           c.sms_promo_enabled, c.sms_level,
           c.current_campaign, c.total_orders, c.last_order_at
    FROM contacts c
    WHERE (p_lifecycle_segment IS NULL OR c.lifecycle_segment = p_lifecycle_segment::lifecycle_segment)
      AND (p_email_promo_enabled IS NULL OR c.email_promo_enabled = p_email_promo_enabled)
      AND (p_sms_promo_enabled IS NULL OR c.sms_promo_enabled = p_sms_promo_enabled)
      AND (p_min_orders IS NULL OR c.total_orders >= p_min_orders)
      AND (p_max_orders IS NULL OR c.total_orders <= p_max_orders)
    ORDER BY c.updated_at DESC
    LIMIT p_limit;
END;
$$;


-- COMMUNICATION HISTORY — SMS + calls + delivery as JSONB
CREATE OR REPLACE FUNCTION get_communication_history(p_contact_id BIGINT, p_days INT DEFAULT 30)
RETURNS JSONB
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_sms JSONB;
    v_calls JSONB;
    v_deliveries JSONB;
BEGIN
    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_sms
    FROM (
        SELECT id, direction, from_number, to_number, body, status,
               is_delivery_staff, sent_at
        FROM telnyx_messages
        WHERE contact_id = p_contact_id AND sent_at > now() - (p_days || ' days')::interval
        ORDER BY sent_at DESC
    ) t;

    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_calls
    FROM (
        SELECT id, direction, from_number, to_number, duration_sec,
               transcript, summary, is_delivery_staff, started_at, ended_at
        FROM telnyx_calls
        WHERE contact_id = p_contact_id AND started_at > now() - (p_days || ' days')::interval
        ORDER BY started_at DESC
    ) t;

    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_deliveries
    FROM (
        SELECT id, order_ref, status, updated_by, notes, location, occurred_at
        FROM delivery_status
        WHERE contact_id = p_contact_id AND occurred_at > now() - (p_days || ' days')::interval
        ORDER BY occurred_at DESC
    ) t;

    RETURN jsonb_build_object(
        'contact_id', p_contact_id,
        'days', p_days,
        'sms_messages', v_sms,
        'voice_calls', v_calls,
        'delivery_updates', v_deliveries
    );
END;
$$;


-- DELIVERY TRACKING (all-time, limited to 50)
CREATE OR REPLACE FUNCTION get_delivery_tracking(p_contact_id BIGINT)
RETURNS TABLE(
    id BIGINT, order_ref TEXT, status delivery_status_type,
    updated_by TEXT, notes TEXT, location TEXT, occurred_at TIMESTAMPTZ
)
LANGUAGE sql
SET search_path TO dabbahwala
AS $$
    SELECT ds.id, ds.order_ref, ds.status, ds.updated_by,
           ds.notes, ds.location, ds.occurred_at
    FROM delivery_status ds
    WHERE ds.contact_id = p_contact_id
    ORDER BY ds.occurred_at DESC
    LIMIT 50;
$$;
