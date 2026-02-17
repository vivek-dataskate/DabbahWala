-- 009_fn_refresh_rollups.sql
-- Aggregates raw events into 7-day rolling windows per contact

SET search_path TO dabbahwala;

CREATE OR REPLACE FUNCTION refresh_engagement_rollups()
RETURNS void
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
BEGIN
    INSERT INTO engagement_rollups (contact_id, opens_7d, clicks_7d, sms_sent_7d, sms_clicks_7d, orders_7d, updated_at)
    SELECT
        c.id AS contact_id,
        COALESCE(SUM(CASE WHEN e.event_type = 'email_open' THEN 1 ELSE 0 END), 0) AS opens_7d,
        COALESCE(SUM(CASE WHEN e.event_type = 'email_click' THEN 1 ELSE 0 END), 0) AS clicks_7d,
        COALESCE(SUM(CASE WHEN e.event_type = 'sms_sent' THEN 1 ELSE 0 END), 0) AS sms_sent_7d,
        COALESCE(SUM(CASE WHEN e.event_type = 'sms_click' THEN 1 ELSE 0 END), 0) AS sms_clicks_7d,
        COALESCE(SUM(CASE WHEN e.event_type = 'order_placed' THEN 1 ELSE 0 END), 0) AS orders_7d,
        now()
    FROM contacts c
    LEFT JOIN events e ON e.contact_id = c.id
        AND e.occurred_at >= now() - interval '7 days'
    GROUP BY c.id
    ON CONFLICT (contact_id) DO UPDATE SET
        opens_7d      = EXCLUDED.opens_7d,
        clicks_7d     = EXCLUDED.clicks_7d,
        sms_sent_7d   = EXCLUDED.sms_sent_7d,
        sms_clicks_7d = EXCLUDED.sms_clicks_7d,
        orders_7d     = EXCLUDED.orders_7d,
        updated_at    = now();
END;
$$;
