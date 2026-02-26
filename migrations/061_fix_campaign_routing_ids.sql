-- 061_fix_campaign_routing_ids.sql
-- Fix: campaign_routing.instantly_campaign_id values were stale (pointed to
-- deleted/renamed Instantly campaigns).  Sync them to the current live
-- campaign IDs that match _CAMPAIGN_META in app/routers/campaigns.py.
-- Also removes stale rows from instantly_campaigns (webhook event registry)
-- that reference the old IDs and inserts the correct ones.

SET search_path TO dabbahwala;

-- ── 1. Fix campaign_routing ──────────────────────────────────────────────────

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

-- ── 2. Clean up instantly_campaigns ─────────────────────────────────────────
-- Remove rows with the old stale IDs that no longer exist in Instantly.
-- The correct IDs will be re-seeded on the next POST /api/webhooks/sync-campaigns call.

DELETE FROM instantly_campaigns
WHERE campaign_id IN (
    '90ecd160-22cc-46b1-9fa5-9342fe970837',  -- old NURTURE_SLOW
    '30292b3d-9f39-4ef3-b0ba-ea15c634acef',  -- old PROMO_STANDARD
    'c9af877a-77ac-491c-a5ee-a8ea7646416b',  -- old PROMO_AGGRESSIVE
    'c4c42e73-83fd-4d43-b629-db5b11be66ae',  -- old NEW_CUSTOMER_ONBOARDING
    '0c760ec8-3415-48cd-87ff-b58babc17dde'   -- old REACTIVATION
);

-- Insert correct IDs (idempotent via ON CONFLICT DO NOTHING)
INSERT INTO instantly_campaigns (campaign_id, campaign_name, source) VALUES
    ('76a88797-961a-47b6-af11-77e2211c4e73', 'DW-NurtureSlow-ColdContacts',          'hardcoded'),
    ('f3e2d621-9bf2-4130-bc1c-f8168fc44e1e', 'DW-PromoStandard-ActiveEngaged',        'hardcoded'),
    ('87d44ff1-8720-4c1d-92ff-b827970f323f', 'DW-PromoAggressive-LapsedCustomers',    'hardcoded'),
    ('8a5ccbfb-500d-4060-ad99-76aa0159bbf2', 'DW-NewCustomerOnboarding',              'hardcoded'),
    ('69c84455-d9b8-437f-b249-8325d23798e6', 'DW-Reactivation-LongDormant',           'hardcoded'),
    ('c763e229-f633-468b-bfe4-7f9a4fd21036', 'DW-ActiveCustomer',                     'hardcoded')
ON CONFLICT (campaign_id) DO NOTHING;
