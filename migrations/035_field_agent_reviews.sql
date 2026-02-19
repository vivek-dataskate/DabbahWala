-- Migration 035: Field Agent Reviews + Order Pattern Analytics
-- Enables AI transcript analysis of field agent calls (no self-reporting)
-- and order pattern functions for daily reports.

-- ---------------------------------------------------------------------------
-- 0. Add agent_name to telnyx_calls so field agent calls can be attributed
-- ---------------------------------------------------------------------------
ALTER TABLE telnyx_calls
    ADD COLUMN IF NOT EXISTS agent_name TEXT;

-- ---------------------------------------------------------------------------
-- 1. Field Agent Reviews table
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS field_agent_reviews (
    id                      BIGSERIAL PRIMARY KEY,
    contact_id              BIGINT REFERENCES contacts(id),
    call_id                 BIGINT REFERENCES telnyx_calls(id),
    opportunity_id          BIGINT REFERENCES opportunities(id),
    agent_name              TEXT,
    reviewed_at             TIMESTAMPTZ DEFAULT NOW(),

    -- AI transcript analysis (Claude reads transcript, no agent self-report)
    pitch_quality_score     FLOAT,          -- 0–10
    asked_for_order         BOOLEAN,
    customer_signal         TEXT,           -- interested/warm/neutral/objecting/not_interested
    objections_raised       TEXT[],
    honest_assessment       TEXT,           -- Claude's unfiltered written verdict

    -- Recommended next step
    recommended_next_action TEXT,           -- call_again_today/send_sms_now/hand_to_automation/close_won/close_lost
    next_action_timing      TEXT,           -- immediate/today/tomorrow/3days
    next_action_reason      TEXT,

    -- Follow-up tracking
    follow_up_opportunity_id BIGINT,        -- set when a new opportunity is auto-created
    status                  TEXT DEFAULT 'pending'  -- pending/actioned/closed
);

CREATE INDEX IF NOT EXISTS idx_far_contact ON field_agent_reviews (contact_id);
CREATE INDEX IF NOT EXISTS idx_far_agent   ON field_agent_reviews (agent_name);
CREATE INDEX IF NOT EXISTS idx_far_date    ON field_agent_reviews (reviewed_at);

-- ---------------------------------------------------------------------------
-- 2. Order pattern: day-of-week breakdown
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION get_order_day_patterns(p_days INT DEFAULT 90)
RETURNS JSON AS $$
DECLARE result JSON;
BEGIN
    SELECT json_agg(row_to_json(t)) INTO result FROM (
        SELECT
            TRIM(TO_CHAR(o.order_date, 'Day')) AS day_name,
            EXTRACT(DOW FROM o.order_date)::INT AS day_num,
            COUNT(*)                            AS order_count,
            ROUND(SUM(o.total_amount)::numeric, 2) AS revenue,
            ROUND(AVG(o.total_amount)::numeric, 2) AS avg_order_value
        FROM orders o
        WHERE o.order_date >= CURRENT_DATE - p_days
        GROUP BY 1, 2
        ORDER BY 2
    ) t;
    RETURN COALESCE(result, '[]'::json);
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- 3. Order pattern: top menu items
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION get_top_menu_items(p_days INT DEFAULT 30, p_limit INT DEFAULT 15)
RETURNS JSON AS $$
DECLARE result JSON;
BEGIN
    SELECT json_agg(row_to_json(t)) INTO result FROM (
        SELECT
            oi.item_name,
            mi.category,
            mi.is_veg,
            SUM(oi.quantity)                       AS total_qty,
            COUNT(DISTINCT oi.order_id)            AS order_count,
            ROUND(AVG(oi.unit_price)::numeric, 2)  AS avg_price,
            ROUND(SUM(oi.line_total)::numeric, 2)  AS total_revenue
        FROM order_items oi
        JOIN orders o       ON o.id  = oi.order_id
        LEFT JOIN menu_items mi ON mi.id = oi.menu_item_id
        WHERE o.order_date >= CURRENT_DATE - p_days
        GROUP BY oi.item_name, mi.category, mi.is_veg
        ORDER BY total_qty DESC
        LIMIT p_limit
    ) t;
    RETURN COALESCE(result, '[]'::json);
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- 4. Order pattern: customer frequency segments
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION get_customer_frequency_segments(p_days INT DEFAULT 90)
RETURNS JSON AS $$
DECLARE result JSON;
BEGIN
    SELECT json_agg(row_to_json(t)) INTO result FROM (
        SELECT
            CASE
                WHEN order_count >= 10 THEN 'weekly_plus'
                WHEN order_count >= 4  THEN 'regular'
                WHEN order_count >= 2  THEN 'occasional'
                ELSE 'one_time'
            END AS segment,
            COUNT(*)                               AS customer_count,
            ROUND(AVG(total_spend)::numeric, 2)    AS avg_spend,
            ROUND(SUM(total_spend)::numeric, 2)    AS total_revenue
        FROM (
            SELECT contact_id,
                   COUNT(*)          AS order_count,
                   SUM(total_amount) AS total_spend
            FROM orders
            WHERE order_date >= CURRENT_DATE - p_days
            GROUP BY contact_id
        ) sub
        GROUP BY 1
        ORDER BY customer_count DESC
    ) t;
    RETURN COALESCE(result, '[]'::json);
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- 5. Top contacts to call (daily field brief — proactive, not reactive)
--    Criteria: 10+ lifetime orders, last order 14–90 days ago,
--              no active field_sales_call opportunity already open
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION get_top_contacts_to_call(p_limit INT DEFAULT 15)
RETURNS JSON AS $$
DECLARE result JSON;
BEGIN
    SELECT json_agg(row_to_json(t)) INTO result FROM (
        SELECT
            c.id                                        AS contact_id,
            c.first_name,
            c.last_name,
            c.phone,
            c.email,
            c.lifecycle_segment,
            c.total_orders,
            c.last_order_at,
            (CURRENT_DATE - c.last_order_at::date)     AS days_since_last_order,
            ROUND(COALESCE(SUM(o.total_amount), 0)::numeric, 2) AS lifetime_spend
        FROM contacts c
        LEFT JOIN orders o ON o.contact_id = c.id
        WHERE
            c.total_orders >= 10
            AND c.last_order_at IS NOT NULL
            AND c.last_order_at::date BETWEEN CURRENT_DATE - 90 AND CURRENT_DATE - 14
            AND c.lifecycle_segment NOT IN ('churned', 'optout', 'cooling')
            AND NOT EXISTS (
                SELECT 1 FROM opportunities op
                WHERE op.contact_id = c.id
                  AND op.action = 'field_sales_call'
                  AND op.status IN ('pending', 'dispatched')
            )
        GROUP BY c.id
        ORDER BY c.total_orders DESC, lifetime_spend DESC
        LIMIT p_limit
    ) t;
    RETURN COALESCE(result, '[]'::json);
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- 6. Field agent scorecard (per-agent performance over N days)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION get_field_agent_scorecard(p_days INT DEFAULT 30)
RETURNS JSON AS $$
DECLARE result JSON;
BEGIN
    SELECT json_agg(row_to_json(t)) INTO result FROM (
        SELECT
            r.agent_name,
            COUNT(*)                                                          AS calls_reviewed,
            ROUND(AVG(r.pitch_quality_score)::numeric, 1)                    AS avg_pitch_quality,
            SUM(CASE WHEN r.asked_for_order         THEN 1 ELSE 0 END)       AS asked_for_order_count,
            SUM(CASE WHEN r.customer_signal = 'interested' THEN 1 ELSE 0 END) AS interested_customers,
            SUM(CASE WHEN r.recommended_next_action = 'close_won'         THEN 1 ELSE 0 END) AS calls_closed_won,
            SUM(CASE WHEN r.recommended_next_action = 'close_lost'        THEN 1 ELSE 0 END) AS calls_closed_lost,
            SUM(CASE WHEN r.recommended_next_action = 'call_again_today'  THEN 1 ELSE 0 END) AS needs_follow_up,
            SUM(CASE WHEN r.recommended_next_action = 'hand_to_automation' THEN 1 ELSE 0 END) AS handed_to_automation
        FROM field_agent_reviews r
        WHERE r.reviewed_at >= NOW() - (p_days || ' days')::INTERVAL
          AND r.agent_name IS NOT NULL
        GROUP BY r.agent_name
        ORDER BY calls_closed_won DESC
    ) t;
    RETURN COALESCE(result, '[]'::json);
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- 7. Recent field agent reviews (for report detail rows)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION get_recent_field_agent_reviews(p_days INT DEFAULT 7)
RETURNS JSON AS $$
DECLARE result JSON;
BEGIN
    SELECT json_agg(row_to_json(t)) INTO result FROM (
        SELECT
            r.id,
            r.agent_name,
            c.first_name,
            c.last_name,
            c.phone,
            r.reviewed_at,
            r.pitch_quality_score,
            r.asked_for_order,
            r.customer_signal,
            r.objections_raised,
            r.honest_assessment,
            r.recommended_next_action,
            r.next_action_reason
        FROM field_agent_reviews r
        JOIN contacts c ON c.id = r.contact_id
        WHERE r.reviewed_at >= NOW() - (p_days || ' days')::INTERVAL
        ORDER BY r.reviewed_at DESC
        LIMIT 100
    ) t;
    RETURN COALESCE(result, '[]'::json);
END;
$$ LANGUAGE plpgsql;
