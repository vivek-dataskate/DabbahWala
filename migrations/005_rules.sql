-- 005_rules.sql
-- Rules stored as data rows with SQL predicate strings
-- This is the "Inference" layer: predicates that map evidence to decisions
-- Change rules by updating rows — no code deploy needed

CREATE TABLE rules (
    id               SERIAL PRIMARY KEY,
    rule_name        TEXT UNIQUE NOT NULL,
    priority         INT NOT NULL,            -- higher number = evaluated first
    predicate_sql    TEXT NOT NULL,            -- SQL WHERE clause fragment (uses c. for contacts, r. for rollups)
    set_lifecycle    lifecycle_segment,        -- NULL = don't change
    set_email_nurture BOOLEAN,                -- NULL = don't change
    set_email_promo  BOOLEAN,                 -- NULL = don't change
    set_sms_promo    BOOLEAN,                 -- NULL = don't change
    set_sms_level    SMALLINT,                -- NULL = don't change
    set_campaign     campaign_name,            -- NULL = don't change
    set_cooling_days INT,                     -- if set, cooling_until = now() + N days
    is_active        BOOLEAN NOT NULL DEFAULT true,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
