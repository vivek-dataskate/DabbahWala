-- Migration 047: Add missing 30d/90d columns to engagement_rollups
-- ----------------------------------------------------------------
-- Migration 034 added opens_30d and orders_90d but clicks_30d and
-- sms_sent_30d were never created. All 4 are referenced in agents.py.
-- Using ADD COLUMN IF NOT EXISTS so this is safe to re-run.

SET search_path TO dabbahwala;

ALTER TABLE engagement_rollups
    ADD COLUMN IF NOT EXISTS opens_30d    INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS clicks_30d   INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS sms_sent_30d INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS orders_90d   INT NOT NULL DEFAULT 0;

-- Update refresh function to also populate the new 30d columns
CREATE OR REPLACE FUNCTION refresh_engagement_rollups()
RETURNS VOID
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
BEGIN
    INSERT INTO engagement_rollups (
        contact_id,
        opens_7d, clicks_7d, sms_sent_7d, sms_clicks_7d, orders_7d,
        opens_30d, clicks_30d, sms_sent_30d, orders_90d,
        updated_at
    )
    SELECT
        c.id AS contact_id,
        COALESCE(SUM(CASE WHEN e.event_type = 'email_open'   AND e.occurred_at >= NOW() - INTERVAL '7 days'  THEN 1 ELSE 0 END), 0) AS opens_7d,
        COALESCE(SUM(CASE WHEN e.event_type = 'email_click'  AND e.occurred_at >= NOW() - INTERVAL '7 days'  THEN 1 ELSE 0 END), 0) AS clicks_7d,
        COALESCE(SUM(CASE WHEN e.event_type = 'sms_sent'     AND e.occurred_at >= NOW() - INTERVAL '7 days'  THEN 1 ELSE 0 END), 0) AS sms_sent_7d,
        COALESCE(SUM(CASE WHEN e.event_type = 'sms_click'    AND e.occurred_at >= NOW() - INTERVAL '7 days'  THEN 1 ELSE 0 END), 0) AS sms_clicks_7d,
        COALESCE(SUM(CASE WHEN e.event_type = 'order_placed' AND e.occurred_at >= NOW() - INTERVAL '7 days'  THEN 1 ELSE 0 END), 0) AS orders_7d,
        COALESCE(SUM(CASE WHEN e.event_type = 'email_open'   AND e.occurred_at >= NOW() - INTERVAL '30 days' THEN 1 ELSE 0 END), 0) AS opens_30d,
        COALESCE(SUM(CASE WHEN e.event_type = 'email_click'  AND e.occurred_at >= NOW() - INTERVAL '30 days' THEN 1 ELSE 0 END), 0) AS clicks_30d,
        COALESCE(SUM(CASE WHEN e.event_type = 'sms_sent'     AND e.occurred_at >= NOW() - INTERVAL '30 days' THEN 1 ELSE 0 END), 0) AS sms_sent_30d,
        COALESCE(SUM(CASE WHEN e.event_type = 'order_placed' AND e.occurred_at >= NOW() - INTERVAL '90 days' THEN 1 ELSE 0 END), 0) AS orders_90d,
        NOW() AS updated_at
    FROM contacts c
    LEFT JOIN events e ON e.contact_id = c.id
    GROUP BY c.id
    ON CONFLICT (contact_id) DO UPDATE SET
        opens_7d      = EXCLUDED.opens_7d,
        clicks_7d     = EXCLUDED.clicks_7d,
        sms_sent_7d   = EXCLUDED.sms_sent_7d,
        sms_clicks_7d = EXCLUDED.sms_clicks_7d,
        orders_7d     = EXCLUDED.orders_7d,
        opens_30d     = EXCLUDED.opens_30d,
        clicks_30d    = EXCLUDED.clicks_30d,
        sms_sent_30d  = EXCLUDED.sms_sent_30d,
        orders_90d    = EXCLUDED.orders_90d,
        updated_at    = NOW();
END;
$$;
