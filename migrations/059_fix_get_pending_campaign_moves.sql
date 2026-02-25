-- 059_fix_get_pending_campaign_moves.sql
-- Add first_name and last_name to get_pending_campaign_moves()
-- so n8n can pass them to Instantly when adding leads.

SET search_path TO dabbahwala;

CREATE OR REPLACE FUNCTION get_pending_campaign_moves()
RETURNS TABLE(
    queue_id       BIGINT,
    contact_email  TEXT,
    contact_phone  TEXT,
    contact_first_name TEXT,
    contact_last_name  TEXT,
    from_campaign  campaign_name,
    to_campaign    campaign_name
)
LANGUAGE sql
SET search_path TO dabbahwala
AS $$
    SELECT cq.id,
           c.email,
           c.phone,
           c.first_name,
           c.last_name,
           cq.from_campaign,
           cq.to_campaign
    FROM campaign_queue cq
    JOIN contacts c ON c.id = cq.contact_id
    WHERE cq.status = 'pending'
    ORDER BY cq.created_at;
$$;
