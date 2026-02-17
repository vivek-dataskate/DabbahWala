-- 011_fn_lifecycle_api.sql
-- Functions that FastAPI/n8n call to interact with the lifecycle system

SET search_path TO dabbahwala;

-- EVENT INGESTION
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
BEGIN
    SELECT id INTO v_contact_id FROM contacts WHERE email = p_contact_email;

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
               (SELECT lifecycle_segment FROM contacts WHERE id = v_contact_id),
               'optout',
               '{"reason": "direct_optout_event"}'::jsonb
        FROM rules r WHERE r.rule_name = 'optout'
        LIMIT 1;
    END IF;

    RETURN v_event_id;
END;
$$;


-- FULL LIFECYCLE CYCLE
CREATE OR REPLACE FUNCTION run_lifecycle_cycle()
RETURNS TABLE(contacts_updated INT, campaigns_queued INT)
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_updated INT;
    v_queued INT;
BEGIN
    PERFORM refresh_engagement_rollups();
    SELECT evaluate_rules() INTO v_updated;
    SELECT count(*) INTO v_queued FROM campaign_queue WHERE status = 'pending';
    RETURN QUERY SELECT v_updated, v_queued;
END;
$$;


-- GET PENDING CAMPAIGN MOVES
CREATE OR REPLACE FUNCTION get_pending_campaign_moves()
RETURNS TABLE(
    queue_id BIGINT,
    contact_email TEXT,
    contact_phone TEXT,
    from_campaign campaign_name,
    to_campaign campaign_name
)
LANGUAGE sql
SET search_path TO dabbahwala
AS $$
    SELECT cq.id, c.email, c.phone, cq.from_campaign, cq.to_campaign
    FROM campaign_queue cq
    JOIN contacts c ON c.id = cq.contact_id
    WHERE cq.status = 'pending'
    ORDER BY cq.created_at;
$$;


-- MARK CAMPAIGN EXECUTED
CREATE OR REPLACE FUNCTION mark_campaign_executed(p_queue_id BIGINT)
RETURNS void
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
BEGIN
    UPDATE campaign_queue
    SET status = 'executed', executed_at = now()
    WHERE id = p_queue_id;
END;
$$;


-- GET PENDING SMS
CREATE OR REPLACE FUNCTION get_pending_sms()
RETURNS TABLE(
    contact_id BIGINT,
    contact_email TEXT,
    phone TEXT,
    sms_level SMALLINT,
    lifecycle lifecycle_segment
)
LANGUAGE sql
SET search_path TO dabbahwala
AS $$
    SELECT c.id, c.email, c.phone, c.sms_level, c.lifecycle_segment
    FROM contacts c
    WHERE c.sms_promo_enabled = true
      AND c.lifecycle_segment NOT IN ('optout', 'cooling')
      AND c.phone IS NOT NULL
    ORDER BY c.sms_level DESC, c.updated_at;
$$;


-- MARK SMS SENT
CREATE OR REPLACE FUNCTION mark_sms_sent(p_contact_id BIGINT)
RETURNS void
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
BEGIN
    INSERT INTO events (contact_id, event_type, occurred_at)
    VALUES (p_contact_id, 'sms_sent', now());
END;
$$;
