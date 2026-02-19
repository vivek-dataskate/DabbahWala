-- 025_sms_templates.sql
-- SMS content templates for lifecycle-driven outreach via Telnyx
-- Each template has variants for A/B testing and personalization

SET search_path TO dabbahwala;

CREATE TABLE IF NOT EXISTS sms_templates (
    id              BIGSERIAL PRIMARY KEY,
    template_key    TEXT UNIQUE NOT NULL,
    scenario        TEXT NOT NULL,     -- lifecycle trigger
    sms_level       SMALLINT NOT NULL DEFAULT 1 CHECK (sms_level BETWEEN 1 AND 3),
    body            TEXT NOT NULL,     -- supports {{first_name}}, {{order_total}}, {{dish_name}} vars
    variant         CHAR(1) NOT NULL DEFAULT 'A',
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sms_templates_scenario ON sms_templates (scenario, sms_level, is_active);

-- =========================================================================
-- 1. NEW CUSTOMER WELCOME (after first order)
-- =========================================================================
INSERT INTO sms_templates (template_key, scenario, sms_level, body, variant) VALUES
('new_welcome_A', 'new_customer_welcome', 1,
 'Hi {{first_name}}! Welcome to DabbahWala. Your first order is being prepared fresh right now. Hope you love it! Questions? Just text us here. - DabbahWala',
 'A'),
('new_welcome_B', 'new_customer_welcome', 1,
 'Hey {{first_name}}, thanks for trying DabbahWala! Fresh home-style food, cooked just for you. We hope today''s meal makes your day. - DabbahWala',
 'B');

-- =========================================================================
-- 2. SUBSCRIPTION PITCH (day 2-3 after first order)
-- =========================================================================
INSERT INTO sms_templates (template_key, scenario, sms_level, body, variant) VALUES
('sub_pitch_A', 'subscription_pitch', 1,
 'Hi {{first_name}}, enjoyed yesterday''s meal? Our weekly subscribers save 15-20% and get priority delivery. Fresh food, zero planning. Reply SUBSCRIBE to learn more. - DabbahWala',
 'A'),
('sub_pitch_B', 'subscription_pitch', 1,
 '{{first_name}}, quick thought: our subscription customers get fresh meals daily without reordering. Less planning, more eating. Want details? Reply SUBSCRIBE. - DabbahWala',
 'B');

-- =========================================================================
-- 3. REORDER NUDGE (5-7 days after last order, no reorder)
-- =========================================================================
INSERT INTO sms_templates (template_key, scenario, sms_level, body, variant) VALUES
('reorder_nudge_A', 'reorder_nudge', 1,
 'Hi {{first_name}}, it''s been a few days since your last DabbahWala meal. Today''s menu is fresh and ready. Reply MENU to see what''s cooking! - DabbahWala',
 'A'),
('reorder_nudge_B', 'reorder_nudge', 1,
 '{{first_name}}, missing home-style food? We''ve got something good cooking today. Reply MENU for today''s options. Orders over $35 get free delivery! - DabbahWala',
 'B');

-- Level 2 (more direct, after 10-14 days)
INSERT INTO sms_templates (template_key, scenario, sms_level, body, variant) VALUES
('reorder_nudge2_A', 'reorder_nudge', 2,
 '{{first_name}}, we noticed you haven''t ordered in a while. We''d love to cook for you again. Today''s special: fresh home-style meals delivered to your door. Reply MENU. - DabbahWala',
 'A');

-- =========================================================================
-- 4. LAPSED CUSTOMER WIN-BACK (30+ days inactive)
-- =========================================================================
INSERT INTO sms_templates (template_key, scenario, sms_level, body, variant) VALUES
('lapsed_winback_A', 'lapsed_winback', 2,
 'Hi {{first_name}}, it''s been a while! We''ve made some changes at DabbahWala — fresher food, rotating menus, easier ordering. We''d love to have you back. Reply MENU to see today''s options. - DabbahWala',
 'A'),
('lapsed_winback_B', 'lapsed_winback', 2,
 '{{first_name}}, we miss cooking for you! DabbahWala has improved — cook-to-order, same-day delivery, more variety. Your next order gets special attention. Reply MENU. - DabbahWala',
 'B');

-- Level 3 (aggressive, 45+ days)
INSERT INTO sms_templates (template_key, scenario, sms_level, body, variant) VALUES
('lapsed_winback3_A', 'lapsed_winback', 3,
 '{{first_name}}, honest question — what would bring you back to DabbahWala? We''ve changed a lot and want to earn your trust again. Reply anytime, we''re listening. - DabbahWala',
 'A');

-- =========================================================================
-- 5. APP-TO-DIRECT CONVERSION (for customers who order via DoorDash/UberEats)
-- =========================================================================
INSERT INTO sms_templates (template_key, scenario, sms_level, body, variant) VALUES
('app_to_direct_A', 'app_to_direct', 1,
 'Hi {{first_name}}! We noticed you order DabbahWala through apps. Quick tip: ordering direct at dabbahwala.com saves you fees, and orders over $35 get free delivery. Same great food! - DabbahWala',
 'A'),
('app_to_direct_B', 'app_to_direct', 1,
 '{{first_name}}, thanks for ordering DabbahWala! Did you know? Order direct and skip the app fees. Plus we can personalize your order better. Visit dabbahwala.com - DabbahWala',
 'B');

-- Level 2 (after they still order via app)
INSERT INTO sms_templates (template_key, scenario, sms_level, body, variant) VALUES
('app_to_direct2_A', 'app_to_direct', 2,
 '{{first_name}}, you''ve ordered DabbahWala a few times on apps — here''s a secret: same food, lower price, faster delivery when you order direct. Just text MENU here or visit dabbahwala.com. - DabbahWala',
 'A');

-- =========================================================================
-- 6. DELIVERY CONFIRMATION
-- =========================================================================
INSERT INTO sms_templates (template_key, scenario, sms_level, body, variant) VALUES
('delivery_confirm_A', 'delivery_confirmation', 1,
 'Hi {{first_name}}, your DabbahWala meal has been delivered! Enjoy your fresh, home-style food. How was it? Reply with any feedback. - DabbahWala',
 'A');

-- =========================================================================
-- 7. ORDER CONFIRMATION
-- =========================================================================
INSERT INTO sms_templates (template_key, scenario, sms_level, body, variant) VALUES
('order_confirm_A', 'order_confirmation', 1,
 'Hi {{first_name}}, your DabbahWala order is confirmed! Fresh meals being prepared now. Delivery: {{delivery_slot}}. Track at dabbahwala.com - DabbahWala',
 'A');

-- =========================================================================
-- 8. SUBSCRIPTION RENEWAL REMINDER
-- =========================================================================
INSERT INTO sms_templates (template_key, scenario, sms_level, body, variant) VALUES
('sub_renewal_A', 'subscription_renewal', 1,
 'Hi {{first_name}}, your DabbahWala subscription renews tomorrow. Same great food, delivered fresh. Want to make any changes? Reply here or visit dabbahwala.com - DabbahWala',
 'A'),
('sub_renewal_B', 'subscription_renewal', 1,
 '{{first_name}}, heads up — your weekly meal subscription renews soon. We''re excited to keep cooking for you! Any changes? Just reply here. - DabbahWala',
 'B');

-- =========================================================================
-- 9. RETURNING CUSTOMER WELCOME-BACK
-- =========================================================================
INSERT INTO sms_templates (template_key, scenario, sms_level, body, variant) VALUES
('return_welcome_A', 'returning_customer', 1,
 'Welcome back {{first_name}}! We''re so glad you''re ordering from DabbahWala again. Your meal gets extra love from our kitchen today. Enjoy! - DabbahWala',
 'A'),
('return_welcome_B', 'returning_customer', 1,
 '{{first_name}}, you''re back! We remember you. Expect a little surprise with your order today — our way of saying thanks. - DabbahWala',
 'B');

-- =========================================================================
-- 10. FEEDBACK REQUEST (day after delivery)
-- =========================================================================
INSERT INTO sms_templates (template_key, scenario, sms_level, body, variant) VALUES
('feedback_A', 'feedback_request', 1,
 'Hi {{first_name}}, how was yesterday''s DabbahWala meal? Your feedback helps us cook better. Reply 1-5 (5=loved it!) or share any thoughts. - DabbahWala',
 'A');

-- =========================================================================
-- 11. SPECIAL OCCASION / FESTIVAL
-- =========================================================================
INSERT INTO sms_templates (template_key, scenario, sms_level, body, variant) VALUES
('festival_A', 'festival_special', 1,
 'Hi {{first_name}}, we''re preparing something special for {{occasion}}! Limited festive menu available. Order early at dabbahwala.com. - DabbahWala',
 'A');

-- =========================================================================
-- 12. REFERRAL ASK (for active customers with 5+ orders)
-- =========================================================================
INSERT INTO sms_templates (template_key, scenario, sms_level, body, variant) VALUES
('referral_A', 'referral_ask', 1,
 '{{first_name}}, you''ve been a wonderful DabbahWala customer! Know someone who''d love fresh home-style food? Share dabbahwala.com with them. They get a welcome treat, you get our thanks! - DabbahWala',
 'A');

-- Helper function: get SMS template by scenario + level
CREATE OR REPLACE FUNCTION get_sms_template(
    p_scenario TEXT,
    p_sms_level SMALLINT DEFAULT 1
)
RETURNS TABLE(template_key TEXT, body TEXT, variant CHAR(1))
LANGUAGE sql
SET search_path TO dabbahwala
AS $$
    SELECT template_key, body, variant
    FROM sms_templates
    WHERE scenario = p_scenario
      AND sms_level <= p_sms_level
      AND is_active = true
    ORDER BY random()  -- random variant for A/B testing
    LIMIT 1;
$$;
