-- 006_drop_campaign_queue.sql
-- Backfill all pending campaign_queue rows into action_queue, then drop
-- campaign_queue entirely. evaluate_rules() now writes directly to action_queue.
-- Run once — idempotent (INSERT uses NOT EXISTS dedup).

SET search_path TO dabbahwala;

-- 1. Backfill existing pending rows into action_queue
INSERT INTO action_queue (contact_id, action_type, payload)
SELECT
    cq.contact_id::INTEGER,
    'push_instantly_lead',
    jsonb_build_object(
        'instantly_campaign_id', cr.instantly_campaign_id,
        'campaign_name',         cq.to_campaign::TEXT,
        'email',                 c.email,
        'first_name',            COALESCE(c.first_name, ''),
        'last_name',             COALESCE(c.last_name, ''),
        'phone',                 COALESCE(c.phone, '')
    )
FROM campaign_queue cq
JOIN contacts        c  ON c.id  = cq.contact_id
JOIN campaign_routing cr ON cr.default_campaign = cq.to_campaign
WHERE cq.status                          = 'pending'
  AND c.email IS NOT NULL
  AND c.email NOT LIKE '%@app.placeholder.local'
  AND cr.instantly_campaign_id IS NOT NULL
  AND cq.to_campaign::TEXT               != 'APP_TO_DIRECT'
  AND NOT EXISTS (
      SELECT 1
      FROM   action_queue aq
      WHERE  aq.contact_id   = cq.contact_id::INTEGER
        AND  aq.action_type  = 'push_instantly_lead'
        AND  aq.status       = 'pending'
  );

-- 2. Drop dependent SQL helpers (no longer needed)
DROP FUNCTION IF EXISTS get_pending_campaign_moves();
DROP FUNCTION IF EXISTS mark_campaign_executed(BIGINT);

-- 3. Drop the table
DROP TABLE IF EXISTS campaign_queue;
