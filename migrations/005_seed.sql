-- 005_seed.sql
-- All seed / reference data — final consolidated state.
-- Every INSERT uses ON CONFLICT DO NOTHING or ON CONFLICT DO UPDATE so this
-- is safe to re-run without duplicating rows.
--
-- Sections:
--   1. rules             (7 lifecycle rules)
--   2. campaign_routing  (6 segment → campaign mappings + Instantly IDs)
--   3. sms_templates     (12 scenario / variant sets)
--   4. agent_playbook    (17 canonical playbook rules)

SET search_path TO dabbahwala;

-- ===========================================================================
-- 1. RULES — lifecycle decision engine
-- ===========================================================================

INSERT INTO rules (rule_name, priority, predicate_sql, set_lifecycle, set_email_nurture, set_email_promo, set_sms_promo, set_sms_level, set_campaign, set_cooling_days)
VALUES

('optout', 70,
 'c.lifecycle_segment = ''optout''',
 'optout', false, false, false, NULL, NULL, NULL),

('fatigue', 60,
 'r.sms_sent_7d >= 2 AND r.clicks_7d = 0 AND c.lifecycle_segment != ''cooling''',
 'cooling', NULL, NULL, false, NULL, NULL, 14),

('first_order', 50,
 'c.total_orders = 1 AND c.last_order_at > now() - interval ''7 days''',
 'new_customer', NULL, false, false, NULL, 'NEW_CUSTOMER_ONBOARDING', NULL),

('active_customer', 40,
 'c.last_order_at > now() - interval ''14 days'' AND c.total_orders > 1',
 'active_customer', NULL, NULL, NULL, 1, 'PROMO_STANDARD', NULL),

('lapsed', 30,
 'c.last_order_at BETWEEN now() - interval ''29 days'' AND now() - interval ''14 days''',
 'lapsed_customer', NULL, true, NULL, NULL, 'PROMO_AGGRESSIVE', NULL),

('reactivation', 20,
 'c.last_order_at < now() - interval ''30 days'' AND c.last_order_at IS NOT NULL',
 'reactivation_candidate', NULL, true, true, 2, 'REACTIVATION', NULL),

('any_open', 10,
 'r.opens_7d >= 1 AND c.lifecycle_segment = ''cold''',
 'engaged', NULL, true, true, NULL, 'PROMO_STANDARD', NULL)

ON CONFLICT (rule_name) DO NOTHING;

-- ===========================================================================
-- 2. CAMPAIGN ROUTING — lifecycle_segment → Instantly campaign mapping
-- ===========================================================================

INSERT INTO campaign_routing (lifecycle_segment, default_campaign, instantly_campaign_id, instantly_campaign_name, template_file)
VALUES
    ('cold',                   'NURTURE_SLOW',            '76a88797-961a-47b6-af11-77e2211c4e73', 'DW-NurtureSlow-ColdContacts',          'nurture_slow.json'),
    ('engaged',                'PROMO_STANDARD',          'f3e2d621-9bf2-4130-bc1c-f8168fc44e1e', 'DW-PromoStandard-ActiveEngaged',        'promo_standard.json'),
    ('active_customer',        'ACTIVE_CUSTOMER',         'c763e229-f633-468b-bfe4-7f9a4fd21036', 'DW-ActiveCustomer',                     'promo_standard.json'),
    ('new_customer',           'NEW_CUSTOMER_ONBOARDING', '8a5ccbfb-500d-4060-ad99-76aa0159bbf2', 'DW-NewCustomerOnboarding',              'new_customer_onboarding.json'),
    ('lapsed_customer',        'PROMO_AGGRESSIVE',        '87d44ff1-8720-4c1d-92ff-b827970f323f', 'DW-PromoAggressive-LapsedCustomers',    'promo_aggressive.json'),
    ('reactivation_candidate', 'REACTIVATION',            '69c84455-d9b8-437f-b249-8325d23798e6', 'DW-Reactivation-LongDormant',           'reactivation.json')

ON CONFLICT (lifecycle_segment) DO UPDATE
    SET default_campaign        = EXCLUDED.default_campaign,
        instantly_campaign_id   = EXCLUDED.instantly_campaign_id,
        instantly_campaign_name = EXCLUDED.instantly_campaign_name,
        template_file           = EXCLUDED.template_file;

-- ===========================================================================
-- 3. SMS TEMPLATES
-- ===========================================================================

INSERT INTO sms_templates (template_key, scenario, sms_level, body, variant) VALUES

-- 1. New customer welcome
('new_welcome_A', 'new_customer_welcome', 1,
 'Hi {{first_name}}! Welcome to DabbahWala. Your first order is being prepared fresh right now. Hope you love it! Questions? Just text us here. - DabbahWala',
 'A'),
('new_welcome_B', 'new_customer_welcome', 1,
 'Hey {{first_name}}, thanks for trying DabbahWala! Fresh home-style food, cooked just for you. We hope today''s meal makes your day. - DabbahWala',
 'B'),

-- 2. Subscription pitch
('sub_pitch_A', 'subscription_pitch', 1,
 'Hi {{first_name}}, enjoyed yesterday''s meal? Our weekly subscribers save 15-20% and get priority delivery. Fresh food, zero planning. Reply SUBSCRIBE to learn more. - DabbahWala',
 'A'),
('sub_pitch_B', 'subscription_pitch', 1,
 '{{first_name}}, quick thought: our subscription customers get fresh meals daily without reordering. Less planning, more eating. Want details? Reply SUBSCRIBE. - DabbahWala',
 'B'),

-- 3. Reorder nudge — level 1
('reorder_nudge_A', 'reorder_nudge', 1,
 'Hi {{first_name}}, it''s been a few days since your last DabbahWala meal. Today''s menu is fresh and ready. Reply MENU to see what''s cooking! - DabbahWala',
 'A'),
('reorder_nudge_B', 'reorder_nudge', 1,
 '{{first_name}}, missing home-style food? We''ve got something good cooking today. Reply MENU for today''s options. Orders over $35 get free delivery! - DabbahWala',
 'B'),

-- 3. Reorder nudge — level 2
('reorder_nudge2_A', 'reorder_nudge', 2,
 '{{first_name}}, we noticed you haven''t ordered in a while. We''d love to cook for you again. Today''s special: fresh home-style meals delivered to your door. Reply MENU. - DabbahWala',
 'A'),

-- 4. Lapsed win-back — level 2
('lapsed_winback_A', 'lapsed_winback', 2,
 'Hi {{first_name}}, it''s been a while! We''ve made some changes at DabbahWala — fresher food, rotating menus, easier ordering. We''d love to have you back. Reply MENU to see today''s options. - DabbahWala',
 'A'),
('lapsed_winback_B', 'lapsed_winback', 2,
 '{{first_name}}, we miss cooking for you! DabbahWala has improved — cook-to-order, same-day delivery, more variety. Your next order gets special attention. Reply MENU. - DabbahWala',
 'B'),

-- 4. Lapsed win-back — level 3
('lapsed_winback3_A', 'lapsed_winback', 3,
 '{{first_name}}, honest question — what would bring you back to DabbahWala? We''ve changed a lot and want to earn your trust again. Reply anytime, we''re listening. - DabbahWala',
 'A'),

-- 5. App-to-direct conversion — level 1
('app_to_direct_A', 'app_to_direct', 1,
 'Hi {{first_name}}! We noticed you order DabbahWala through apps. Quick tip: ordering direct at dabbahwala.com saves you fees, and orders over $35 get free delivery. Same great food! - DabbahWala',
 'A'),
('app_to_direct_B', 'app_to_direct', 1,
 '{{first_name}}, thanks for ordering DabbahWala! Did you know? Order direct and skip the app fees. Plus we can personalize your order better. Visit dabbahwala.com - DabbahWala',
 'B'),

-- 5. App-to-direct conversion — level 2
('app_to_direct2_A', 'app_to_direct', 2,
 '{{first_name}}, you''ve ordered DabbahWala a few times on apps — here''s a secret: same food, lower price, faster delivery when you order direct. Just text MENU here or visit dabbahwala.com. - DabbahWala',
 'A'),

-- 6. Delivery confirmation
('delivery_confirm_A', 'delivery_confirmation', 1,
 'Hi {{first_name}}, your DabbahWala meal has been delivered! Enjoy your fresh, home-style food. How was it? Reply with any feedback. - DabbahWala',
 'A'),

-- 7. Order confirmation
('order_confirm_A', 'order_confirmation', 1,
 'Hi {{first_name}}, your DabbahWala order is confirmed! Fresh meals being prepared now. Delivery: {{delivery_slot}}. Track at dabbahwala.com - DabbahWala',
 'A'),

-- 8. Subscription renewal reminder
('sub_renewal_A', 'subscription_renewal', 1,
 'Hi {{first_name}}, your DabbahWala subscription renews tomorrow. Same great food, delivered fresh. Want to make any changes? Reply here or visit dabbahwala.com - DabbahWala',
 'A'),
('sub_renewal_B', 'subscription_renewal', 1,
 '{{first_name}}, heads up — your weekly meal subscription renews soon. We''re excited to keep cooking for you! Any changes? Just reply here. - DabbahWala',
 'B'),

-- 9. Returning customer welcome-back
('return_welcome_A', 'returning_customer', 1,
 'Welcome back {{first_name}}! We''re so glad you''re ordering from DabbahWala again. Your meal gets extra love from our kitchen today. Enjoy! - DabbahWala',
 'A'),
('return_welcome_B', 'returning_customer', 1,
 '{{first_name}}, you''re back! We remember you. Expect a little surprise with your order today — our way of saying thanks. - DabbahWala',
 'B'),

-- 10. Feedback request
('feedback_A', 'feedback_request', 1,
 'Hi {{first_name}}, how was yesterday''s DabbahWala meal? Your feedback helps us cook better. Reply 1-5 (5=loved it!) or share any thoughts. - DabbahWala',
 'A'),

-- 11. Festival special
('festival_A', 'festival_special', 1,
 'Hi {{first_name}}, we''re preparing something special for {{occasion}}! Limited festive menu available. Order early at dabbahwala.com. - DabbahWala',
 'A'),

-- 12. Referral ask
('referral_A', 'referral_ask', 1,
 '{{first_name}}, you''ve been a wonderful DabbahWala customer! Know someone who''d love fresh home-style food? Share dabbahwala.com with them. They get a welcome treat, you get our thanks! - DabbahWala',
 'A')

ON CONFLICT (template_key) DO NOTHING;

-- ===========================================================================
-- 4. AGENT PLAYBOOK — canonical rules (17 rows)
-- Unique constraint on rule_name is defined in 002_tables.sql.
-- ON CONFLICT DO NOTHING preserves any customised live rules.
-- ===========================================================================

INSERT INTO agent_playbook (rule_name, category, instruction, priority, created_by)
VALUES

-- ── EXCLUSION (safety-first — injected first into every prompt) ─────────────
('recent_order_cooldown', 'exclusion',
 'Do NOT reach out to anyone who ordered in the last 2 days. Let them enjoy the food first. Exception: delivery confirmation and feedback requests are fine.',
 95, 'system'),

('complaint_handling', 'exclusion',
 'If a customer has complained in recent SMS or calls (look for negative sentiment in transcripts), do NOT send a promo. Instead, suggest a personal apology call from the team.',
 90, 'system'),

('opted_out_respect', 'exclusion',
 'Never suggest contacting someone whose lifecycle is optout or cooling. Check the cooling_until date.',
 99, 'system'),

-- ── PRIORITY (who to focus on first) ────────────────────────────────────────
('metro_atlanta_priority', 'priority',
 'Customers in Atlanta, Marietta, Sandy Springs, Alpharetta, and Cumming are in our core delivery zone. They should get slightly higher priority since delivery is faster and cheaper.',
 60, 'system'),

('returning_customer_vip', 'priority',
 'A lapsed customer who places a new order is a VIP moment. Immediately flag as HOT. Suggest welcome-back SMS + a small kitchen surprise. This is a conversion we MUST reinforce.',
 95, 'system'),

-- ── INFERENCE / OBSERVER (what signals to detect) ───────────────────────────
('biryani_lover', 'inference',
 'If a customer ordered biryani or dum biryani 3+ times, they are a "Biryani Lover". Suggest a weekly biryani subscription.',
 80, 'system'),

('thali_subscriber_potential', 'inference',
 'Customers ordering Single''s Thali or Couples Thali regularly (2+ times) are strong subscription candidates. Emphasize convenience and savings.',
 80, 'system'),

('weekend_only_customer', 'inference',
 'If a customer only orders on weekends, note this. They might benefit from a weekday trial offer or a "try a weekday delivery" nudge.',
 60, 'system'),

('high_ticket_customer', 'inference',
 'Customers with average order value over $40 are high-value. Prioritize retention. If they go quiet for 10+ days, flag as HOT priority.',
 90, 'system'),

('family_size_detector', 'inference',
 'If orders include Family Thali, Couples Thali, or qty 2+ of multiple items, this is likely a family. Suggest family-sized plans and mention kid-friendly options.',
 70, 'system'),

-- ── DECISION / ADVISOR (what actions to recommend) ──────────────────────────
('app_to_direct_conversion', 'decision',
 'App customers (DoorDash, Uber Eats, Grubhub) who have ordered 2+ times via app should be converted to direct ordering. Lead with savings (no app fees, free delivery over $35). This is a TOP PRIORITY business goal.',
 95, 'system'),

('subscription_upsell_timing', 'decision',
 'Best time to pitch subscription is after 2nd order when they already trust the food quality. Do NOT pitch subscription on the first order — let them enjoy the food first.',
 85, 'system'),

('lapsed_customer_approach', 'decision',
 'For lapsed customers (30+ days inactive), do NOT start with a discount. Instead, lead with what has changed (fresher food, new menu items, easier ordering). Only mention discounts if they have been contacted once already without response.',
 80, 'system'),

('sales_call_threshold', 'decision',
 'Only recommend a sales call for: (1) high-value customers at risk (5+ orders, going quiet), (2) lapsed customers who re-engaged via email, or (3) customers who expressed reorder intent in a call. Do NOT recommend sales calls for cold contacts.',
 90, 'system'),

-- ── MESSAGING (tone and format rules) ───────────────────────────────────────
('tone_and_style', 'messaging',
 'Keep all messages warm, conversational, and humble. Never sound corporate or pushy. Use the customer''s first name. Mention specific dishes they''ve ordered before. DabbahWala is a kitchen, not a corporation.',
 95, 'system'),

('sms_brevity', 'messaging',
 'SMS messages MUST be under 160 characters. No links except dabbahwala.com. Always end with "- DabbahWala". Use "Reply MENU" as CTA when appropriate.',
 90, 'system'),

('personalization_required', 'messaging',
 'Every message must reference something specific about the customer: their favorite dish, their neighborhood, how many times they''ve ordered, or when they last ordered. Generic messages are not acceptable.',
 85, 'system')

ON CONFLICT (rule_name) DO NOTHING;
