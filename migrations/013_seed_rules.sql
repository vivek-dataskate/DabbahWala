-- 013_seed_rules.sql
-- The 7 base lifecycle rules as data rows
-- Higher priority number = evaluated first (optout always wins)
-- Predicates use c. for contacts table, r. for engagement_rollups

INSERT INTO rules (rule_name, priority, predicate_sql, set_lifecycle, set_email_nurture, set_email_promo, set_sms_promo, set_sms_level, set_campaign, set_cooling_days) VALUES

-- Rule 7: Optout (highest priority — always wins)
('optout', 70,
 'c.lifecycle_segment = ''optout''',
 'optout', false, false, false, NULL, NULL, NULL),

-- Rule 6: Fatigue detection
('fatigue', 60,
 'r.sms_sent_7d >= 2 AND r.clicks_7d = 0 AND c.lifecycle_segment != ''cooling''',
 'cooling', NULL, NULL, false, NULL, NULL, 14),

-- Rule 2: First order (new customer onboarding)
('first_order', 50,
 'c.total_orders = 1 AND c.last_order_at > now() - interval ''7 days''',
 'new_customer', NULL, false, false, NULL, 'NEW_CUSTOMER_ONBOARDING', NULL),

-- Rule 3: Active customer
('active_customer', 40,
 'c.last_order_at > now() - interval ''14 days'' AND c.total_orders > 1',
 'active_customer', NULL, NULL, NULL, 1, 'PROMO_STANDARD', NULL),

-- Rule 4: Lapsed customer
('lapsed', 30,
 'c.last_order_at BETWEEN now() - interval ''29 days'' AND now() - interval ''14 days''',
 'lapsed_customer', NULL, true, NULL, NULL, 'PROMO_AGGRESSIVE', NULL),

-- Rule 5: Reactivation candidate
('reactivation', 20,
 'c.last_order_at < now() - interval ''30 days'' AND c.last_order_at IS NOT NULL',
 'reactivation_candidate', NULL, true, true, 2, 'REACTIVATION', NULL),

-- Rule 1: Any open (cold → engaged)
('any_open', 10,
 'r.opens_7d >= 1 AND c.lifecycle_segment = ''cold''',
 'engaged', NULL, true, true, NULL, 'PROMO_STANDARD', NULL);
