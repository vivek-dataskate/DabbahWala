-- 011_fn_lifecycle_api.sql
-- Functions that FastAPI/n8n call to interact with the lifecycle system
-- This is the API surface between the outside world and Postgres

-- EVENT INGESTION: called when webhooks arrive
CREATE OR REPLACE FUNCTION ingest_event(
    p_contact_email TEXT,
    p_event_type event_type,
    p_metadata JSONB DEFAULT '{}'
)
RETURNS BIGINT
LANGUAGE plpgsql
AS $$
DECLARE
    v_contact_id BIGINT;
    v_event_id BIGINT;
BEGIN
    -- Find contact by email
    SELECT id INTO v_contact_id FROM contacts WHERE email = p_contact_email;

    IF v_contact_id IS NULL THEN
        RAISE EXCEPTION 'Contact not found: %', p_contact_email;
    END IF;

    -- Insert the event
    INSERT INTO events (contact_id, event_type, metadata, occurred_at)
    VALUES (v_contact_id, p_event_type, p_metadata, now())
    RETURNING id INTO v_event_id;

    -- Update denormalized order fields if this is an order event
    IF p_event_type = 'order_placed' THEN
        UPDATE contacts SET
            total_orders = total_orders + 1,
            last_order_at = now(),
            updated_at = now()
        WHERE id = v_contact_id;
    END IF;

    -- Handle optout events immediately
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

        -- Log the optout decision
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


-- FULL LIFECYCLE CYCLE: refresh rollups then evaluate rules
CREATE OR REPLACE FUNCTION run_lifecycle_cycle()
RETURNS TABLE(contacts_updated INT, campaigns_queued INT)
LANGUAGE plpgsql
AS $$
DECLARE
    v_updated INT;
    v_queued INT;
BEGIN
    -- Step 1: Refresh evidence
    PERFORM refresh_engagement_rollups();

    -- Step 2: Evaluate rules (inference → decision)
    SELECT evaluate_rules() INTO v_updated;

    -- Step 3: Count pending campaign moves
    SELECT count(*) INTO v_queued FROM campaign_queue WHERE status = 'pending';

    RETURN QUERY SELECT v_updated, v_queued;
END;
$$;


-- GET PENDING CAMPAIGN MOVES: n8n reads these to execute on Instantly
CREATE OR REPLACE FUNCTION get_pending_campaign_moves()
RETURNS TABLE(
    queue_id BIGINT,
    contact_email TEXT,
    contact_phone TEXT,
    from_campaign campaign_name,
    to_campaign campaign_name
)
LANGUAGE sql
AS $$
    SELECT cq.id, c.email, c.phone, cq.from_campaign, cq.to_campaign
    FROM campaign_queue cq
    JOIN contacts c ON c.id = cq.contact_id
    WHERE cq.status = 'pending'
    ORDER BY cq.created_at;
$$;


-- MARK CAMPAIGN EXECUTED: n8n confirms move was done in Instantly
CREATE OR REPLACE FUNCTION mark_campaign_executed(p_queue_id BIGINT)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE campaign_queue
    SET status = 'executed', executed_at = now()
    WHERE id = p_queue_id;
END;
$$;


-- GET PENDING SMS: contacts needing SMS based on flags + sms_level
CREATE OR REPLACE FUNCTION get_pending_sms()
RETURNS TABLE(
    contact_id BIGINT,
    contact_email TEXT,
    phone TEXT,
    sms_level SMALLINT,
    lifecycle lifecycle_segment
)
LANGUAGE sql
AS $$
    SELECT c.id, c.email, c.phone, c.sms_level, c.lifecycle_segment
    FROM contacts c
    WHERE c.sms_promo_enabled = true
      AND c.lifecycle_segment NOT IN ('optout', 'cooling')
      AND c.phone IS NOT NULL
    ORDER BY c.sms_level DESC, c.updated_at;
$$;


-- MARK SMS SENT: n8n confirms SMS was sent via Telnyx
CREATE OR REPLACE FUNCTION mark_sms_sent(p_contact_id BIGINT)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    -- Insert sms_sent event for rollup tracking
    INSERT INTO events (contact_id, event_type, occurred_at)
    VALUES (p_contact_id, 'sms_sent', now());
END;
$$;
