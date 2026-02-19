-- 027_agent_playbook.sql
-- User-configurable playbook for the Claude Agent.
-- Each row is a natural language instruction that gets injected into
-- Claude's system prompt when analyzing contacts.
--
-- Users can add/edit/disable rules without touching code.
-- Examples:
--   "If a customer ordered biryani 3+ times, suggest the Biryani Lover subscription"
--   "Customers in Sandy Springs area should get priority SMS since delivery is fast there"
--   "Anyone who complained about delivery should NOT get a promo — send apology first"

SET search_path TO dabbahwala;

CREATE TABLE IF NOT EXISTS agent_playbook (
    id              BIGSERIAL PRIMARY KEY,
    rule_name       TEXT NOT NULL,
    category        TEXT NOT NULL DEFAULT 'general',
        -- categories: 'inference', 'decision', 'messaging', 'exclusion', 'priority', 'general'
    instruction     TEXT NOT NULL,
        -- Natural language instruction for Claude
    priority        INT NOT NULL DEFAULT 50,
        -- Higher = more important (shown first to Claude)
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_by      TEXT DEFAULT 'system',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_playbook_active ON agent_playbook (is_active, priority DESC);
CREATE INDEX IF NOT EXISTS idx_playbook_category ON agent_playbook (category);

-- Seed with initial rules
INSERT INTO agent_playbook (rule_name, category, instruction, priority, created_by) VALUES

-- INFERENCE rules (what to look for)
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

-- DECISION rules (what to do)
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

-- MESSAGING rules (how to communicate)
('tone_and_style', 'messaging',
 'Keep all messages warm, conversational, and humble. Never sound corporate or pushy. Use the customer''s first name. Mention specific dishes they''ve ordered before. DabbahWala is a kitchen, not a corporation.',
 95, 'system'),

('sms_brevity', 'messaging',
 'SMS messages MUST be under 160 characters. No links except dabbahwala.com. Always end with "- DabbahWala". Use "Reply MENU" as CTA when appropriate.',
 90, 'system'),

('personalization_required', 'messaging',
 'Every message must reference something specific about the customer: their favorite dish, their neighborhood, how many times they''ve ordered, or when they last ordered. Generic messages are not acceptable.',
 85, 'system'),

-- EXCLUSION rules (who NOT to contact)
('recent_order_cooldown', 'exclusion',
 'Do NOT reach out to anyone who ordered in the last 2 days. Let them enjoy the food first. Exception: delivery confirmation and feedback requests are fine.',
 95, 'system'),

('complaint_handling', 'exclusion',
 'If a customer has complained in recent SMS or calls (look for negative sentiment in transcripts), do NOT send a promo. Instead, suggest a personal apology call from the team.',
 90, 'system'),

('opted_out_respect', 'exclusion',
 'Never suggest contacting someone whose lifecycle is optout or cooling. Check the cooling_until date.',
 99, 'system'),

-- PRIORITY rules (who to prioritize)
('metro_atlanta_priority', 'priority',
 'Customers in Atlanta, Marietta, Sandy Springs, Alpharetta, and Cumming are in our core delivery zone. They should get slightly higher priority since delivery is faster and cheaper.',
 60, 'system'),

('returning_customer_vip', 'priority',
 'A lapsed customer who places a new order is a VIP moment. Immediately flag as HOT. Suggest welcome-back SMS + a small kitchen surprise. This is a conversion we MUST reinforce.',
 95, 'system');
