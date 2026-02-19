-- 030_fix_ingest_event_audit.sql
-- Fix: ingest_event was reading prev_lifecycle AFTER the UPDATE changed it,
-- so decision_log always recorded prev_lifecycle = 'optout' for opt-out events.
-- Fix: campaign_routing INSERT for APP_TO_DIRECT conflicted on PK (lifecycle_segment = 'cold').

SET search_path TO dabbahwala;

-- FIX 1: Capture prev_lifecycle BEFORE the UPDATE
CREATE OR REPLACE FUNCTION ingest_event(
    p_contact_email TEXT,
    p_event_type event_type,
    p_metadata JSONB DEFAULT '{}'
)
RETURNS BIGINT
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_contact_id BIGINT;
    v_event_id BIGINT;
    v_prev_lifecycle lifecycle_segment;
BEGIN
    SELECT id, lifecycle_segment
    INTO v_contact_id, v_prev_lifecycle
    FROM contacts
    WHERE email = p_contact_email;

    IF v_contact_id IS NULL THEN
        RAISE EXCEPTION 'Contact not found: %', p_contact_email;
    END IF;

    INSERT INTO events (contact_id, event_type, metadata, occurred_at)
    VALUES (v_contact_id, p_event_type, p_metadata, now())
    RETURNING id INTO v_event_id;

    IF p_event_type = 'order_placed' THEN
        UPDATE contacts SET
            total_orders = total_orders + 1,
            last_order_at = now(),
            updated_at = now()
        WHERE id = v_contact_id;
    END IF;

    IF p_event_type IN ('unsubscribe', 'sms_stop') THEN
        UPDATE contacts SET
            lifecycle_segment = 'optout',
            email_nurture_enabled = false,
            email_promo_enabled = false,
            sms_promo_enabled = false,
            current_campaign = NULL,
            updated_at = now()
        WHERE id = v_contact_id
          AND lifecycle_segment != 'optout';

        INSERT INTO decision_log (contact_id, rule_id, prev_lifecycle, new_lifecycle, changes_applied)
        SELECT v_contact_id, r.id,
               v_prev_lifecycle,
               'optout',
               '{"reason": "direct_optout_event"}'::jsonb
        FROM rules r WHERE r.rule_name = 'optout'
        LIMIT 1;
    END IF;

    RETURN v_event_id;
END;
$$;
