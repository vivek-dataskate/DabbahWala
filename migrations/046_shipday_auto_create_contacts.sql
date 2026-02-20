-- Migration 044: Auto-create contacts from unmatched Shipday orders
-- -----------------------------------------------------------------
-- Updates sync_shipday_order() so that when an incoming Shipday order
-- cannot be matched to an existing contact by email or phone, a new
-- contact record is created from the Shipday customer payload.
--
-- Before this migration: unmatched orders were saved with contact_id=NULL
-- and silently skipped by the feedback-sync, events, and agent pipeline.
--
-- After this migration: every Shipday order with identifiable customer
-- data (name, email, or phone) will have a contact — so all pipeline
-- phases (feedback, rollups, agents) process them normally.

SET search_path TO dabbahwala;

-- ─────────────────────────────────────────────────────────────────
-- Helper: split a full name string into (first_name, last_name)
-- Returns the last word as last_name, everything before as first_name.
-- ─────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION _split_name(p_full_name TEXT, OUT first_name TEXT, OUT last_name TEXT)
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_parts TEXT[];
BEGIN
    v_parts    := string_to_array(TRIM(p_full_name), ' ');
    last_name  := v_parts[array_upper(v_parts, 1)];
    first_name := TRIM(array_to_string(v_parts[1:array_upper(v_parts, 1) - 1], ' '));
    -- Edge case: single-word name (no space)
    IF first_name = '' OR first_name IS NULL THEN
        first_name := last_name;
        last_name  := NULL;
    END IF;
END;
$$;


-- ─────────────────────────────────────────────────────────────────
-- Updated sync_shipday_order: auto-create contact if no match found
-- ─────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION sync_shipday_order(p_payload JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_order_id        TEXT;
    v_contact_id      BIGINT;
    v_email           TEXT;
    v_phone           TEXT;
    v_customer_name   TEXT;
    v_first_name      TEXT;
    v_last_name       TEXT;
    v_status          TEXT;
    v_actual_del      TIMESTAMPTZ;
    v_created_at      TIMESTAMPTZ;
    v_address         TEXT;
    v_contact_created BOOLEAN := FALSE;
    v_result          JSONB;
BEGIN
    -- ── Extract order ID ───────────────────────────────────────────
    v_order_id := p_payload->>'orderId';
    IF v_order_id IS NULL THEN
        v_order_id := p_payload->>'id';
    END IF;
    IF v_order_id IS NULL THEN
        RETURN jsonb_build_object('status', 'skipped', 'reason', 'no_order_id');
    END IF;

    -- ── Extract customer fields ────────────────────────────────────
    v_email         := NULLIF(TRIM(COALESCE(p_payload->'customer'->>'email', '')), '');
    v_phone         := NULLIF(TRIM(COALESCE(p_payload->'customer'->>'phone', '')), '');
    v_customer_name := NULLIF(TRIM(COALESCE(p_payload->'customer'->>'name', '')), '');
    v_address       := NULLIF(TRIM(COALESCE(
                           p_payload->'customer'->>'address',
                           p_payload->'deliveryAddress'->>'address',
                           ''
                       )), '');
    v_status        := UPPER(COALESCE(p_payload->>'orderStatus', p_payload->>'status', 'UNKNOWN'));

    -- ── Parse timestamps ───────────────────────────────────────────
    BEGIN
        v_actual_del := (p_payload->>'actualDeliveryTime')::TIMESTAMPTZ;
    EXCEPTION WHEN OTHERS THEN v_actual_del := NULL; END;

    BEGIN
        v_created_at := (p_payload->>'createdAt')::TIMESTAMPTZ;
    EXCEPTION WHEN OTHERS THEN v_created_at := NOW(); END;

    -- ── Resolve existing contact (email → phone) ───────────────────
    IF v_email IS NOT NULL THEN
        SELECT id INTO v_contact_id FROM contacts WHERE email = v_email LIMIT 1;
    END IF;
    IF v_contact_id IS NULL AND v_phone IS NOT NULL THEN
        SELECT id INTO v_contact_id FROM contacts WHERE phone = v_phone LIMIT 1;
    END IF;

    -- ── Auto-create contact if still unmatched ─────────────────────
    IF v_contact_id IS NULL AND (v_email IS NOT NULL OR v_phone IS NOT NULL OR v_customer_name IS NOT NULL) THEN
        -- Split name into first / last
        IF v_customer_name IS NOT NULL THEN
            SELECT fn, ln INTO v_first_name, v_last_name
            FROM _split_name(v_customer_name) AS t(fn TEXT, ln TEXT);
        ELSE
            v_first_name := 'Shipday';
            v_last_name  := 'Customer';
        END IF;

        INSERT INTO contacts (
            first_name, last_name,
            email, phone,
            lifecycle_segment,
            total_orders,
            last_order_at,
            created_at
        ) VALUES (
            v_first_name,
            v_last_name,
            v_email,
            v_phone,
            CASE WHEN v_status IN ('COMPLETED', 'DELIVERED') THEN 'new_customer' ELSE 'cold' END,
            CASE WHEN v_status IN ('COMPLETED', 'DELIVERED') THEN 1 ELSE 0 END,
            CASE WHEN v_status IN ('COMPLETED', 'DELIVERED')
                 THEN COALESCE(v_actual_del, v_created_at)
                 ELSE NULL END,
            COALESCE(v_created_at, NOW())
        )
        ON CONFLICT DO NOTHING
        RETURNING id INTO v_contact_id;

        -- Concurrent insert race: if another session just created it, look it up
        IF v_contact_id IS NULL THEN
            IF v_email IS NOT NULL THEN
                SELECT id INTO v_contact_id FROM contacts WHERE email = v_email LIMIT 1;
            END IF;
            IF v_contact_id IS NULL AND v_phone IS NOT NULL THEN
                SELECT id INTO v_contact_id FROM contacts WHERE phone = v_phone LIMIT 1;
            END IF;
        END IF;

        IF v_contact_id IS NOT NULL THEN
            v_contact_created := TRUE;
        END IF;
    END IF;

    -- ── Upsert raw order ───────────────────────────────────────────
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
        v_customer_name,
        v_address,
        v_status,
        p_payload->'assignedCarrier'->>'name',
        p_payload->'assignedCarrier'->>'phone',
        NULLIF(TRIM(COALESCE(p_payload->>'estimatedDeliveryTime', '')), '')::TIMESTAMPTZ,
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

    -- ── Fire events for completed, matched orders ──────────────────
    IF v_status IN ('COMPLETED', 'DELIVERED') AND v_contact_id IS NOT NULL THEN
        INSERT INTO events (contact_id, event_type, occurred_at, metadata)
        VALUES (
            v_contact_id,
            'order_placed',
            COALESCE(v_actual_del, v_created_at),
            jsonb_build_object(
                'source',           'shipday_historical',
                'order_number',     p_payload->>'orderNumber',
                'shipday_order_id', v_order_id,
                'contact_created',  v_contact_created
            )
        )
        ON CONFLICT DO NOTHING;

        INSERT INTO delivery_status (contact_id, order_ref, status, updated_by, occurred_at, metadata)
        VALUES (
            v_contact_id,
            COALESCE(p_payload->>'orderNumber', v_order_id),
            'delivered',
            'shipday_historical_sync',
            COALESCE(v_actual_del, v_created_at),
            jsonb_build_object(
                'shipday_order_id', v_order_id,
                'source',           'historical',
                'contact_created',  v_contact_created
            )
        )
        ON CONFLICT DO NOTHING;
    END IF;

    v_result := jsonb_build_object(
        'status',          'ok',
        'order_id',        v_order_id,
        'contact_id',      v_contact_id,
        'matched',         (v_contact_id IS NOT NULL),
        'contact_created', v_contact_created
    );
    RETURN v_result;
END;
$$;
