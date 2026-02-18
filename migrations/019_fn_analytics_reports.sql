-- 019_fn_analytics_reports.sql
-- Stored functions for analytics, reporting, and daily report generation.
-- Python layer calls these instead of raw SQL.

SET search_path TO dabbahwala;


-- LIFECYCLE SUMMARY (pipeline snapshot)
CREATE OR REPLACE FUNCTION get_lifecycle_summary()
RETURNS TABLE(lifecycle_segment lifecycle_segment, count BIGINT)
LANGUAGE sql
SET search_path TO dabbahwala
AS $$
    SELECT lifecycle_segment, count(*) AS count
    FROM contacts
    GROUP BY lifecycle_segment
    ORDER BY count DESC;
$$;


-- CAMPAIGN PERFORMANCE
CREATE OR REPLACE FUNCTION get_campaign_performance(p_campaign campaign_name, p_days INT DEFAULT 7)
RETURNS JSONB
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_contact_count BIGINT;
    v_activity JSONB;
BEGIN
    SELECT count(*) INTO v_contact_count
    FROM contacts WHERE current_campaign = p_campaign;

    SELECT COALESCE(jsonb_object_agg(event_type::text, count), '{}'::jsonb) INTO v_activity
    FROM (
        SELECT e.event_type, count(*) AS count
        FROM events e
        JOIN contacts c ON c.id = e.contact_id
        WHERE c.current_campaign = p_campaign
          AND e.occurred_at > now() - (p_days || ' days')::interval
        GROUP BY e.event_type
    ) t;

    RETURN jsonb_build_object(
        'campaign', p_campaign::text,
        'days', p_days,
        'contacts_in_campaign', v_contact_count,
        'activity', v_activity
    );
END;
$$;


-- ENGAGEMENT TRENDS (by day)
CREATE OR REPLACE FUNCTION get_engagement_trends(p_days INT DEFAULT 30)
RETURNS TABLE(day DATE, event_type event_type, count BIGINT)
LANGUAGE sql
SET search_path TO dabbahwala
AS $$
    SELECT occurred_at::date AS day, event_type, count(*) AS count
    FROM events
    WHERE occurred_at > now() - (p_days || ' days')::interval
    GROUP BY occurred_at::date, event_type
    ORDER BY day DESC, event_type;
$$;


-- ORDER ATTRIBUTION
CREATE OR REPLACE FUNCTION get_order_attribution(p_days_lookback INT DEFAULT 7)
RETURNS JSONB
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_result JSONB;
BEGIN
    SELECT row_to_json(t)::jsonb INTO v_result
    FROM (
        WITH orders AS (
            SELECT e.id AS order_event_id, e.contact_id, e.occurred_at AS order_at,
                   c.email, c.current_campaign
            FROM events e
            JOIN contacts c ON c.id = e.contact_id
            WHERE e.event_type = 'order_placed'
              AND e.occurred_at > now() - interval '30 days'
        ),
        attributed AS (
            SELECT o.*,
                   (SELECT e2.event_type FROM events e2
                    WHERE e2.contact_id = o.contact_id
                      AND e2.event_type IN ('email_open', 'email_click', 'sms_click')
                      AND e2.occurred_at BETWEEN o.order_at - (p_days_lookback || ' days')::interval AND o.order_at
                    ORDER BY e2.occurred_at DESC LIMIT 1
                   ) AS attributed_touch
            FROM orders o
        )
        SELECT
            count(*) AS total_orders,
            count(attributed_touch) AS attributed_orders,
            count(*) - count(attributed_touch) AS unattributed_orders
        FROM attributed
    ) t;

    RETURN COALESCE(v_result, '{}'::jsonb);
END;
$$;


-- DECISION HISTORY for a contact
CREATE OR REPLACE FUNCTION get_decision_history(p_contact_id BIGINT, p_limit INT DEFAULT 20)
RETURNS TABLE(
    id BIGINT,
    rule_name TEXT,
    prev_lifecycle lifecycle_segment,
    new_lifecycle lifecycle_segment,
    changes_applied JSONB,
    decided_at TIMESTAMPTZ
)
LANGUAGE sql
SET search_path TO dabbahwala
AS $$
    SELECT dl.id, r.rule_name, dl.prev_lifecycle, dl.new_lifecycle,
           dl.changes_applied, dl.decided_at
    FROM decision_log dl
    LEFT JOIN rules r ON r.id = dl.rule_id
    WHERE dl.contact_id = p_contact_id
    ORDER BY dl.decided_at DESC
    LIMIT p_limit;
$$;


-- GET DAILY REPORT (read-only lookup)
CREATE OR REPLACE FUNCTION get_daily_report(p_report_date DATE)
RETURNS TABLE(id BIGINT, report_date DATE, report_data JSONB, net_new_orders INT, created_at TIMESTAMPTZ)
LANGUAGE sql
SET search_path TO dabbahwala
AS $$
    SELECT id, report_date, report_data, net_new_orders, created_at
    FROM daily_reports
    WHERE report_date = p_report_date;
$$;


-- GENERATE DAILY REPORT (computes + upserts)
CREATE OR REPLACE FUNCTION generate_daily_report(p_report_date DATE)
RETURNS JSONB
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_activity JSONB;
    v_transitions JSONB;
    v_pipeline JSONB;
    v_net_new INT;
    v_report_data JSONB;
    v_report_id BIGINT;
BEGIN
    -- Campaign activity
    SELECT COALESCE(jsonb_object_agg(event_type::text, count), '{}'::jsonb) INTO v_activity
    FROM (
        SELECT e.event_type, count(*) AS count
        FROM events e
        WHERE e.occurred_at::date = p_report_date
          AND e.event_type IN ('email_open', 'email_click', 'sms_sent', 'sms_click', 'order_placed', 'unsubscribe')
        GROUP BY e.event_type
    ) t;

    -- Lifecycle transitions
    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_transitions
    FROM (
        SELECT prev_lifecycle::text, new_lifecycle::text, count(*) AS count
        FROM decision_log
        WHERE decided_at::date = p_report_date
        GROUP BY prev_lifecycle, new_lifecycle
    ) t;

    -- Pipeline snapshot
    SELECT COALESCE(jsonb_object_agg(lifecycle_segment::text, count), '{}'::jsonb) INTO v_pipeline
    FROM (
        SELECT lifecycle_segment, count(*) AS count
        FROM contacts
        GROUP BY lifecycle_segment
    ) t;

    -- Net new orders (attributed to marketing touches in prior 7 days)
    SELECT count(DISTINCT e_order.id)::int INTO v_net_new
    FROM events e_order
    WHERE e_order.event_type = 'order_placed'
      AND e_order.occurred_at::date = p_report_date
      AND EXISTS (
          SELECT 1 FROM events e_touch
          WHERE e_touch.contact_id = e_order.contact_id
            AND e_touch.event_type IN ('email_open', 'email_click', 'sms_click')
            AND e_touch.occurred_at BETWEEN e_order.occurred_at - interval '7 days' AND e_order.occurred_at
      );

    v_net_new := COALESCE(v_net_new, 0);

    v_report_data := jsonb_build_object(
        'activity', v_activity,
        'transitions', v_transitions,
        'pipeline', v_pipeline,
        'net_new_orders', v_net_new
    );

    -- Upsert
    INSERT INTO daily_reports (report_date, report_data, net_new_orders)
    VALUES (p_report_date, v_report_data, v_net_new)
    ON CONFLICT (report_date) DO UPDATE SET
        report_data = EXCLUDED.report_data,
        net_new_orders = EXCLUDED.net_new_orders,
        created_at = now()
    RETURNING daily_reports.id INTO v_report_id;

    RETURN jsonb_build_object(
        'id', v_report_id,
        'report_date', p_report_date,
        'activity', v_activity,
        'transitions', v_transitions,
        'pipeline', v_pipeline,
        'net_new_orders', v_net_new
    );
END;
$$;
