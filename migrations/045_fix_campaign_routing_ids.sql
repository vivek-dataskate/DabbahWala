-- 045_fix_campaign_routing_ids.sql
-- Fix: all campaign_routing rows were pointing to the old Dabbah-NewWebsite
-- campaign (8e420c5c-4fec-4dea-ad9e-8aeb25c067c3).
-- Re-apply the correct per-segment Instantly campaign IDs that match _CAMPAIGN_META
-- in app/routers/campaigns.py.

SET search_path TO dabbahwala;

-- NURTURE_SLOW  →  cold contacts
UPDATE campaign_routing
SET instantly_campaign_id   = '90ecd160-22cc-46b1-9fa5-9342fe970837',
    instantly_campaign_name = 'DW-NurtureSlow-ColdContacts'
WHERE default_campaign = 'NURTURE_SLOW';

-- PROMO_STANDARD  →  engaged + active customers
UPDATE campaign_routing
SET instantly_campaign_id   = '30292b3d-9f39-4ef3-b0ba-ea15c634acef',
    instantly_campaign_name = 'DW-PromoStandard-ActiveEngaged'
WHERE default_campaign = 'PROMO_STANDARD';

-- PROMO_AGGRESSIVE  →  lapsed customers
UPDATE campaign_routing
SET instantly_campaign_id   = 'c9af877a-77ac-491c-a5ee-a8ea7646416b',
    instantly_campaign_name = 'DW-PromoAggressive-LapsedCustomers'
WHERE default_campaign = 'PROMO_AGGRESSIVE';

-- NEW_CUSTOMER_ONBOARDING  →  first-time buyers
UPDATE campaign_routing
SET instantly_campaign_id   = 'c4c42e73-83fd-4d43-b629-db5b11be66ae',
    instantly_campaign_name = 'DW-NewCustomerOnboarding'
WHERE default_campaign = 'NEW_CUSTOMER_ONBOARDING';

-- REACTIVATION  →  long-dormant customers
UPDATE campaign_routing
SET instantly_campaign_id   = '0c760ec8-3415-48cd-87ff-b58babc17dde',
    instantly_campaign_name = 'DW-Reactivation-LongDormant'
WHERE default_campaign = 'REACTIVATION';
