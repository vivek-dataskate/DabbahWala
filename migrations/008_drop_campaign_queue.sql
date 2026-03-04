-- Migration 008: Flush campaign_queue → action_queue and drop campaign_queue
-- evaluate_rules() now writes directly to action_queue; campaign_queue is no longer needed.

SET search_path TO dabbahwala;

-- 1. Flush any remaining 'pending' campaign_queue rows into action_queue.
--    Skips contacts with no real email, APP_TO_DIRECT, or that already have a pending push.
INSERT INTO action_queue (contact_id, action_type, payload)
SELECT
    cq.contact_id,
    'push_instantly_lead',
    jsonb_build_object(
        'email',                 c.email,
        'first_name',            COALESCE(c.first_name, ''),
        'last_name',             COALESCE(c.last_name, ''),
        'phone',                 COALESCE(c.phone, ''),
        'campaign_name',         cq.to_campaign::text,
        'instantly_campaign_id', cr.instantly_campaign_id
    )
FROM campaign_queue cq
JOIN contacts c ON c.id = cq.contact_id
JOIN campaign_routing cr ON cr.default_campaign = cq.to_campaign
WHERE cq.status = 'pending'
  AND cq.to_campaign != 'APP_TO_DIRECT'
  AND cr.instantly_campaign_id IS NOT NULL
  AND c.email IS NOT NULL
  AND c.email NOT LIKE '%@app.placeholder.local'
  AND NOT EXISTS (
      SELECT 1 FROM action_queue aq
      WHERE aq.contact_id = cq.contact_id
        AND aq.action_type = 'push_instantly_lead'
        AND aq.status = 'pending'
  );

-- 2. Drop dependent stored functions that reference campaign_queue
DROP FUNCTION IF EXISTS get_pending_campaign_moves();
DROP FUNCTION IF EXISTS mark_campaign_executed(BIGINT);

-- 3. Drop the campaign_queue table
DROP TABLE IF EXISTS campaign_queue;
