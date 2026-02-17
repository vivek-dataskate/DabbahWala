-- 014_seed_campaign_routing.sql
-- Default mapping from lifecycle_segment to Instantly campaign
-- Used by the rule engine when a lifecycle change doesn't specify a campaign

CREATE TABLE campaign_routing (
    lifecycle_segment  lifecycle_segment PRIMARY KEY,
    default_campaign   campaign_name
);

INSERT INTO campaign_routing (lifecycle_segment, default_campaign) VALUES
    ('cold', 'NURTURE_SLOW'),
    ('engaged', 'PROMO_STANDARD'),
    ('active_customer', 'PROMO_STANDARD'),
    ('new_customer', 'NEW_CUSTOMER_ONBOARDING'),
    ('lapsed_customer', 'PROMO_AGGRESSIVE'),
    ('reactivation_candidate', 'REACTIVATION');
-- cooling and optout intentionally omitted (no campaign = removed from all)
