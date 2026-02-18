-- 018_fn_opportunities.sql
-- Stored functions for opportunity CRUD and the 4 detection signals.
-- Python layer calls these instead of raw SQL.

SET search_path TO dabbahwala;


-- CREATE OPPORTUNITY
CREATE OR REPLACE FUNCTION create_opportunity(
    p_contact_id BIGINT,
    p_action opportunity_action,
    p_priority TEXT,
    p_reason TEXT,
    p_suggested_message TEXT DEFAULT NULL,
    p_confidence_score NUMERIC(3,2) DEFAULT NULL
)
RETURNS BIGINT
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_id BIGINT;
BEGIN
    INSERT INTO opportunities
        (contact_id, action, priority, reason, suggested_message, confidence_score)
    VALUES (p_contact_id, p_action, p_priority, p_reason, p_suggested_message, p_confidence_score)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;


-- GET PENDING OPPORTUNITIES (for n8n dispatcher)
CREATE OR REPLACE FUNCTION get_pending_opportunities()
RETURNS TABLE(
    id BIGINT,
    contact_id BIGINT,
    action opportunity_action,
    priority TEXT,
    reason TEXT,
    suggested_message TEXT,
    confidence_score NUMERIC,
    email TEXT,
    phone TEXT,
    first_name TEXT,
    last_name TEXT,
    lifecycle_segment lifecycle_segment,
    total_orders INT,
    last_order_at TIMESTAMPTZ
)
LANGUAGE sql
SET search_path TO dabbahwala
AS $$
    SELECT o.id, o.contact_id, o.action, o.priority, o.reason,
           o.suggested_message, o.confidence_score,
           c.email, c.phone, c.first_name, c.last_name,
           c.lifecycle_segment, c.total_orders, c.last_order_at
    FROM opportunities o
    JOIN contacts c ON c.id = o.contact_id
    WHERE o.status = 'pending'
    ORDER BY
        CASE o.priority WHEN 'hot' THEN 1 WHEN 'warm' THEN 2 ELSE 3 END,
        o.created_at;
$$;


-- MARK OPPORTUNITY DISPATCHED
CREATE OR REPLACE FUNCTION mark_opportunity_dispatched(
    p_opportunity_id BIGINT,
    p_airtable_record_id TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
BEGIN
    UPDATE opportunities
    SET status = 'dispatched', airtable_record_id = p_airtable_record_id, dispatched_at = now()
    WHERE id = p_opportunity_id AND status = 'pending';
    RETURN FOUND;
END;
$$;


-- UPDATE OPPORTUNITY OUTCOME
CREATE OR REPLACE FUNCTION update_opportunity_outcome(
    p_opportunity_id BIGINT,
    p_status opportunity_status,
    p_outcome TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
BEGIN
    UPDATE opportunities
    SET status = p_status, outcome = p_outcome, completed_at = now()
    WHERE id = p_opportunity_id;
    RETURN FOUND;
END;
$$;


-----------------------------------------------------------------------
-- DETECTION SIGNALS — Each returns a table of opportunity candidates
-----------------------------------------------------------------------

-- Signal 1: Multiple opens, no order in 7 days, no pending opportunity
CREATE OR REPLACE FUNCTION detect_engaged_no_order()
RETURNS TABLE(
    id BIGINT, email TEXT, first_name TEXT, last_name TEXT, phone TEXT,
    lifecycle_segment lifecycle_segment, total_orders INT, last_order_at TIMESTAMPTZ,
    opens_7d INT, clicks_7d INT
)
LANGUAGE sql
SET search_path TO dabbahwala
AS $$
    SELECT c.id, c.email, c.first_name, c.last_name, c.phone,
           c.lifecycle_segment, c.total_orders, c.last_order_at,
           r.opens_7d, r.clicks_7d
    FROM contacts c
    JOIN engagement_rollups r ON r.contact_id = c.id
    WHERE r.opens_7d >= 2
      AND r.orders_7d = 0
      AND c.lifecycle_segment NOT IN ('optout', 'cooling', 'new_customer')
      AND NOT EXISTS (
          SELECT 1 FROM opportunities o
          WHERE o.contact_id = c.id AND o.status IN ('pending', 'dispatched')
      );
$$;


-- Signal 2: New customer, 1 order, no repeat after 5 days
CREATE OR REPLACE FUNCTION detect_new_customer_no_repeat()
RETURNS TABLE(
    id BIGINT, email TEXT, first_name TEXT, last_name TEXT, phone TEXT,
    lifecycle_segment lifecycle_segment, total_orders INT, last_order_at TIMESTAMPTZ
)
LANGUAGE sql
SET search_path TO dabbahwala
AS $$
    SELECT c.id, c.email, c.first_name, c.last_name, c.phone,
           c.lifecycle_segment, c.total_orders, c.last_order_at
    FROM contacts c
    WHERE c.lifecycle_segment = 'new_customer'
      AND c.total_orders = 1
      AND c.last_order_at < now() - interval '5 days'
      AND NOT EXISTS (
          SELECT 1 FROM opportunities o
          WHERE o.contact_id = c.id AND o.status IN ('pending', 'dispatched')
      );
$$;


-- Signal 3: Lapsed customer recently transitioned to engaged
CREATE OR REPLACE FUNCTION detect_lapsed_reengaged()
RETURNS TABLE(
    id BIGINT, email TEXT, first_name TEXT, last_name TEXT, phone TEXT,
    lifecycle_segment lifecycle_segment, total_orders INT, last_order_at TIMESTAMPTZ
)
LANGUAGE sql
SET search_path TO dabbahwala
AS $$
    SELECT DISTINCT c.id, c.email, c.first_name, c.last_name, c.phone,
           c.lifecycle_segment, c.total_orders, c.last_order_at
    FROM contacts c
    JOIN decision_log dl ON dl.contact_id = c.id
    WHERE dl.prev_lifecycle IN ('lapsed_customer', 'reactivation_candidate')
      AND dl.new_lifecycle = 'engaged'
      AND dl.decided_at > now() - interval '3 days'
      AND NOT EXISTS (
          SELECT 1 FROM opportunities o
          WHERE o.contact_id = c.id AND o.status IN ('pending', 'dispatched')
      );
$$;


-- Signal 4: Reorder intent in call transcripts (keyword-based; see 022 for vector upgrade)
CREATE OR REPLACE FUNCTION detect_reorder_intent()
RETURNS TABLE(
    id BIGINT, email TEXT, first_name TEXT, last_name TEXT, phone TEXT,
    lifecycle_segment lifecycle_segment, total_orders INT, last_order_at TIMESTAMPTZ,
    transcript TEXT
)
LANGUAGE sql
SET search_path TO dabbahwala
AS $$
    SELECT DISTINCT c.id, c.email, c.first_name, c.last_name, c.phone,
           c.lifecycle_segment, c.total_orders, c.last_order_at,
           tc.transcript
    FROM contacts c
    JOIN telnyx_calls tc ON tc.contact_id = c.id
    WHERE tc.started_at > now() - interval '3 days'
      AND tc.transcript IS NOT NULL
      AND (tc.transcript ILIKE '%reorder%' OR tc.transcript ILIKE '%order again%'
           OR tc.transcript ILIKE '%next delivery%' OR tc.transcript ILIKE '%loved it%')
      AND NOT EXISTS (
          SELECT 1 FROM opportunities o
          WHERE o.contact_id = c.id AND o.status IN ('pending', 'dispatched')
      );
$$;


-- OPPORTUNITY OUTCOMES (for reviewing conversion rates)
CREATE OR REPLACE FUNCTION get_opportunity_outcomes(p_days INT DEFAULT 30)
RETURNS JSONB
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_summary JSONB;
    v_rates JSONB;
BEGIN
    SELECT jsonb_agg(row_to_json(t))
    INTO v_summary
    FROM (
        SELECT action::text, priority, outcome, count(*) AS count
        FROM opportunities
        WHERE completed_at > now() - (p_days || ' days')::interval
          AND outcome IS NOT NULL
        GROUP BY action, priority, outcome
        ORDER BY count DESC
    ) t;

    SELECT row_to_json(t)::jsonb
    INTO v_rates
    FROM (
        SELECT
            count(*) AS total,
            count(*) FILTER (WHERE outcome = 'ordered') AS converted,
            count(*) FILTER (WHERE outcome = 'not_interested') AS not_interested,
            count(*) FILTER (WHERE outcome = 'no_answer') AS no_answer
        FROM opportunities
        WHERE completed_at > now() - (p_days || ' days')::interval
    ) t;

    RETURN jsonb_build_object(
        'days', p_days,
        'outcome_summary', COALESCE(v_summary, '[]'::jsonb),
        'rates', COALESCE(v_rates, '{}'::jsonb)
    );
END;
$$;


-- HIGH INTENT SIGNALS for a specific contact
CREATE OR REPLACE FUNCTION get_high_intent_signals(p_contact_id BIGINT)
RETURNS JSONB
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_contact JSONB;
    v_sms JSONB;
    v_calls JSONB;
    v_deliveries JSONB;
BEGIN
    SELECT row_to_json(t)::jsonb INTO v_contact
    FROM (
        SELECT c.*, r.opens_7d, r.clicks_7d, r.sms_sent_7d, r.sms_clicks_7d, r.orders_7d
        FROM contacts c
        LEFT JOIN engagement_rollups r ON r.contact_id = c.id
        WHERE c.id = p_contact_id
    ) t;

    IF v_contact IS NULL THEN
        RETURN jsonb_build_object('error', 'Contact not found: ' || p_contact_id);
    END IF;

    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_sms
    FROM (
        SELECT direction, body, is_delivery_staff, sent_at
        FROM telnyx_messages WHERE contact_id = p_contact_id
        ORDER BY sent_at DESC LIMIT 10
    ) t;

    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_calls
    FROM (
        SELECT transcript, summary, is_delivery_staff, duration_sec, started_at
        FROM telnyx_calls
        WHERE contact_id = p_contact_id AND transcript IS NOT NULL
        ORDER BY started_at DESC LIMIT 5
    ) t;

    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_deliveries
    FROM (
        SELECT status, notes, updated_by, occurred_at
        FROM delivery_status WHERE contact_id = p_contact_id
        ORDER BY occurred_at DESC LIMIT 10
    ) t;

    RETURN jsonb_build_object(
        'contact', v_contact,
        'recent_sms', v_sms,
        'call_transcripts', v_calls,
        'delivery_notes', v_deliveries
    );
END;
$$;
