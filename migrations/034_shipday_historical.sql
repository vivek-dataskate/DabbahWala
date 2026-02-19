-- Migration 034: Shipday Historical Data Import
-- -----------------------------------------------
-- Adds:
-- 1. shipday_orders_raw — stores raw Shipday API responses for last 1 year
-- 2. agent_signal_config — user-configurable signal thresholds (dynamic agents)
-- 3. get_top_reorder_candidates() — identifies 10 people most worth calling
-- 4. get_contacts_needing_call() — contacts with high intent + no recent action

SET search_path TO dabbahwala;

-- ─────────────────────────────────────────────────────────────────
-- 1. Raw Shipday orders table — for historical bulk import
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS shipday_orders_raw (
    id                  BIGSERIAL PRIMARY KEY,
    shipday_order_id    TEXT NOT NULL UNIQUE,
    order_number        TEXT,
    contact_id          BIGINT REFERENCES contacts(id) ON DELETE SET NULL,
    customer_email      TEXT,
    customer_phone      TEXT,
    customer_name       TEXT,
    delivery_address    TEXT,
    shipday_status      TEXT,          -- ACCEPTED, ASSIGNED, PICKED_UP, COMPLETED, FAILED, RETURNED
    driver_name         TEXT,
    driver_phone        TEXT,
    estimated_delivery  TIMESTAMPTZ,
    actual_delivery     TIMESTAMPTZ,
    order_created_at    TIMESTAMPTZ,
    synced_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_payload         JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_shipday_raw_contact    ON shipday_orders_raw(contact_id);
CREATE INDEX IF NOT EXISTS idx_shipday_raw_email      ON shipday_orders_raw(customer_email);
CREATE INDEX IF NOT EXISTS idx_shipday_raw_status     ON shipday_orders_raw(shipday_status);
CREATE INDEX IF NOT EXISTS idx_shipday_raw_created    ON shipday_orders_raw(order_created_at DESC);

-- ─────────────────────────────────────────────────────────────────
-- 2. Agent signal configuration — makes inference agents dynamic
--    Users can adjust thresholds without redeploying code
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_signal_config (
    id              SERIAL PRIMARY KEY,
    signal_key      TEXT NOT NULL UNIQUE,   -- e.g. 'lapsed_days', 'high_value_threshold_aed'
    signal_value    TEXT NOT NULL,          -- stored as text, parsed in Python
    description     TEXT,
    category        TEXT DEFAULT 'threshold',  -- threshold, flag, window
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Default signal thresholds — users can update these via admin UI or Airtable
INSERT INTO agent_signal_config (signal_key, signal_value, description, category) VALUES
    ('lapsed_days',              '45',    'Days since last order to classify as lapsed',           'threshold'),
    ('engaged_opens_min',        '2',     'Minimum opens_7d to qualify as "engaged" signal',        'threshold'),
    ('high_value_aed',           '200',   'Minimum total spend to be "high value" customer',        'threshold'),
    ('reactivation_window_days', '90',    'Days window to look for lapsed-then-reengaged signal',   'window'),
    ('call_priority_threshold',  '0.7',   'Minimum engagement score to be call-priority candidate', 'threshold'),
    ('no_order_after_days',      '5',     'Days after first order before sending repeat nudge',     'threshold'),
    ('max_sms_per_week',         '3',     'Hard cap on SMS per contact per 7 days',                 'flag'),
    ('reorder_nudge_delay_h',    '4',     'Hours after delivery to send reorder nudge',             'threshold'),
    ('feedback_weight_ordered',  '0.3',   'Upward confidence boost when contact converted before',  'threshold'),
    ('feedback_weight_declined', '0.2',   'Downward confidence penalty when contact declined before','threshold')
ON CONFLICT (signal_key) DO NOTHING;


-- ─────────────────────────────────────────────────────────────────
-- 2b. Add 30-day and 90-day rollup columns to engagement_rollups
--     (existing rollups only track 7-day windows)
-- ─────────────────────────────────────────────────────────────────
ALTER TABLE engagement_rollups
    ADD COLUMN IF NOT EXISTS opens_30d   INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS orders_90d  INT NOT NULL DEFAULT 0;

-- Update refresh_engagement_rollups() to also populate the new 30d/90d windows
CREATE OR REPLACE FUNCTION refresh_engagement_rollups()
RETURNS VOID
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
BEGIN
    INSERT INTO engagement_rollups (
        contact_id,
        opens_7d, clicks_7d, sms_sent_7d, sms_clicks_7d, orders_7d,
        opens_30d, orders_90d,
        updated_at
    )
    SELECT
        c.id AS contact_id,
        COALESCE(SUM(CASE WHEN e.event_type = 'email_open'   AND e.occurred_at >= NOW() - INTERVAL '7 days'  THEN 1 ELSE 0 END), 0) AS opens_7d,
        COALESCE(SUM(CASE WHEN e.event_type = 'email_click'  AND e.occurred_at >= NOW() - INTERVAL '7 days'  THEN 1 ELSE 0 END), 0) AS clicks_7d,
        COALESCE(SUM(CASE WHEN e.event_type = 'sms_sent'     AND e.occurred_at >= NOW() - INTERVAL '7 days'  THEN 1 ELSE 0 END), 0) AS sms_sent_7d,
        COALESCE(SUM(CASE WHEN e.event_type = 'sms_click'    AND e.occurred_at >= NOW() - INTERVAL '7 days'  THEN 1 ELSE 0 END), 0) AS sms_clicks_7d,
        COALESCE(SUM(CASE WHEN e.event_type = 'order_placed' AND e.occurred_at >= NOW() - INTERVAL '7 days'  THEN 1 ELSE 0 END), 0) AS orders_7d,
        COALESCE(SUM(CASE WHEN e.event_type = 'email_open'   AND e.occurred_at >= NOW() - INTERVAL '30 days' THEN 1 ELSE 0 END), 0) AS opens_30d,
        COALESCE(SUM(CASE WHEN e.event_type = 'order_placed' AND e.occurred_at >= NOW() - INTERVAL '90 days' THEN 1 ELSE 0 END), 0) AS orders_90d,
        NOW() AS updated_at
    FROM contacts c
    LEFT JOIN events e ON e.contact_id = c.id
    GROUP BY c.id
    ON CONFLICT (contact_id) DO UPDATE SET
        opens_7d      = EXCLUDED.opens_7d,
        clicks_7d     = EXCLUDED.clicks_7d,
        sms_sent_7d   = EXCLUDED.sms_sent_7d,
        sms_clicks_7d = EXCLUDED.sms_clicks_7d,
        orders_7d     = EXCLUDED.orders_7d,
        opens_30d     = EXCLUDED.opens_30d,
        orders_90d    = EXCLUDED.orders_90d,
        updated_at    = NOW();
END;
$$;


-- ─────────────────────────────────────────────────────────────────
-- 3. Function: get_top_reorder_candidates(limit)
--    Returns N contacts ranked by reorder urgency, with call script
--    Used to answer: "who should I call and what should I say?"
-- ─────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION get_top_reorder_candidates(p_limit INT DEFAULT 10)
RETURNS TABLE (
    rank                    INT,
    contact_id              BIGINT,
    full_name               TEXT,
    phone                   TEXT,
    email                   TEXT,
    lifecycle_segment       TEXT,
    total_orders            INT,
    last_order_at           TIMESTAMPTZ,
    days_since_last_order   INT,
    opens_7d                INT,
    opens_30d               INT,
    orders_90d              INT,
    last_delivery_status    TEXT,
    last_delivery_at        TIMESTAMPTZ,
    recent_sms_count        INT,
    opportunity_outcome     TEXT,
    urgency_score           FLOAT,
    call_reason             TEXT,
    suggested_script        TEXT
)
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
BEGIN
    RETURN QUERY
    WITH base AS (
        SELECT
            c.id                                                            AS contact_id,
            TRIM(COALESCE(c.first_name, '') || ' ' || COALESCE(c.last_name, ''))  AS full_name,
            c.phone,
            c.email,
            c.lifecycle_segment::TEXT,
            c.total_orders,
            c.last_order_at,
            EXTRACT(DAY FROM NOW() - c.last_order_at)::INT                 AS days_since_last_order,
            COALESCE(er.opens_7d, 0)                                       AS opens_7d,
            COALESCE(er.opens_30d, 0)                                      AS opens_30d,
            COALESCE(er.orders_90d, 0)                                     AS orders_90d
        FROM contacts c
        LEFT JOIN engagement_rollups er ON er.contact_id = c.id
        WHERE c.phone IS NOT NULL
          AND c.phone != ''
          AND c.lifecycle_segment NOT IN ('optout', 'churned')
          AND c.last_order_at IS NOT NULL
    ),
    with_delivery AS (
        SELECT
            b.*,
            ds.status::TEXT    AS last_delivery_status,
            ds.occurred_at     AS last_delivery_at
        FROM base b
        LEFT JOIN LATERAL (
            SELECT status, occurred_at
            FROM delivery_status
            WHERE contact_id = b.contact_id
            ORDER BY occurred_at DESC
            LIMIT 1
        ) ds ON true
    ),
    with_sms AS (
        SELECT
            wd.*,
            (SELECT COUNT(*) FROM telnyx_messages tm
             WHERE tm.contact_id = wd.contact_id
               AND tm.direction = 'outbound'
               AND tm.sent_at >= NOW() - INTERVAL '7 days')::INT AS recent_sms_count
        FROM with_delivery wd
    ),
    with_outcomes AS (
        SELECT
            ws.*,
            (SELECT outcome FROM opportunities op
             WHERE op.contact_id = ws.contact_id
               AND op.outcome IS NOT NULL
             ORDER BY op.created_at DESC
             LIMIT 1) AS opportunity_outcome
        FROM with_sms ws
    ),
    scored AS (
        SELECT
            wo.*,
            -- URGENCY SCORING MODEL
            -- Base: engagement (opens weight heavily)
            (CASE WHEN wo.opens_7d >= 3 THEN 0.35
                  WHEN wo.opens_7d >= 1 THEN 0.20
                  ELSE 0.05 END
            -- Days since last order: sweet spot 14-60 days
            + CASE WHEN wo.days_since_last_order BETWEEN 14 AND 30 THEN 0.25
                   WHEN wo.days_since_last_order BETWEEN 30 AND 60 THEN 0.20
                   WHEN wo.days_since_last_order BETWEEN 7 AND 14 THEN 0.15
                   ELSE 0.05 END
            -- Order history: repeat customers are easier to convert
            + CASE WHEN wo.total_orders >= 5 THEN 0.20
                   WHEN wo.total_orders >= 2 THEN 0.15
                   WHEN wo.total_orders = 1 THEN 0.10
                   ELSE 0.0 END
            -- Delivery recency: completed delivery = prime reorder window
            + CASE WHEN wo.last_delivery_status = 'delivered'
                        AND wo.last_delivery_at >= NOW() - INTERVAL '3 days' THEN 0.15
                   WHEN wo.last_delivery_status = 'delivered'
                        AND wo.last_delivery_at >= NOW() - INTERVAL '7 days' THEN 0.10
                   ELSE 0.0 END
            -- Feedback loop: previous 'ordered' outcome = easier to convert
            + CASE WHEN wo.opportunity_outcome = 'ordered' THEN 0.05
                   WHEN wo.opportunity_outcome IN ('not_interested', 'declined') THEN -0.10
                   ELSE 0.0 END
            -- Penalise if already messaged recently
            - CASE WHEN wo.recent_sms_count >= 2 THEN 0.15
                   WHEN wo.recent_sms_count = 1 THEN 0.05
                   ELSE 0.0 END
            ) AS urgency_score
        FROM with_outcomes wo
    ),
    reasoned AS (
        SELECT
            s.*,
            -- Determine primary call reason
            CASE
                WHEN s.last_delivery_status = 'delivered'
                     AND s.last_delivery_at >= NOW() - INTERVAL '7 days'
                    THEN 'Recent delivery — prime reorder window'
                WHEN s.opens_7d >= 3 AND s.last_order_at < NOW() - INTERVAL '14 days'
                    THEN 'High email engagement but not ordered in ' || s.days_since_last_order || ' days'
                WHEN s.total_orders >= 5 AND s.days_since_last_order BETWEEN 20 AND 45
                    THEN 'Loyal customer (' || s.total_orders || ' orders) approaching lapse zone'
                WHEN s.lifecycle_segment IN ('lapsed_customer', 'reactivation_candidate')
                     AND s.opens_30d > 0
                    THEN 'Lapsed customer re-engaging — opened emails recently'
                WHEN s.opportunity_outcome = 'ordered'
                    THEN 'Previously converted — follow-up for repeat order'
                ELSE 'Engaged contact overdue for personal outreach (' || s.days_since_last_order || 'd since last order)'
            END AS call_reason,
            -- Generate personalised call script
            'Hi ' || SPLIT_PART(TRIM(COALESCE(s.full_name, '')), ' ', 1) || '! '
            || CASE
                WHEN s.last_delivery_status = 'delivered'
                    THEN 'Hope you enjoyed your last meal! We wanted to personally check in and see if you''d like to order again. '
                         || 'We have some new specials this week you''d love.'
                WHEN s.lifecycle_segment IN ('lapsed_customer', 'reactivation_candidate')
                    THEN 'We noticed it''s been a while since your last order and we genuinely miss cooking for you. '
                         || 'We have some new dishes and better delivery slots — can I share what''s new?'
                WHEN s.total_orders >= 5
                    THEN 'You''ve been one of our most loyal customers with ' || s.total_orders || ' orders, '
                         || 'and we wanted to personally say thank you. '
                         || 'We''d love to hear what we can do to make your next order even better.'
                WHEN s.opens_7d >= 3
                    THEN 'I see you''ve been checking out our emails — is there anything specific you were looking for? '
                         || 'I can help you find the perfect meal or plan for this week.'
                ELSE 'We''re reaching out personally to our valued customers. '
                     || 'It''s been about ' || s.days_since_last_order || ' days and we''d love to cook for you again. '
                     || 'Can I take a quick order or answer any questions?'
            END AS suggested_script
        FROM scored s
    )
    SELECT
        ROW_NUMBER() OVER (ORDER BY urgency_score DESC)::INT AS rank,
        contact_id,
        full_name,
        phone,
        email,
        lifecycle_segment,
        total_orders,
        last_order_at,
        days_since_last_order,
        opens_7d,
        opens_30d,
        orders_90d,
        last_delivery_status,
        last_delivery_at,
        recent_sms_count,
        opportunity_outcome,
        urgency_score,
        call_reason,
        suggested_script
    FROM reasoned
    WHERE urgency_score > 0.2   -- only meaningful candidates
    ORDER BY urgency_score DESC
    LIMIT p_limit;
END;
$$;


-- ─────────────────────────────────────────────────────────────────
-- 4. Function: sync_shipday_order(raw payload JSONB)
--    Called by the bulk historical import to upsert one order
-- ─────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION sync_shipday_order(p_payload JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_order_id      TEXT;
    v_contact_id    BIGINT;
    v_email         TEXT;
    v_phone         TEXT;
    v_status        TEXT;
    v_actual_del    TIMESTAMPTZ;
    v_created_at    TIMESTAMPTZ;
    v_result        JSONB;
BEGIN
    v_order_id   := p_payload->>'orderId';
    IF v_order_id IS NULL THEN
        v_order_id := p_payload->>'id';
    END IF;
    IF v_order_id IS NULL THEN
        RETURN jsonb_build_object('status', 'skipped', 'reason', 'no_order_id');
    END IF;

    v_email  := p_payload->'customer'->>'email';
    v_phone  := p_payload->'customer'->>'phone';
    v_status := UPPER(COALESCE(p_payload->>'orderStatus', p_payload->>'status', 'UNKNOWN'));

    -- Try to parse timestamps
    BEGIN
        v_actual_del := (p_payload->>'actualDeliveryTime')::TIMESTAMPTZ;
    EXCEPTION WHEN OTHERS THEN v_actual_del := NULL; END;

    BEGIN
        v_created_at := (p_payload->>'createdAt')::TIMESTAMPTZ;
    EXCEPTION WHEN OTHERS THEN v_created_at := NOW(); END;

    -- Resolve contact
    IF v_email IS NOT NULL AND v_email != '' THEN
        SELECT id INTO v_contact_id FROM contacts WHERE email = v_email LIMIT 1;
    END IF;
    IF v_contact_id IS NULL AND v_phone IS NOT NULL AND v_phone != '' THEN
        SELECT id INTO v_contact_id FROM contacts WHERE phone = v_phone LIMIT 1;
    END IF;

    -- Upsert raw order
    INSERT INTO shipday_orders_raw (
        shipday_order_id, order_number, contact_id,
        customer_email, customer_phone, customer_name,
        delivery_address, shipday_status,
        driver_name, driver_phone,
        estimated_delivery, actual_delivery,
        order_created_at, raw_payload
    ) VALUES (
        v_order_id,
        p_payload->>'orderNumber',
        v_contact_id,
        v_email,
        v_phone,
        p_payload->'customer'->>'name',
        COALESCE(p_payload->'customer'->>'address', p_payload->'deliveryAddress'->>'address'),
        v_status,
        p_payload->'assignedCarrier'->>'name',
        p_payload->'assignedCarrier'->>'phone',
        (p_payload->>'estimatedDeliveryTime')::TIMESTAMPTZ,
        v_actual_del,
        v_created_at,
        p_payload
    )
    ON CONFLICT (shipday_order_id) DO UPDATE SET
        shipday_status    = EXCLUDED.shipday_status,
        contact_id        = COALESCE(EXCLUDED.contact_id, shipday_orders_raw.contact_id),
        actual_delivery   = COALESCE(EXCLUDED.actual_delivery, shipday_orders_raw.actual_delivery),
        driver_name       = COALESCE(EXCLUDED.driver_name, shipday_orders_raw.driver_name),
        synced_at         = NOW(),
        raw_payload       = EXCLUDED.raw_payload;

    -- For COMPLETED orders with a linked contact: fire order_placed + delivery event
    IF v_status = 'COMPLETED' AND v_contact_id IS NOT NULL THEN
        -- Insert order_placed event (ignore duplicate via DO NOTHING based on metadata match)
        INSERT INTO events (contact_id, event_type, occurred_at, metadata)
        VALUES (
            v_contact_id,
            'order_placed',
            COALESCE(v_actual_del, v_created_at),
            jsonb_build_object(
                'source', 'shipday_historical',
                'order_number', p_payload->>'orderNumber',
                'shipday_order_id', v_order_id
            )
        )
        ON CONFLICT DO NOTHING;

        -- Insert delivery status
        INSERT INTO delivery_status (contact_id, order_ref, status, updated_by, occurred_at, metadata)
        VALUES (
            v_contact_id,
            COALESCE(p_payload->>'orderNumber', v_order_id),
            'delivered',
            'shipday_historical_sync',
            COALESCE(v_actual_del, v_created_at),
            jsonb_build_object('shipday_order_id', v_order_id, 'source', 'historical')
        )
        ON CONFLICT DO NOTHING;
    END IF;

    v_result := jsonb_build_object(
        'status',      'ok',
        'order_id',    v_order_id,
        'contact_id',  v_contact_id,
        'matched',     (v_contact_id IS NOT NULL)
    );
    RETURN v_result;
END;
$$;


-- ─────────────────────────────────────────────────────────────────
-- 5. Add ON CONFLICT DO NOTHING to events INSERT to prevent
--    duplicate events from historical sync (idempotency)
--    NOTE: This requires a unique constraint — add a partial index
-- ─────────────────────────────────────────────────────────────────
-- Unique constraint: one 'order_placed' event per contact + source + order_number combo
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_shipday_unique
    ON events (contact_id, event_type, (metadata->>'shipday_order_id'))
    WHERE metadata->>'shipday_order_id' IS NOT NULL;

-- Unique constraint: one 'delivered' status per contact + order_ref combo (from historical)
CREATE UNIQUE INDEX IF NOT EXISTS idx_delivery_status_unique
    ON delivery_status (contact_id, order_ref, (metadata->>'source'))
    WHERE metadata->>'source' = 'historical';
