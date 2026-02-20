-- 044_active_customer_campaign.sql
-- Add ACTIVE_CUSTOMER as a dedicated Instantly campaign (split from PROMO_STANDARD).
-- active_customer segment previously shared PROMO_STANDARD with engaged;
-- it now has its own campaign for better targeting.

SET search_path TO dabbahwala;

-- 1. Add ACTIVE_CUSTOMER to the campaign_name enum
ALTER TYPE campaign_name ADD VALUE IF NOT EXISTS 'ACTIVE_CUSTOMER';

-- 2. Route active_customer → ACTIVE_CUSTOMER (was PROMO_STANDARD)
UPDATE campaign_routing
SET default_campaign       = 'ACTIVE_CUSTOMER',
    instantly_campaign_id   = '',          -- filled in after setup-instantly endpoint returns IDs
    instantly_campaign_name = 'DW-ActiveCustomer'
WHERE lifecycle_segment = 'active_customer';

-- 3. Rename PROMO_STANDARD routing label to reflect engaged-only scope
UPDATE campaign_routing
SET instantly_campaign_name = 'DW-PromoStandard-Engaged'
WHERE default_campaign = 'PROMO_STANDARD';
