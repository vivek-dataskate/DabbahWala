-- Migration 046: Auto-create contacts AND orders from Shipday data
-- ----------------------------------------------------------------
-- Updates sync_shipday_order() to do three things when processing
-- a Shipday order:
--
-- 1. CONTACT  — resolve by email/phone; create new contact if no match
-- 2. ORDER    — resolve by order_id_external (order number) or by
--               contact_id + order_date; create new order if no match;
--               update delivery_date / notes if the order exists but
--               those fields are missing
-- 3. CONTACT STATS — keep contacts.total_orders and last_order_at
--               in sync whenever a new order row is created
--
-- Before this migration:
--   - Unmatched contacts  → contact_id = NULL, skipped by all pipelines
--   - Unmatched orders    → never written to the orders table at all
--
-- After this migration:
--   - Every order with customer data gets a contact record
--   - Every order with a contact gets a row in orders (created or updated)
--   - contacts.total_orders / last_order_at are always kept current

SET search_path TO dabbahwala;

-- ─────────────────────────────────────────────────────────────────
-- Helper: split a full name string into (first_name, last_name)
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
    -- Edge case: single-word name
    IF first_name = '' OR first_name IS NULL THEN
        first_name := last_name;
        last_name  := NULL;
    END IF;
END;
$$;


-- ─────────────────────────────────────────────────────────────────
-- Main function: sync one Shipday order
-- ─────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION sync_shipday_order(p_payload JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    -- Shipday identifiers
    v_order_id          TEXT;
    v_order_number      TEXT;

    -- Customer fields
    v_contact_id        BIGINT;
    v_email             TEXT;
    v_phone             TEXT;
    v_customer_name     TEXT;
    v_first_name        TEXT;
    v_last_name         TEXT;
    v_address           TEXT;

    -- Order status & timestamps
    v_status            TEXT;
    v_actual_del        TIMESTAMPTZ;
    v_created_at        TIMESTAMPTZ;
    v_order_date        DATE;
    v_delivery_date     DATE;

    -- Order financials / meta
    v_total_amount      NUMERIC(10,2);
    v_notes             TEXT;

    -- Internal tracking
    v_contact_created   BOOLEAN := FALSE;
    v_order_db_id       BIGINT;
    v_order_created     BOOLEAN := FALSE;
    v_order_updated     BOOLEAN := FALSE;
    v_result            JSONB;
BEGIN
    -- ── 1. Extract Shipday order identifiers ───────────────────────
    v_order_id     := COALESCE(p_payload->>'orderId', p_payload->>'id');
    v_order_number := NULLIF(TRIM(COALESCE(p_payload->>'orderNumber', '')), '');

    IF v_order_id IS NULL THEN
        RETURN jsonb_build_object('status', 'skipped', 'reason', 'no_order_id');
    END IF;

    -- ── 2. Extract customer fields ─────────────────────────────────
    v_email         := NULLIF(TRIM(COALESCE(p_payload->'customer'->>'email', '')), '');
    v_phone         := NULLIF(TRIM(COALESCE(p_payload->'customer'->>'phone', '')), '');
    v_customer_name := NULLIF(TRIM(COALESCE(p_payload->'customer'->>'name', '')), '');
    v_address       := NULLIF(TRIM(COALESCE(
                           p_payload->'customer'->>'address',
                           p_payload->'deliveryAddress'->>'address',
                           ''
                       )), '');
    v_status        := UPPER(COALESCE(p_payload->>'orderStatus', p_payload->>'status', 'UNKNOWN'));

    -- Delivery notes (customer note on the order)
    v_notes := NULLIF(TRIM(COALESCE(p_payload->>'deliveryInstruction', '')), '');

    -- Order total (Shipday may expose this under different keys)
    BEGIN
        v_total_amount := (
            COALESCE(
                NULLIF(p_payload->>'orderTotal', ''),
                NULLIF(p_payload->>'totalAmount', ''),
                NULLIF(p_payload->>'totalCost', ''),
                '0'
            )
        )::NUMERIC(10,2);
    EXCEPTION WHEN OTHERS THEN
        v_total_amount := 0;
    END;

    -- ── 3. Parse timestamps → dates ────────────────────────────────
    BEGIN
        v_actual_del := (p_payload->>'actualDeliveryTime')::TIMESTAMPTZ;
    EXCEPTION WHEN OTHERS THEN v_actual_del := NULL; END;

    BEGIN
        v_created_at := (p_payload->>'createdAt')::TIMESTAMPTZ;
    EXCEPTION WHEN OTHERS THEN v_created_at := NOW(); END;

    v_order_date    := COALESCE(v_created_at, NOW())::DATE;
    v_delivery_date := v_actual_del::DATE;   -- NULL if not yet delivered

    -- ── 4. Resolve existing contact (email → phone) ────────────────
    IF v_email IS NOT NULL THEN
        SELECT id INTO v_contact_id FROM contacts WHERE email = v_email LIMIT 1;
    END IF;
    IF v_contact_id IS NULL AND v_phone IS NOT NULL THEN
        SELECT id INTO v_contact_id FROM contacts WHERE phone = v_phone LIMIT 1;
    END IF;

    -- ── 5. Auto-create contact if still unmatched ──────────────────
    IF v_contact_id IS NULL
       AND (v_email IS NOT NULL OR v_phone IS NOT NULL OR v_customer_name IS NOT NULL)
    THEN
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
                 THEN COALESCE(v_actual_del, v_created_at) ELSE NULL END,
            COALESCE(v_created_at, NOW())
        )
        ON CONFLICT DO NOTHING
        RETURNING id INTO v_contact_id;

        -- Race condition: another session may have just created it
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

    -- ── 6. Upsert into shipday_orders_raw ─────────────────────────
    INSERT INTO shipday_orders_raw (
        shipday_order_id, order_number, contact_id,
        customer_email, customer_phone, customer_name,
        delivery_address, shipday_status,
        driver_name, driver_phone,
        estimated_delivery, actual_delivery,
        order_created_at, raw_payload
    ) VALUES (
        v_order_id,
        v_order_number,
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
        shipday_status  = EXCLUDED.shipday_status,
        contact_id      = COALESCE(EXCLUDED.contact_id, shipday_orders_raw.contact_id),
        actual_delivery = COALESCE(EXCLUDED.actual_delivery, shipday_orders_raw.actual_delivery),
        driver_name     = COALESCE(EXCLUDED.driver_name, shipday_orders_raw.driver_name),
        synced_at       = NOW(),
        raw_payload     = EXCLUDED.raw_payload;

    -- ── 7. Match / create / update in orders table ─────────────────
    --   Only do this when we have a contact to link to
    IF v_contact_id IS NOT NULL THEN

        -- 7a. Try to find an existing orders row
        --     Priority 1: match by external order number (most precise)
        --     Priority 2: same contact + same order date (dedup fallback)
        IF v_order_number IS NOT NULL THEN
            SELECT id INTO v_order_db_id
            FROM orders
            WHERE order_id_external = v_order_number
            LIMIT 1;
        END IF;

        IF v_order_db_id IS NULL THEN
            SELECT id INTO v_order_db_id
            FROM orders
            WHERE contact_id = v_contact_id
              AND order_date = v_order_date
            ORDER BY id DESC
            LIMIT 1;
        END IF;

        -- 7b. Order found → patch delivery_date and/or notes if missing
        IF v_order_db_id IS NOT NULL THEN
            UPDATE orders SET
                delivery_date = COALESCE(delivery_date, v_delivery_date),
                notes         = COALESCE(notes, v_notes),
                -- Also backfill order_id_external if it was blank
                order_id_external = COALESCE(order_id_external, v_order_number)
            WHERE id = v_order_db_id
              AND (
                  (delivery_date IS NULL AND v_delivery_date IS NOT NULL)
                  OR (notes IS NULL AND v_notes IS NOT NULL)
                  OR (order_id_external IS NULL AND v_order_number IS NOT NULL)
              );

            GET DIAGNOSTICS v_order_updated = ROW_COUNT;   -- 1 if any row changed

        -- 7c. No order found → create one
        ELSE
            INSERT INTO orders (
                contact_id,
                order_id_external,
                order_date,
                delivery_date,
                source,
                total_amount,
                customer_name_raw,
                notes,
                metadata
            ) VALUES (
                v_contact_id,
                v_order_number,
                v_order_date,
                v_delivery_date,
                'Shipday',
                v_total_amount,
                v_customer_name,
                v_notes,
                jsonb_build_object(
                    'shipday_order_id', v_order_id,
                    'shipday_status',   v_status,
                    'driver_name',      p_payload->'assignedCarrier'->>'name'
                )
            )
            RETURNING id INTO v_order_db_id;

            v_order_created := TRUE;

            -- Keep contact stats in sync for newly discovered orders
            IF v_status IN ('COMPLETED', 'DELIVERED') THEN
                UPDATE contacts SET
                    total_orders  = total_orders + 1,
                    last_order_at = GREATEST(
                        last_order_at,
                        COALESCE(v_actual_del, v_created_at)
                    )
                WHERE id = v_contact_id
                  -- Only increment when contact was NOT just created
                  -- (newly created contacts already have total_orders=1)
                  AND NOT v_contact_created;
            END IF;
        END IF;

    END IF;  -- v_contact_id IS NOT NULL

    -- ── 8. Fire events for completed orders ────────────────────────
    IF v_status IN ('COMPLETED', 'DELIVERED') AND v_contact_id IS NOT NULL THEN
        INSERT INTO events (contact_id, event_type, occurred_at, metadata)
        VALUES (
            v_contact_id,
            'order_placed',
            COALESCE(v_actual_del, v_created_at),
            jsonb_build_object(
                'source',           'shipday_historical',
                'order_number',     v_order_number,
                'shipday_order_id', v_order_id,
                'order_db_id',      v_order_db_id,
                'contact_created',  v_contact_created
            )
        )
        ON CONFLICT DO NOTHING;

        INSERT INTO delivery_status (contact_id, order_ref, status, updated_by, occurred_at, metadata)
        VALUES (
            v_contact_id,
            COALESCE(v_order_number, v_order_id),
            'delivered',
            'shipday_historical_sync',
            COALESCE(v_actual_del, v_created_at),
            jsonb_build_object(
                'shipday_order_id', v_order_id,
                'source',           'historical',
                'contact_created',  v_contact_created,
                'order_db_id',      v_order_db_id
            )
        )
        ON CONFLICT DO NOTHING;
    END IF;

    -- ── 9. Return result ───────────────────────────────────────────
    v_result := jsonb_build_object(
        'status',          'ok',
        'order_id',        v_order_id,
        'order_number',    v_order_number,
        'contact_id',      v_contact_id,
        'order_db_id',     v_order_db_id,
        'matched',         (v_contact_id IS NOT NULL),
        'contact_created', v_contact_created,
        'order_created',   v_order_created,
        'order_updated',   v_order_updated
    );
    RETURN v_result;
END;
$$;
