-- 062_single_source_campaign_truth.sql
-- Consolidate campaign data into campaign_routing as the single source of truth.
--
-- Changes:
--   1. Add stats + template_file columns to campaign_routing
--   2. Seed template_file values
--   3. Migrate stats from instantly_campaigns into campaign_routing
--   4. Drop contacts.current_campaign (derived from lifecycle_segment via campaign_routing)
--   5. Drop instantly_campaigns table
--   6. Rewrite stored procs that referenced contacts.current_campaign to use JOIN

SET search_path TO dabbahwala;

-- ── 1. Extend campaign_routing ───────────────────────────────────────────────

ALTER TABLE campaign_routing
    ADD COLUMN IF NOT EXISTS template_file    TEXT,
    ADD COLUMN IF NOT EXISTS leads_count      INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS emails_sent      INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS unique_opens     INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS opens            INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS replies          INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS clicks           INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS bounces          INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS unsubscribes     INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS open_rate        NUMERIC(5,2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS reply_rate       NUMERIC(5,2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS stats_synced_at  TIMESTAMPTZ;

-- ── 2. Seed template_file ────────────────────────────────────────────────────

UPDATE campaign_routing SET template_file = 'nurture_slow.json'            WHERE default_campaign = 'NURTURE_SLOW';
UPDATE campaign_routing SET template_file = 'promo_standard.json'          WHERE default_campaign = 'PROMO_STANDARD';
UPDATE campaign_routing SET template_file = 'promo_standard.json'          WHERE default_campaign = 'ACTIVE_CUSTOMER';
UPDATE campaign_routing SET template_file = 'promo_aggressive.json'        WHERE default_campaign = 'PROMO_AGGRESSIVE';
UPDATE campaign_routing SET template_file = 'new_customer_onboarding.json' WHERE default_campaign = 'NEW_CUSTOMER_ONBOARDING';
UPDATE campaign_routing SET template_file = 'reactivation.json'            WHERE default_campaign = 'REACTIVATION';

-- ── 3. Migrate stats from instantly_campaigns ────────────────────────────────

UPDATE campaign_routing cr
SET leads_count      = COALESCE(ic.leads_count,  0),
    emails_sent      = COALESCE(ic.emails_sent,  0),
    unique_opens     = COALESCE(ic.unique_opens, 0),
    opens            = COALESCE(ic.opens,        0),
    replies          = COALESCE(ic.replies,      0),
    clicks           = COALESCE(ic.clicks,       0),
    bounces          = COALESCE(ic.bounces,      0),
    unsubscribes     = COALESCE(ic.unsubscribes, 0),
    open_rate        = COALESCE(ic.open_rate,    0),
    reply_rate       = COALESCE(ic.reply_rate,   0),
    stats_synced_at  = ic.stats_synced_at
FROM instantly_campaigns ic
WHERE cr.instantly_campaign_id = ic.campaign_id
  AND ic.leads_count IS NOT NULL;

-- ── 4. Drop contacts.current_campaign ───────────────────────────────────────

ALTER TABLE contacts DROP COLUMN IF EXISTS current_campaign;

-- ── 5. Drop instantly_campaigns ──────────────────────────────────────────────

DROP TABLE IF EXISTS instantly_campaigns;

-- ── 6a. Rewrite evaluate_rules() ─────────────────────────────────────────────
-- Derive prev_campaign from lifecycle_segment via campaign_routing.
-- Remove UPDATE contacts SET current_campaign.

CREATE OR REPLACE FUNCTION evaluate_rules(p_contact_id BIGINT DEFAULT NULL)
RETURNS INT
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_rule          RECORD;
    v_contact       RECORD;
    v_matched       BOOLEAN;
    v_changes       JSONB;
    v_prev_lifecycle lifecycle_segment;
    v_prev_campaign  campaign_name;
    v_new_campaign   campaign_name;
    v_updated_count  INT := 0;
BEGIN
    -- Auto-clear expired cooling states
    UPDATE contacts
    SET lifecycle_segment = 'cold',
        cooling_until     = NULL,
        updated_at        = now()
    WHERE lifecycle_segment = 'cooling'
      AND cooling_until IS NOT NULL
      AND cooling_until < now()
      AND (p_contact_id IS NULL OR id = p_contact_id);

    FOR v_contact IN
        SELECT c.id, c.email, c.lifecycle_segment, c.email_nurture_enabled,
               c.email_promo_enabled, c.sms_promo_enabled, c.sms_level,
               c.cooling_until, c.total_orders, c.last_order_at,
               COALESCE(r.opens_7d,      0) AS opens_7d,
               COALESCE(r.clicks_7d,     0) AS clicks_7d,
               COALESCE(r.sms_sent_7d,   0) AS sms_sent_7d,
               COALESCE(r.sms_clicks_7d, 0) AS sms_clicks_7d,
               COALESCE(r.orders_7d,     0) AS orders_7d
        FROM contacts c
        LEFT JOIN engagement_rollups r ON r.contact_id = c.id
        WHERE c.lifecycle_segment != 'optout'
          AND (p_contact_id IS NULL OR c.id = p_contact_id)
    LOOP
        -- Derive current campaign from lifecycle_segment (single source of truth)
        SELECT default_campaign INTO v_prev_campaign
        FROM campaign_routing
        WHERE lifecycle_segment = v_contact.lifecycle_segment;

        FOR v_rule IN
            SELECT * FROM rules WHERE is_active = true ORDER BY priority DESC
        LOOP
            EXECUTE format(
                'SELECT EXISTS(SELECT 1 FROM dabbahwala.contacts c '
                'LEFT JOIN dabbahwala.engagement_rollups r ON r.contact_id = c.id '
                'WHERE c.id = $1 AND (%s))',
                v_rule.predicate_sql
            ) INTO v_matched USING v_contact.id;

            IF v_matched THEN
                v_prev_lifecycle := v_contact.lifecycle_segment;
                v_changes := '{}'::jsonb;

                IF v_rule.set_lifecycle IS NOT NULL AND v_rule.set_lifecycle != v_contact.lifecycle_segment THEN
                    v_changes := v_changes || jsonb_build_object(
                        'lifecycle_segment', jsonb_build_object(
                            'from', v_contact.lifecycle_segment::text,
                            'to',   v_rule.set_lifecycle::text)
                    );
                END IF;

                IF v_rule.set_email_nurture IS NOT NULL THEN
                    v_changes := v_changes || jsonb_build_object('email_nurture_enabled', v_rule.set_email_nurture);
                END IF;
                IF v_rule.set_email_promo IS NOT NULL THEN
                    v_changes := v_changes || jsonb_build_object('email_promo_enabled', v_rule.set_email_promo);
                END IF;
                IF v_rule.set_sms_promo IS NOT NULL THEN
                    v_changes := v_changes || jsonb_build_object('sms_promo_enabled', v_rule.set_sms_promo);
                END IF;
                IF v_rule.set_sms_level IS NOT NULL THEN
                    v_changes := v_changes || jsonb_build_object('sms_level', v_rule.set_sms_level);
                END IF;

                IF v_changes != '{}'::jsonb OR v_rule.set_cooling_days IS NOT NULL THEN
                    UPDATE contacts SET
                        lifecycle_segment     = COALESCE(v_rule.set_lifecycle, lifecycle_segment),
                        email_nurture_enabled = COALESCE(v_rule.set_email_nurture, email_nurture_enabled),
                        email_promo_enabled   = COALESCE(v_rule.set_email_promo, email_promo_enabled),
                        sms_promo_enabled     = COALESCE(v_rule.set_sms_promo, sms_promo_enabled),
                        sms_level             = COALESCE(v_rule.set_sms_level, sms_level),
                        cooling_until         = CASE
                                                  WHEN v_rule.set_cooling_days IS NOT NULL
                                                  THEN now() + (v_rule.set_cooling_days || ' days')::interval
                                                  ELSE cooling_until
                                                END,
                        updated_at            = now()
                    WHERE id = v_contact.id;

                    -- New campaign is always derived from the new lifecycle via routing table
                    IF v_rule.set_lifecycle IS NOT NULL THEN
                        SELECT default_campaign INTO v_new_campaign
                        FROM campaign_routing
                        WHERE campaign_routing.lifecycle_segment = v_rule.set_lifecycle;
                    ELSE
                        v_new_campaign := v_prev_campaign;
                    END IF;

                    IF v_new_campaign IS DISTINCT FROM v_prev_campaign THEN
                        INSERT INTO campaign_queue (contact_id, from_campaign, to_campaign)
                        VALUES (v_contact.id, v_prev_campaign, v_new_campaign);

                        v_changes := v_changes || jsonb_build_object(
                            'campaign', jsonb_build_object(
                                'from', v_prev_campaign::text,
                                'to',   v_new_campaign::text)
                        );
                    END IF;

                    INSERT INTO decision_log (contact_id, rule_id, prev_lifecycle, new_lifecycle, changes_applied)
                    VALUES (
                        v_contact.id,
                        v_rule.id,
                        v_prev_lifecycle,
                        COALESCE(v_rule.set_lifecycle, v_prev_lifecycle),
                        v_changes
                    );

                    v_updated_count := v_updated_count + 1;
                END IF;

                EXIT; -- first matching rule wins
            END IF;
        END LOOP;
    END LOOP;

    RETURN v_updated_count;
END;
$$;

-- ── 6b. Rewrite ingest_event() ────────────────────────────────────────────────
-- Remove current_campaign = NULL from optout update (column no longer exists).

CREATE OR REPLACE FUNCTION ingest_event(
    p_contact_email TEXT,
    p_event_type    event_type,
    p_metadata      JSONB DEFAULT '{}'
)
RETURNS BIGINT
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_contact_id BIGINT;
    v_event_id   BIGINT;
BEGIN
    SELECT id INTO v_contact_id FROM contacts WHERE email = p_contact_email;

    IF v_contact_id IS NULL THEN
        RAISE EXCEPTION 'Contact not found: %', p_contact_email;
    END IF;

    INSERT INTO events (contact_id, event_type, metadata, occurred_at)
    VALUES (v_contact_id, p_event_type, p_metadata, now())
    RETURNING id INTO v_event_id;

    IF p_event_type = 'order_placed' THEN
        UPDATE contacts SET
            total_orders  = total_orders + 1,
            last_order_at = now(),
            updated_at    = now()
        WHERE id = v_contact_id;
    END IF;

    IF p_event_type IN ('unsubscribe', 'sms_stop') THEN
        UPDATE contacts SET
            lifecycle_segment     = 'optout',
            email_nurture_enabled = false,
            email_promo_enabled   = false,
            sms_promo_enabled     = false,
            updated_at            = now()
        WHERE id = v_contact_id
          AND lifecycle_segment != 'optout';

        INSERT INTO decision_log (contact_id, rule_id, prev_lifecycle, new_lifecycle, changes_applied)
        SELECT v_contact_id, r.id,
               (SELECT lifecycle_segment FROM contacts WHERE id = v_contact_id),
               'optout',
               '{"reason": "direct_optout_event"}'::jsonb
        FROM rules r WHERE r.rule_name = 'optout'
        LIMIT 1;
    END IF;

    RETURN v_event_id;
END;
$$;

-- ── 6c. Rewrite get_campaign_performance() ────────────────────────────────────

CREATE OR REPLACE FUNCTION get_campaign_performance(p_campaign campaign_name, p_days INT DEFAULT 7)
RETURNS JSONB
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_contact_count BIGINT;
    v_activity      JSONB;
BEGIN
    SELECT count(*) INTO v_contact_count
    FROM contacts c
    JOIN campaign_routing cr ON cr.lifecycle_segment = c.lifecycle_segment
    WHERE cr.default_campaign = p_campaign;

    SELECT COALESCE(jsonb_object_agg(event_type::text, cnt), '{}'::jsonb) INTO v_activity
    FROM (
        SELECT e.event_type, count(*) AS cnt
        FROM events e
        JOIN contacts c ON c.id = e.contact_id
        JOIN campaign_routing cr ON cr.lifecycle_segment = c.lifecycle_segment
        WHERE cr.default_campaign = p_campaign
          AND e.occurred_at > now() - (p_days || ' days')::interval
        GROUP BY e.event_type
    ) t;

    RETURN jsonb_build_object(
        'campaign', p_campaign::text,
        'days',     p_days,
        'contacts_in_campaign', v_contact_count,
        'activity', v_activity
    );
END;
$$;

-- ── 6d. Rewrite get_order_attribution() ──────────────────────────────────────

CREATE OR REPLACE FUNCTION get_order_attribution(p_days_lookback INT DEFAULT 7)
RETURNS JSONB
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_result JSONB;
BEGIN
    SELECT row_to_json(t)::jsonb INTO v_result
    FROM (
        WITH orders AS (
            SELECT e.id AS order_event_id, e.contact_id, e.occurred_at AS order_at,
                   c.email, cr.default_campaign AS current_campaign
            FROM events e
            JOIN contacts c ON c.id = e.contact_id
            LEFT JOIN campaign_routing cr ON cr.lifecycle_segment = c.lifecycle_segment
            WHERE e.event_type = 'order_placed'
              AND e.occurred_at > now() - interval '30 days'
        ),
        attributed AS (
            SELECT o.*,
                   (SELECT e2.event_type FROM events e2
                    WHERE e2.contact_id = o.contact_id
                      AND e2.event_type IN ('email_open', 'email_click', 'sms_click')
                      AND e2.occurred_at BETWEEN o.order_at - (p_days_lookback || ' days')::interval
                                             AND o.order_at
                    ORDER BY e2.occurred_at DESC LIMIT 1
                   ) AS attributed_touch
            FROM orders o
        )
        SELECT
            count(*) AS total_orders,
            count(attributed_touch) AS attributed_orders,
            count(*) - count(attributed_touch) AS unattributed_orders
        FROM attributed
    ) t;

    RETURN COALESCE(v_result, '{}'::jsonb);
END;
$$;

-- ── 6e. Rewrite search_contacts() ─────────────────────────────────────────────
-- Return current_campaign derived via JOIN instead of contacts.current_campaign.

DROP FUNCTION IF EXISTS search_contacts(TEXT, BOOLEAN, BOOLEAN, INT, INT, INT);

CREATE OR REPLACE FUNCTION search_contacts(
    p_lifecycle_segment   TEXT    DEFAULT NULL,
    p_email_promo_enabled BOOLEAN DEFAULT NULL,
    p_sms_promo_enabled   BOOLEAN DEFAULT NULL,
    p_min_orders          INT     DEFAULT NULL,
    p_max_orders          INT     DEFAULT NULL,
    p_limit               INT     DEFAULT 50
)
RETURNS TABLE(
    id                   BIGINT,
    email                TEXT,
    first_name           TEXT,
    last_name            TEXT,
    lifecycle_segment    lifecycle_segment,
    email_promo_enabled  BOOLEAN,
    sms_promo_enabled    BOOLEAN,
    sms_level            SMALLINT,
    current_campaign     campaign_name,
    total_orders         INT,
    last_order_at        TIMESTAMPTZ
)
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
BEGIN
    RETURN QUERY
    SELECT c.id, c.email, c.first_name, c.last_name,
           c.lifecycle_segment, c.email_promo_enabled,
           c.sms_promo_enabled, c.sms_level,
           cr.default_campaign AS current_campaign,
           c.total_orders, c.last_order_at
    FROM contacts c
    LEFT JOIN campaign_routing cr ON cr.lifecycle_segment = c.lifecycle_segment
    WHERE (p_lifecycle_segment   IS NULL OR c.lifecycle_segment     = p_lifecycle_segment::lifecycle_segment)
      AND (p_email_promo_enabled IS NULL OR c.email_promo_enabled   = p_email_promo_enabled)
      AND (p_sms_promo_enabled   IS NULL OR c.sms_promo_enabled     = p_sms_promo_enabled)
      AND (p_min_orders          IS NULL OR c.total_orders          >= p_min_orders)
      AND (p_max_orders          IS NULL OR c.total_orders          <= p_max_orders)
    ORDER BY c.updated_at DESC
    LIMIT p_limit;
END;
$$;

-- ── 6f. Rewrite suggest_reactivation_targets() ───────────────────────────────

DROP FUNCTION IF EXISTS suggest_reactivation_targets(INT);

CREATE OR REPLACE FUNCTION suggest_reactivation_targets(p_limit INT DEFAULT 20)
RETURNS TABLE(
    id                  BIGINT,
    email               TEXT,
    first_name          TEXT,
    last_name           TEXT,
    phone               TEXT,
    lifecycle_segment   lifecycle_segment,
    total_orders        INT,
    last_order_at       TIMESTAMPTZ,
    sms_level           SMALLINT,
    current_campaign    campaign_name,
    opens_7d            INT,
    clicks_7d           INT,
    sms_clicks_7d       INT,
    reactivation_score  NUMERIC
)
LANGUAGE sql
SET search_path TO dabbahwala
AS $$
    SELECT c.id, c.email, c.first_name, c.last_name, c.phone,
           c.lifecycle_segment, c.total_orders, c.last_order_at,
           c.sms_level,
           cr.default_campaign AS current_campaign,
           r.opens_7d, r.clicks_7d, r.sms_clicks_7d,
           (COALESCE(r.opens_7d,      0) * 1
          + COALESCE(r.clicks_7d,     0) * 3
          + COALESCE(r.sms_clicks_7d, 0) * 2
          + c.total_orders * 2)::numeric AS reactivation_score
    FROM contacts c
    LEFT JOIN engagement_rollups r  ON r.contact_id     = c.id
    LEFT JOIN campaign_routing   cr ON cr.lifecycle_segment = c.lifecycle_segment
    WHERE c.lifecycle_segment IN ('lapsed_customer', 'reactivation_candidate')
    ORDER BY reactivation_score DESC, c.last_order_at DESC
    LIMIT p_limit;
$$;
