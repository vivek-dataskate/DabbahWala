-- 061_fix_campaign_routing_ids.sql
-- Fix: campaign_routing.instantly_campaign_id values were stale (pointed to
-- deleted/renamed Instantly campaigns).  Sync them to the current live
-- campaign IDs that match _CAMPAIGN_META in app/routers/campaigns.py.

SET search_path TO dabbahwala;

-- NURTURE_SLOW → DW-NurtureSlow-ColdContacts
UPDATE campaign_routing
SET instantly_campaign_id   = '76a88797-961a-47b6-af11-77e2211c4e73',
    instantly_campaign_name = 'DW-NurtureSlow-ColdContacts'
WHERE default_campaign = 'NURTURE_SLOW';

-- PROMO_STANDARD → DW-PromoStandard-ActiveEngaged
UPDATE campaign_routing
SET instantly_campaign_id   = 'f3e2d621-9bf2-4130-bc1c-f8168fc44e1e',
    instantly_campaign_name = 'DW-PromoStandard-ActiveEngaged'
WHERE default_campaign = 'PROMO_STANDARD';

-- PROMO_AGGRESSIVE → DW-PromoAggressive-LapsedCustomers
UPDATE campaign_routing
SET instantly_campaign_id   = '87d44ff1-8720-4c1d-92ff-b827970f323f',
    instantly_campaign_name = 'DW-PromoAggressive-LapsedCustomers'
WHERE default_campaign = 'PROMO_AGGRESSIVE';

-- NEW_CUSTOMER_ONBOARDING → DW-NewCustomerOnboarding
UPDATE campaign_routing
SET instantly_campaign_id   = '8a5ccbfb-500d-4060-ad99-76aa0159bbf2',
    instantly_campaign_name = 'DW-NewCustomerOnboarding'
WHERE default_campaign = 'NEW_CUSTOMER_ONBOARDING';

-- REACTIVATION → DW-Reactivation-LongDormant
UPDATE campaign_routing
SET instantly_campaign_id   = '69c84455-d9b8-437f-b249-8325d23798e6',
    instantly_campaign_name = 'DW-Reactivation-LongDormant'
WHERE default_campaign = 'REACTIVATION';
