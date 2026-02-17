-- 014_seed_campaign_routing.sql
-- Default mapping from lifecycle_segment to Instantly campaign

SET search_path TO dabbahwala;

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
