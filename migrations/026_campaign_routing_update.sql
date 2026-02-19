-- 026_campaign_routing_update.sql
-- Update campaign routing to map each lifecycle segment to its own Instantly campaign.
-- Currently all segments point to Dabbah-NewWebsite. We need separate campaigns.

SET search_path TO dabbahwala;

-- Add new campaign_name enum values if needed
-- (NURTURE_SLOW, PROMO_STANDARD, PROMO_AGGRESSIVE, NEW_CUSTOMER_ONBOARDING, REACTIVATION already exist)

-- Add APP_TO_DIRECT campaign type
DO $$
BEGIN
    ALTER TYPE campaign_name ADD VALUE IF NOT EXISTS 'APP_TO_DIRECT';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Update campaign_routing with Instantly campaign IDs
-- These will be populated once campaigns are created in Instantly

-- Existing: cold -> NURTURE_SLOW (Dabbah-NewWebsite - the current re-engagement series)
UPDATE campaign_routing
SET instantly_campaign_id = '8e420c5c-4fec-4dea-ad9e-8aeb25c067c3',
    instantly_campaign_name = 'Dabbah-NewWebsite'
WHERE lifecycle_segment = 'cold';

-- New customer onboarding (NEEDS INSTANTLY CAMPAIGN CREATED)
UPDATE campaign_routing
SET instantly_campaign_name = 'Dabbah-NewCustomer-Onboarding'
WHERE lifecycle_segment = 'new_customer';

-- Active/Engaged customers get promo campaigns
UPDATE campaign_routing
SET instantly_campaign_name = 'Dabbah-ActiveCustomer-Engagement'
WHERE lifecycle_segment IN ('engaged', 'active_customer');

-- Lapsed customers get aggressive re-engagement
UPDATE campaign_routing
SET instantly_campaign_name = 'Dabbah-Lapsed-Winback'
WHERE lifecycle_segment = 'lapsed_customer';

-- Reactivation candidates
UPDATE campaign_routing
SET instantly_campaign_name = 'Dabbah-Reactivation'
WHERE lifecycle_segment = 'reactivation_candidate';

-- NOTE: APP_TO_DIRECT campaign routing is handled through the campaign_queue
-- (intelligence.py inserts directly with to_campaign = 'APP_TO_DIRECT').
-- It cannot be in campaign_routing because the PK is lifecycle_segment and
-- 'cold' already exists. The Instantly campaign name for APP_TO_DIRECT is
-- resolved by the intelligence engine, not through this routing table.
