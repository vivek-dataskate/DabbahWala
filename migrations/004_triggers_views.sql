-- 004_triggers_views.sql
-- All triggers and views — final consolidated state.
-- Safe to re-run (CREATE OR REPLACE for functions/views; DROP IF EXISTS before CREATE for triggers).

SET search_path TO dabbahwala;

-- ---------------------------------------------------------------------------
-- Trigger function: generic updated_at setter
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

-- ---------------------------------------------------------------------------
-- Trigger: contacts.updated_at
-- ---------------------------------------------------------------------------

DROP TRIGGER IF EXISTS contacts_updated_at ON contacts;
CREATE TRIGGER contacts_updated_at
    BEFORE UPDATE ON contacts
    FOR EACH ROW
    EXECUTE FUNCTION trigger_set_updated_at();

-- ---------------------------------------------------------------------------
-- Trigger: customer_goals.updated_at
-- ---------------------------------------------------------------------------

DROP TRIGGER IF EXISTS customer_goals_updated_at ON customer_goals;
CREATE TRIGGER customer_goals_updated_at
    BEFORE UPDATE ON customer_goals
    FOR EACH ROW
    EXECUTE FUNCTION update_customer_goals_updated_at();

-- ---------------------------------------------------------------------------
-- View: telnyx_tracking
-- Compatibility shim over telnyx_messages — exposes sent_at as created_at
-- so existing queries referencing telnyx_tracking.created_at still work.
-- ---------------------------------------------------------------------------

DROP VIEW IF EXISTS telnyx_tracking;
CREATE VIEW telnyx_tracking AS
SELECT
    id,
    contact_id,
    direction,
    from_number,
    to_number,
    body,
    telnyx_msg_id,
    status,
    is_delivery_staff,
    metadata,
    sent_at,
    sent_at AS created_at
FROM telnyx_messages;

-- ---------------------------------------------------------------------------
-- View: shipday_feedback_summary
-- Sentiment breakdown by week for dashboard reporting.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW shipday_feedback_summary AS
SELECT
    date_trunc('week', occurred_at)                                                         AS week,
    sentiment,
    COUNT(*)                                                                                 AS count,
    round(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (PARTITION BY date_trunc('week', occurred_at)), 1) AS pct
FROM shipday_communications
WHERE comm_type = 'customer_feedback'
  AND occurred_at IS NOT NULL
GROUP BY 1, 2
ORDER BY 1 DESC, 2;
