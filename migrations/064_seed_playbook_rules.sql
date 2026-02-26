-- 063_seed_playbook_rules.sql
-- Idempotent re-seed of the agent_playbook table.
-- Adds a unique constraint on rule_name (safe if it already exists),
-- then inserts the canonical rule set with ON CONFLICT DO NOTHING so
-- existing customised rules are never overwritten.
--
-- Run this if the table was accidentally emptied or the Airtable source
-- was deleted. Categories: exclusion, priority, inference, decision, messaging
-- (categories will be renamed observer/advisor in migration 064)

SET search_path TO dabbahwala;

-- Add unique constraint on rule_name so ON CONFLICT works
-- (DROP CONSTRAINT IF NOT EXISTS is not supported; use DO block)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_playbook_rule_name'
          AND conrelid = 'agent_playbook'::regclass
    ) THEN
        ALTER TABLE agent_playbook ADD CONSTRAINT uq_playbook_rule_name UNIQUE (rule_name);
    END IF;
END $$;

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
