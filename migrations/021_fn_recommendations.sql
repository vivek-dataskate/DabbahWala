-- 021_fn_recommendations.sql
-- Stored functions for reactivation targeting and content strategy.
-- Python layer calls these instead of raw SQL.

SET search_path TO dabbahwala;


-- REACTIVATION TARGETS — scored by engagement + order history
CREATE OR REPLACE FUNCTION suggest_reactivation_targets(p_limit INT DEFAULT 20)
RETURNS TABLE(
    id BIGINT, email TEXT, first_name TEXT, last_name TEXT, phone TEXT,
    lifecycle_segment lifecycle_segment, total_orders INT, last_order_at TIMESTAMPTZ,
    sms_level SMALLINT, current_campaign campaign_name,
    opens_7d INT, clicks_7d INT, sms_clicks_7d INT,
    reactivation_score NUMERIC
)
LANGUAGE sql
SET search_path TO dabbahwala
AS $$
    SELECT c.id, c.email, c.first_name, c.last_name, c.phone,
           c.lifecycle_segment, c.total_orders, c.last_order_at,
           c.sms_level, c.current_campaign,
           r.opens_7d, r.clicks_7d, r.sms_clicks_7d,
           (COALESCE(r.opens_7d, 0) * 1 + COALESCE(r.clicks_7d, 0) * 3
            + COALESCE(r.sms_clicks_7d, 0) * 2 + c.total_orders * 2)::numeric AS reactivation_score
    FROM contacts c
    LEFT JOIN engagement_rollups r ON r.contact_id = c.id
    WHERE c.lifecycle_segment IN ('lapsed_customer', 'reactivation_candidate')
      AND c.lifecycle_segment != 'optout'
    ORDER BY reactivation_score DESC, c.last_order_at DESC
    LIMIT p_limit;
$$;


-- CONTENT STRATEGY DATA — full contact profile for agent analysis
CREATE OR REPLACE FUNCTION get_content_strategy_data(p_contact_id BIGINT)
RETURNS JSONB
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_contact JSONB;
    v_rollups JSONB;
    v_event_summary JSONB;
    v_transcripts JSONB;
    v_delivery_notes JSONB;
    v_past_outcomes JSONB;
BEGIN
    SELECT row_to_json(c)::jsonb INTO v_contact FROM contacts c WHERE c.id = p_contact_id;
    IF v_contact IS NULL THEN
        RETURN jsonb_build_object('error', 'Contact not found: ' || p_contact_id);
    END IF;

    SELECT COALESCE(row_to_json(er)::jsonb, '{}'::jsonb) INTO v_rollups
    FROM engagement_rollups er WHERE er.contact_id = p_contact_id;

    SELECT COALESCE(jsonb_object_agg(event_type::text, count), '{}'::jsonb) INTO v_event_summary
    FROM (
        SELECT event_type, count(*) AS count
        FROM events WHERE contact_id = p_contact_id AND occurred_at > now() - interval '30 days'
        GROUP BY event_type
    ) t;

    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_transcripts
    FROM (
        SELECT transcript, summary, started_at
        FROM telnyx_calls
        WHERE contact_id = p_contact_id AND transcript IS NOT NULL
        ORDER BY started_at DESC LIMIT 5
    ) t;

    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_delivery_notes
    FROM (
        SELECT status, notes, occurred_at
        FROM delivery_status WHERE contact_id = p_contact_id
        ORDER BY occurred_at DESC LIMIT 10
    ) t;

    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_past_outcomes
    FROM (
        SELECT action::text, priority, reason, outcome, created_at
        FROM opportunities
        WHERE contact_id = p_contact_id AND outcome IS NOT NULL
        ORDER BY created_at DESC LIMIT 10
    ) t;

    RETURN jsonb_build_object(
        'contact', v_contact,
        'engagement_7d', COALESCE(v_rollups, '{}'::jsonb),
        'event_summary_30d', v_event_summary,
        'recent_transcripts', v_transcripts,
        'delivery_feedback', v_delivery_notes,
        'past_opportunity_outcomes', v_past_outcomes
    );
END;
$$;
