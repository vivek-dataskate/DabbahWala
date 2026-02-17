-- 005_rules.sql
-- Rules stored as data rows with SQL predicate strings

SET search_path TO dabbahwala;

CREATE TABLE rules (
    id               SERIAL PRIMARY KEY,
    rule_name        TEXT UNIQUE NOT NULL,
    priority         INT NOT NULL,
    predicate_sql    TEXT NOT NULL,
    set_lifecycle    lifecycle_segment,
    set_email_nurture BOOLEAN,
    set_email_promo  BOOLEAN,
    set_sms_promo    BOOLEAN,
    set_sms_level    SMALLINT,
    set_campaign     campaign_name,
    set_cooling_days INT,
    is_active        BOOLEAN NOT NULL DEFAULT true,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
