-- 023_instantly_campaign_routing.sql
-- Map campaign_routing rows to actual Instantly campaign IDs

SET search_path TO dabbahwala;

ALTER TABLE campaign_routing
    ADD COLUMN IF NOT EXISTS instantly_campaign_id TEXT,
    ADD COLUMN IF NOT EXISTS instantly_campaign_name TEXT;

-- Point all segments to "Dabbah-NewWebsite" (the only active DabbahWala campaign)
UPDATE campaign_routing
SET instantly_campaign_id   = '8e420c5c-4fec-4dea-ad9e-8aeb25c067c3',
    instantly_campaign_name = 'Dabbah-NewWebsite';
