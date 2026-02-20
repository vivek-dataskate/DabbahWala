-- 044_active_customer_campaign.sql
-- Add ACTIVE_CUSTOMER as a dedicated Instantly campaign (split from PROMO_STANDARD).
-- Also fixes all campaign_routing rows that were incorrectly set to the personal
-- Instantly campaign (8e420c5c-4fec-4dea-ad9e-8aeb25c067c3 / Dabbah-NewWebsite).

SET search_path TO dabbahwala;

-- 1. Add ACTIVE_CUSTOMER to the campaign_name enum
ALTER TYPE campaign_name ADD VALUE IF NOT EXISTS 'ACTIVE_CUSTOMER';

-- 2. Fix all 5 existing rows with the real DabbahWala campaign IDs
UPDATE campaign_routing
SET instantly_campaign_id   = '90ecd160-22cc-46b1-9fa5-9342fe970837',
    instantly_campaign_name = 'DW-NurtureSlow-ColdContacts'
WHERE lifecycle_segment = 'cold';

UPDATE campaign_routing
SET instantly_campaign_id   = '30292b3d-9f39-4ef3-b0ba-ea15c634acef',
    instantly_campaign_name = 'DW-PromoStandard-Engaged'
WHERE lifecycle_segment = 'engaged';

UPDATE campaign_routing
SET instantly_campaign_id   = 'c9af877a-77ac-491c-a5ee-a8ea7646416b',
    instantly_campaign_name = 'DW-PromoAggressive-LapsedCustomers'
WHERE lifecycle_segment = 'lapsed_customer';

UPDATE campaign_routing
SET instantly_campaign_id   = 'c4c42e73-83fd-4d43-b629-db5b11be66ae',
    instantly_campaign_name = 'DW-NewCustomerOnboarding'
WHERE lifecycle_segment = 'new_customer';

UPDATE campaign_routing
SET instantly_campaign_id   = '0c760ec8-3415-48cd-87ff-b58babc17dde',
    instantly_campaign_name = 'DW-Reactivation-LongDormant'
WHERE lifecycle_segment = 'reactivation_candidate';

-- 3. Route active_customer → ACTIVE_CUSTOMER (was PROMO_STANDARD)
--    instantly_campaign_id left blank; filled in by POST /api/campaigns/setup-instantly
UPDATE campaign_routing
SET default_campaign        = 'ACTIVE_CUSTOMER',
    instantly_campaign_id   = '',
    instantly_campaign_name = 'DW-ActiveCustomer'
WHERE lifecycle_segment = 'active_customer';
