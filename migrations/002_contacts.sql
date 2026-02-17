-- 002_contacts.sql
-- Master contacts table: single source of truth for lifecycle state + channel flags

CREATE TABLE contacts (
    id                     BIGSERIAL PRIMARY KEY,
    email                  TEXT UNIQUE,
    phone                  TEXT,
    first_name             TEXT,
    last_name              TEXT,

    -- Lifecycle (one value per contact)
    lifecycle_segment      lifecycle_segment NOT NULL DEFAULT 'cold',

    -- Channel flags (independent booleans)
    email_nurture_enabled  BOOLEAN NOT NULL DEFAULT true,
    email_promo_enabled    BOOLEAN NOT NULL DEFAULT false,
    sms_promo_enabled      BOOLEAN NOT NULL DEFAULT false,

    -- SMS pressure level (1=light, 2=stronger, 3=aggressive)
    sms_level              SMALLINT NOT NULL DEFAULT 1 CHECK (sms_level BETWEEN 1 AND 3),

    -- Current campaign assignment
    current_campaign       campaign_name,

    -- Cooling management
    cooling_until          TIMESTAMPTZ,

    -- Denormalized order tracking (fast rule evaluation)
    total_orders           INT NOT NULL DEFAULT 0,
    last_order_at          TIMESTAMPTZ,

    -- Metadata
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_contacts_lifecycle ON contacts (lifecycle_segment);
CREATE INDEX idx_contacts_campaign ON contacts (current_campaign);
CREATE INDEX idx_contacts_cooling ON contacts (cooling_until) WHERE cooling_until IS NOT NULL;
