-- 003_functions.sql
-- All stored functions — final versions only (CREATE OR REPLACE throughout).
-- Order: helpers → core engine → lifecycle API → analytics → comms → agent

SET search_path TO dabbahwala;

-- ---------------------------------------------------------------------------
-- Schema fixes (002_tables.sql was backfilled without completing its transaction)
-- ---------------------------------------------------------------------------

-- Add template_file column to campaign_routing if not present
ALTER TABLE campaign_routing ADD COLUMN IF NOT EXISTS template_file TEXT;

-- Deduplicate agent_playbook rule_name before adding unique constraint
DELETE FROM agent_playbook a
USING agent_playbook b
WHERE a.id > b.id AND a.rule_name = b.rule_name;

-- Add unique constraint on rule_name if not already present
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_playbook_rule_name'
          AND conrelid = 'agent_playbook'::regclass
    ) THEN
        ALTER TABLE agent_playbook ADD CONSTRAINT uq_playbook_rule_name UNIQUE (rule_name);
    END IF;
END $$;

-- Drop get_pending_campaign_moves so it can be recreated below with updated return type
DROP FUNCTION IF EXISTS get_pending_campaign_moves();

-- ---------------------------------------------------------------------------
-- Helper: split full name into first/last
-- ---------------------------------------------------------------------------

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
    IF first_name = '' OR first_name IS NULL THEN
        first_name := last_name;
        last_name  := NULL;
    END IF;
END;
$$;

-- ---------------------------------------------------------------------------
-- Core engine: refresh rollups + evaluate rules
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION refresh_engagement_rollups()
RETURNS VOID
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
BEGIN
    INSERT INTO engagement_rollups (
        contact_id,
        opens_7d, clicks_7d, sms_sent_7d, sms_clicks_7d, orders_7d,
        opens_30d, clicks_30d, sms_sent_30d, orders_90d,
        updated_at
    )
    SELECT
        c.id AS contact_id,
        COALESCE(SUM(CASE WHEN e.event_type = 'email_open'   AND e.occurred_at >= NOW() - INTERVAL '7 days'  THEN 1 ELSE 0 END), 0),
        COALESCE(SUM(CASE WHEN e.event_type = 'email_click'  AND e.occurred_at >= NOW() - INTERVAL '7 days'  THEN 1 ELSE 0 END), 0),
        COALESCE(SUM(CASE WHEN e.event_type = 'sms_sent'     AND e.occurred_at >= NOW() - INTERVAL '7 days'  THEN 1 ELSE 0 END), 0),
        COALESCE(SUM(CASE WHEN e.event_type = 'sms_click'    AND e.occurred_at >= NOW() - INTERVAL '7 days'  THEN 1 ELSE 0 END), 0),
        COALESCE(SUM(CASE WHEN e.event_type = 'order_placed' AND e.occurred_at >= NOW() - INTERVAL '7 days'  THEN 1 ELSE 0 END), 0),
        COALESCE(SUM(CASE WHEN e.event_type = 'email_open'   AND e.occurred_at >= NOW() - INTERVAL '30 days' THEN 1 ELSE 0 END), 0),
        COALESCE(SUM(CASE WHEN e.event_type = 'email_click'  AND e.occurred_at >= NOW() - INTERVAL '30 days' THEN 1 ELSE 0 END), 0),
        COALESCE(SUM(CASE WHEN e.event_type = 'sms_sent'     AND e.occurred_at >= NOW() - INTERVAL '30 days' THEN 1 ELSE 0 END), 0),
        COALESCE(SUM(CASE WHEN e.event_type = 'order_placed' AND e.occurred_at >= NOW() - INTERVAL '90 days' THEN 1 ELSE 0 END), 0),
        NOW()
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
        clicks_30d    = EXCLUDED.clicks_30d,
        sms_sent_30d  = EXCLUDED.sms_sent_30d,
        orders_90d    = EXCLUDED.orders_90d,
        updated_at    = NOW();
END;
$$;


CREATE OR REPLACE FUNCTION evaluate_rules(p_contact_id BIGINT DEFAULT NULL)
RETURNS INT
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_rule           RECORD;
    v_contact        RECORD;
    v_matched        BOOLEAN;
    v_changes        JSONB;
    v_prev_lifecycle lifecycle_segment;
    v_prev_campaign  campaign_name;
    v_new_campaign   campaign_name;
    v_instantly_id   TEXT;
    v_updated_count  INT := 0;
BEGIN
    UPDATE contacts
    SET lifecycle_segment = 'cold', cooling_until = NULL, updated_at = now()
    WHERE lifecycle_segment = 'cooling'
      AND cooling_until IS NOT NULL AND cooling_until < now()
      AND (p_contact_id IS NULL OR id = p_contact_id);

    FOR v_contact IN
        SELECT c.id, c.email, c.first_name, c.last_name, c.phone,
               c.lifecycle_segment, c.email_nurture_enabled,
               c.email_promo_enabled, c.sms_promo_enabled, c.sms_level,
               c.cooling_until, c.total_orders, c.last_order_at,
               COALESCE(r.opens_7d, 0) AS opens_7d, COALESCE(r.clicks_7d, 0) AS clicks_7d,
               COALESCE(r.sms_sent_7d, 0) AS sms_sent_7d, COALESCE(r.sms_clicks_7d, 0) AS sms_clicks_7d,
               COALESCE(r.orders_7d, 0) AS orders_7d
        FROM contacts c
        LEFT JOIN engagement_rollups r ON r.contact_id = c.id
        WHERE c.lifecycle_segment != 'optout'
          AND (p_contact_id IS NULL OR c.id = p_contact_id)
    LOOP
        SELECT default_campaign INTO v_prev_campaign
        FROM campaign_routing WHERE lifecycle_segment = v_contact.lifecycle_segment;

        FOR v_rule IN SELECT * FROM rules WHERE is_active = true ORDER BY priority DESC LOOP
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
                    v_changes := v_changes || jsonb_build_object('lifecycle_segment',
                        jsonb_build_object('from', v_contact.lifecycle_segment::text, 'to', v_rule.set_lifecycle::text));
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
                        cooling_until         = CASE WHEN v_rule.set_cooling_days IS NOT NULL
                                                THEN now() + (v_rule.set_cooling_days || ' days')::interval
                                                ELSE cooling_until END,
                        updated_at            = now()
                    WHERE id = v_contact.id;

                    IF v_rule.set_lifecycle IS NOT NULL THEN
                        SELECT default_campaign, instantly_campaign_id
                          INTO v_new_campaign, v_instantly_id
                        FROM campaign_routing WHERE lifecycle_segment = v_rule.set_lifecycle;
                    ELSE
                        v_new_campaign   := v_prev_campaign;
                        v_instantly_id   := NULL;
                    END IF;

                    IF v_new_campaign IS DISTINCT FROM v_prev_campaign THEN
                        -- Write directly to action_queue; skip placeholder emails and
                        -- segments with no Instantly campaign (e.g. cooling, optout).
                        IF v_instantly_id IS NOT NULL
                           AND v_contact.email IS NOT NULL
                           AND v_contact.email NOT LIKE '%@app.placeholder.local'
                        THEN
                            INSERT INTO action_queue (contact_id, action_type, payload)
                            SELECT v_contact.id::INTEGER,
                                   'push_instantly_lead',
                                   jsonb_build_object(
                                       'instantly_campaign_id', v_instantly_id,
                                       'campaign_name',         v_new_campaign::TEXT,
                                       'email',                 v_contact.email,
                                       'first_name',            COALESCE(v_contact.first_name, ''),
                                       'last_name',             COALESCE(v_contact.last_name,  ''),
                                       'phone',                 COALESCE(v_contact.phone,      '')
                                   )
                            WHERE NOT EXISTS (
                                SELECT 1 FROM action_queue aq2
                                WHERE  aq2.contact_id  = v_contact.id::INTEGER
                                  AND  aq2.action_type = 'push_instantly_lead'
                                  AND  aq2.status      = 'pending'
                            );
                        END IF;
                        v_changes := v_changes || jsonb_build_object('campaign',
                            jsonb_build_object('from', v_prev_campaign::text, 'to', v_new_campaign::text));
                    END IF;

                    INSERT INTO decision_log (contact_id, rule_id, prev_lifecycle, new_lifecycle, changes_applied)
                    VALUES (v_contact.id, v_rule.id, v_prev_lifecycle,
                            COALESCE(v_rule.set_lifecycle, v_prev_lifecycle), v_changes);

                    v_updated_count := v_updated_count + 1;
                END IF;
                EXIT;
            END IF;
        END LOOP;
    END LOOP;
    RETURN v_updated_count;
END;
$$;

-- ---------------------------------------------------------------------------
-- Lifecycle API
-- ---------------------------------------------------------------------------

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
    v_contact_id     BIGINT;
    v_event_id       BIGINT;
    v_prev_lifecycle lifecycle_segment;
BEGIN
    SELECT id, lifecycle_segment INTO v_contact_id, v_prev_lifecycle
    FROM contacts WHERE email = p_contact_email;

    IF v_contact_id IS NULL THEN
        RAISE EXCEPTION 'Contact not found: %', p_contact_email;
    END IF;

    INSERT INTO events (contact_id, event_type, metadata, occurred_at)
    VALUES (v_contact_id, p_event_type, p_metadata, now())
    RETURNING id INTO v_event_id;

    IF p_event_type = 'order_placed' THEN
        UPDATE contacts SET total_orders = total_orders + 1, last_order_at = now(), updated_at = now()
        WHERE id = v_contact_id;
    END IF;

    IF p_event_type IN ('unsubscribe', 'sms_stop') THEN
        UPDATE contacts SET
            lifecycle_segment = 'optout', email_nurture_enabled = false,
            email_promo_enabled = false, sms_promo_enabled = false, updated_at = now()
        WHERE id = v_contact_id AND lifecycle_segment != 'optout';

        INSERT INTO decision_log (contact_id, rule_id, prev_lifecycle, new_lifecycle, changes_applied)
        SELECT v_contact_id, r.id, v_prev_lifecycle, 'optout',
               '{"reason": "direct_optout_event"}'::jsonb
        FROM rules r WHERE r.rule_name = 'optout' LIMIT 1;
    END IF;

    RETURN v_event_id;
END;
$$;


CREATE OR REPLACE FUNCTION run_lifecycle_cycle()
RETURNS TABLE(contacts_updated INT, campaigns_queued INT)
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE v_updated INT; v_queued INT;
BEGIN
    PERFORM refresh_engagement_rollups();
    SELECT evaluate_rules() INTO v_updated;
    -- campaigns_queued now counts pending push_instantly_lead rows in action_queue
    SELECT count(*) INTO v_queued
    FROM action_queue
    WHERE action_type = 'push_instantly_lead' AND status = 'pending';
    RETURN QUERY SELECT v_updated, v_queued;
END;
$$;


CREATE OR REPLACE FUNCTION get_pending_campaign_moves()
RETURNS TABLE(
    queue_id BIGINT, contact_email TEXT, contact_phone TEXT,
    first_name TEXT, last_name TEXT,
    from_campaign campaign_name, to_campaign campaign_name
)
LANGUAGE sql
SET search_path TO dabbahwala
AS $$
    SELECT cq.id, c.email, c.phone, c.first_name, c.last_name,
           cq.from_campaign, cq.to_campaign
    FROM campaign_queue cq
    JOIN contacts c ON c.id = cq.contact_id
    WHERE cq.status = 'pending'
    ORDER BY cq.created_at;
$$;


CREATE OR REPLACE FUNCTION mark_campaign_executed(p_queue_id BIGINT)
RETURNS void
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
BEGIN
    UPDATE campaign_queue SET status = 'executed', executed_at = now() WHERE id = p_queue_id;
END;
$$;


CREATE OR REPLACE FUNCTION get_pending_sms()
RETURNS TABLE(
    contact_id BIGINT, contact_email TEXT, phone TEXT,
    sms_level SMALLINT, lifecycle lifecycle_segment
)
LANGUAGE sql
SET search_path TO dabbahwala
AS $$
    SELECT c.id, c.email, c.phone, c.sms_level, c.lifecycle_segment
    FROM contacts c
    WHERE c.sms_promo_enabled = true
      AND c.lifecycle_segment NOT IN ('optout', 'cooling')
      AND c.phone IS NOT NULL
    ORDER BY c.sms_level DESC, c.updated_at;
$$;


CREATE OR REPLACE FUNCTION mark_sms_sent(p_contact_id BIGINT)
RETURNS void
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
BEGIN
    INSERT INTO events (contact_id, event_type, occurred_at) VALUES (p_contact_id, 'sms_sent', now());
END;
$$;

-- ---------------------------------------------------------------------------
-- Delivery + Telnyx
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION update_delivery_status(
    p_contact_email TEXT, p_order_ref TEXT, p_status delivery_status_type,
    p_updated_by TEXT, p_notes TEXT DEFAULT NULL,
    p_location TEXT DEFAULT NULL, p_metadata JSONB DEFAULT '{}'
)
RETURNS BIGINT
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE v_contact_id BIGINT; v_delivery_id BIGINT;
BEGIN
    SELECT id INTO v_contact_id FROM contacts WHERE email = p_contact_email;
    IF v_contact_id IS NULL THEN RAISE EXCEPTION 'Contact not found: %', p_contact_email; END IF;

    INSERT INTO delivery_status (contact_id, order_ref, status, updated_by, notes, location, metadata)
    VALUES (v_contact_id, p_order_ref, p_status, p_updated_by, p_notes, p_location, p_metadata)
    RETURNING id INTO v_delivery_id;

    INSERT INTO events (contact_id, event_type, metadata)
    VALUES (v_contact_id, 'delivery_update',
            jsonb_build_object('delivery_status', p_status::text, 'order_ref', p_order_ref));

    RETURN v_delivery_id;
END;
$$;


CREATE OR REPLACE FUNCTION store_telnyx_message(
    p_contact_email TEXT, p_direction TEXT, p_from_number TEXT, p_to_number TEXT,
    p_body TEXT, p_telnyx_msg_id TEXT DEFAULT NULL, p_status TEXT DEFAULT 'sent',
    p_is_delivery_staff BOOLEAN DEFAULT false, p_metadata JSONB DEFAULT '{}',
    p_source TEXT DEFAULT 'telnyx_auto', p_agent_name TEXT DEFAULT NULL,
    p_sent_at TIMESTAMPTZ DEFAULT NULL
)
RETURNS BIGINT
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_contact_id BIGINT; v_message_id BIGINT; v_event_type event_type;
BEGIN
    SELECT id INTO v_contact_id FROM contacts WHERE email = p_contact_email;
    IF v_contact_id IS NULL THEN RAISE EXCEPTION 'Contact not found: %', p_contact_email; END IF;

    INSERT INTO telnyx_messages
        (contact_id, direction, from_number, to_number, body, telnyx_msg_id,
         status, is_delivery_staff, metadata, source, agent_name, sent_at)
    VALUES (v_contact_id, p_direction, p_from_number, p_to_number, p_body, p_telnyx_msg_id,
            p_status, p_is_delivery_staff, p_metadata, p_source, p_agent_name,
            COALESCE(p_sent_at, now()))
    RETURNING id INTO v_message_id;

    v_event_type := CASE WHEN p_direction = 'inbound' THEN 'sms_received' ELSE 'sms_sent' END;
    INSERT INTO events (contact_id, event_type, metadata)
    VALUES (v_contact_id, v_event_type,
            jsonb_build_object('telnyx_msg_id', COALESCE(p_telnyx_msg_id, '')));

    RETURN v_message_id;
END;
$$;


CREATE OR REPLACE FUNCTION store_telnyx_call(
    p_contact_email TEXT, p_direction TEXT, p_from_number TEXT, p_to_number TEXT,
    p_duration_sec INT DEFAULT 0, p_recording_url TEXT DEFAULT NULL,
    p_transcript TEXT DEFAULT NULL, p_summary TEXT DEFAULT NULL,
    p_is_delivery_staff BOOLEAN DEFAULT false, p_metadata JSONB DEFAULT '{}',
    p_started_at TIMESTAMPTZ DEFAULT now(), p_ended_at TIMESTAMPTZ DEFAULT NULL
)
RETURNS BIGINT
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE v_contact_id BIGINT; v_call_id BIGINT;
BEGIN
    SELECT id INTO v_contact_id FROM contacts WHERE email = p_contact_email;
    IF v_contact_id IS NULL THEN RAISE EXCEPTION 'Contact not found: %', p_contact_email; END IF;

    INSERT INTO telnyx_calls
        (contact_id, direction, from_number, to_number, duration_sec, recording_url,
         transcript, summary, is_delivery_staff, metadata, started_at, ended_at)
    VALUES (v_contact_id, p_direction, p_from_number, p_to_number, p_duration_sec,
            p_recording_url, p_transcript, p_summary, p_is_delivery_staff, p_metadata,
            p_started_at, p_ended_at)
    RETURNING id INTO v_call_id;

    INSERT INTO events (contact_id, event_type, metadata)
    VALUES (v_contact_id, 'call_completed',
            jsonb_build_object('call_id', v_call_id, 'duration_sec', p_duration_sec));

    RETURN v_call_id;
END;
$$;


CREATE OR REPLACE FUNCTION classify_sentiment(p_text TEXT)
RETURNS TEXT
LANGUAGE plpgsql IMMUTABLE
SET search_path TO dabbahwala
AS $$
DECLARE v_lower TEXT := lower(coalesce(p_text, ''));
BEGIN
    IF v_lower ~ '(great|amazing|excellent|perfect|love|happy|delicious|fantastic|wonderful|thank|awesome|best|fresh|on.?time|early|quick|fast|friendly|kind|helpful|pleased|satisfied|enjoyed|superb|incredible|outstanding)' THEN
        RETURN 'positive';
    ELSIF v_lower ~ '(bad|terrible|awful|horrible|late|cold|wrong|disappoint|never|complain|rude|slow|missing|poor|broken|dirty|spoiled|damaged|worst|unacceptable|not good|not great|never again|refund|complaint)' THEN
        RETURN 'negative';
    ELSE
        RETURN 'neutral';
    END IF;
END;
$$;


CREATE OR REPLACE FUNCTION store_shipday_communication(
    p_shipday_order_id TEXT, p_comm_type TEXT, p_content TEXT DEFAULT NULL,
    p_proof_image_urls TEXT[] DEFAULT NULL, p_raw_payload JSONB DEFAULT '{}',
    p_occurred_at TIMESTAMPTZ DEFAULT NULL
)
RETURNS BIGINT
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE v_id BIGINT; v_contact_id BIGINT; v_sentiment TEXT;
BEGIN
    SELECT contact_id INTO v_contact_id FROM shipday_orders_raw
    WHERE shipday_order_id = p_shipday_order_id LIMIT 1;

    v_sentiment := CASE WHEN p_comm_type = 'customer_feedback' AND p_content IS NOT NULL
                        THEN classify_sentiment(p_content) ELSE NULL END;

    INSERT INTO shipday_communications
        (contact_id, shipday_order_id, comm_type, content, proof_image_urls,
         sentiment, raw_payload, occurred_at)
    VALUES (v_contact_id, p_shipday_order_id, p_comm_type, p_content, p_proof_image_urls,
            v_sentiment, p_raw_payload, COALESCE(p_occurred_at, NOW()))
    ON CONFLICT (shipday_order_id, comm_type) DO UPDATE SET
        content          = COALESCE(EXCLUDED.content, shipday_communications.content),
        proof_image_urls = COALESCE(EXCLUDED.proof_image_urls, shipday_communications.proof_image_urls),
        sentiment        = COALESCE(EXCLUDED.sentiment, shipday_communications.sentiment),
        raw_payload      = EXCLUDED.raw_payload,
        synced_at        = NOW()
    RETURNING id INTO v_id;

    IF p_comm_type = 'customer_feedback' AND v_contact_id IS NOT NULL THEN
        INSERT INTO events (contact_id, event_type, metadata, occurred_at)
        VALUES (v_contact_id, 'delivery_update',
                jsonb_build_object('source', 'shipday_feedback', 'sentiment', v_sentiment,
                                   'shipday_order_id', p_shipday_order_id),
                COALESCE(p_occurred_at, NOW()))
        ON CONFLICT DO NOTHING;
    END IF;

    RETURN v_id;
END;
$$;


CREATE OR REPLACE FUNCTION sync_shipday_order(p_payload JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_order_id       TEXT; v_order_number   TEXT;
    v_contact_id     BIGINT; v_email          TEXT; v_phone          TEXT;
    v_customer_name  TEXT; v_first_name     TEXT; v_last_name      TEXT;
    v_address        TEXT; v_status         TEXT;
    v_actual_del     TIMESTAMPTZ; v_created_at     TIMESTAMPTZ;
    v_order_date     DATE; v_delivery_date  DATE;
    v_total_amount   NUMERIC(10,2); v_notes          TEXT;
    v_contact_created BOOLEAN := FALSE; v_order_db_id    BIGINT;
    v_order_created  BOOLEAN := FALSE; v_order_updated  BOOLEAN := FALSE;
    v_result         JSONB;
BEGIN
    v_order_id     := COALESCE(p_payload->>'orderId', p_payload->>'id');
    v_order_number := NULLIF(TRIM(COALESCE(p_payload->>'orderNumber', '')), '');
    IF v_order_id IS NULL THEN
        RETURN jsonb_build_object('status', 'skipped', 'reason', 'no_order_id');
    END IF;

    v_email         := NULLIF(TRIM(COALESCE(p_payload->'customer'->>'email', '')), '');
    v_phone         := NULLIF(TRIM(COALESCE(p_payload->'customer'->>'phone', '')), '');
    v_customer_name := NULLIF(TRIM(COALESCE(p_payload->'customer'->>'name', '')), '');
    v_address       := NULLIF(TRIM(COALESCE(p_payload->'customer'->>'address',
                               p_payload->'deliveryAddress'->>'address', '')), '');
    v_status        := UPPER(COALESCE(p_payload->>'orderStatus', p_payload->>'status', 'UNKNOWN'));
    v_notes         := NULLIF(TRIM(COALESCE(p_payload->>'deliveryInstruction', '')), '');

    BEGIN
        v_total_amount := (COALESCE(NULLIF(p_payload->>'orderTotal', ''),
            NULLIF(p_payload->>'totalAmount', ''), NULLIF(p_payload->>'totalCost', ''), '0'))::NUMERIC(10,2);
    EXCEPTION WHEN OTHERS THEN v_total_amount := 0; END;

    BEGIN v_actual_del := (p_payload->>'actualDeliveryTime')::TIMESTAMPTZ;
    EXCEPTION WHEN OTHERS THEN v_actual_del := NULL; END;

    BEGIN v_created_at := (p_payload->>'createdAt')::TIMESTAMPTZ;
    EXCEPTION WHEN OTHERS THEN v_created_at := NOW(); END;

    v_order_date    := COALESCE(v_created_at, NOW())::DATE;
    v_delivery_date := v_actual_del::DATE;

    IF v_email IS NOT NULL THEN
        SELECT id INTO v_contact_id FROM contacts WHERE email = v_email LIMIT 1;
    END IF;
    IF v_contact_id IS NULL AND v_phone IS NOT NULL THEN
        SELECT id INTO v_contact_id FROM contacts WHERE phone = v_phone LIMIT 1;
    END IF;

    IF v_contact_id IS NULL AND (v_email IS NOT NULL OR v_phone IS NOT NULL OR v_customer_name IS NOT NULL) THEN
        IF v_customer_name IS NOT NULL THEN
            SELECT fn, ln INTO v_first_name, v_last_name
            FROM _split_name(v_customer_name) AS t(fn TEXT, ln TEXT);
        ELSE
            v_first_name := 'Shipday'; v_last_name := 'Customer';
        END IF;

        INSERT INTO contacts (first_name, last_name, email, phone, lifecycle_segment,
                              total_orders, last_order_at, created_at)
        VALUES (v_first_name, v_last_name, v_email, v_phone,
                CASE WHEN v_status IN ('COMPLETED', 'DELIVERED') THEN 'new_customer' ELSE 'cold' END,
                CASE WHEN v_status IN ('COMPLETED', 'DELIVERED') THEN 1 ELSE 0 END,
                CASE WHEN v_status IN ('COMPLETED', 'DELIVERED')
                     THEN COALESCE(v_actual_del, v_created_at) ELSE NULL END,
                COALESCE(v_created_at, NOW()))
        ON CONFLICT DO NOTHING RETURNING id INTO v_contact_id;

        IF v_contact_id IS NULL THEN
            IF v_email IS NOT NULL THEN SELECT id INTO v_contact_id FROM contacts WHERE email = v_email LIMIT 1; END IF;
            IF v_contact_id IS NULL AND v_phone IS NOT NULL THEN
                SELECT id INTO v_contact_id FROM contacts WHERE phone = v_phone LIMIT 1;
            END IF;
        END IF;
        IF v_contact_id IS NOT NULL THEN v_contact_created := TRUE; END IF;
    END IF;

    INSERT INTO shipday_orders_raw
        (shipday_order_id, order_number, contact_id, customer_email, customer_phone,
         customer_name, delivery_address, shipday_status, driver_name, driver_phone,
         estimated_delivery, actual_delivery, order_created_at, raw_payload)
    VALUES (v_order_id, v_order_number, v_contact_id, v_email, v_phone,
            v_customer_name, v_address, v_status,
            p_payload->'assignedCarrier'->>'name', p_payload->'assignedCarrier'->>'phone',
            NULLIF(TRIM(COALESCE(p_payload->>'estimatedDeliveryTime', '')), '')::TIMESTAMPTZ,
            v_actual_del, v_created_at, p_payload)
    ON CONFLICT (shipday_order_id) DO UPDATE SET
        shipday_status  = EXCLUDED.shipday_status,
        contact_id      = COALESCE(EXCLUDED.contact_id, shipday_orders_raw.contact_id),
        actual_delivery = COALESCE(EXCLUDED.actual_delivery, shipday_orders_raw.actual_delivery),
        driver_name     = COALESCE(EXCLUDED.driver_name, shipday_orders_raw.driver_name),
        synced_at       = NOW(), raw_payload = EXCLUDED.raw_payload;

    IF v_contact_id IS NOT NULL THEN
        IF v_order_number IS NOT NULL THEN
            SELECT id INTO v_order_db_id FROM orders WHERE order_id_external = v_order_number LIMIT 1;
        END IF;
        IF v_order_db_id IS NULL THEN
            SELECT id INTO v_order_db_id FROM orders
            WHERE contact_id = v_contact_id AND order_date = v_order_date ORDER BY id DESC LIMIT 1;
        END IF;

        IF v_order_db_id IS NOT NULL THEN
            UPDATE orders SET
                delivery_date     = COALESCE(delivery_date, v_delivery_date),
                notes             = COALESCE(notes, v_notes),
                order_id_external = COALESCE(order_id_external, v_order_number)
            WHERE id = v_order_db_id
              AND ((delivery_date IS NULL AND v_delivery_date IS NOT NULL)
                OR (notes IS NULL AND v_notes IS NOT NULL)
                OR (order_id_external IS NULL AND v_order_number IS NOT NULL));
            GET DIAGNOSTICS v_order_updated = ROW_COUNT;
        ELSE
            INSERT INTO orders
                (contact_id, order_id_external, order_date, delivery_date, source,
                 total_amount, customer_name_raw, notes, metadata)
            VALUES (v_contact_id, v_order_number, v_order_date, v_delivery_date, 'Shipday',
                    v_total_amount, v_customer_name, v_notes,
                    jsonb_build_object('shipday_order_id', v_order_id, 'shipday_status', v_status,
                                       'driver_name', p_payload->'assignedCarrier'->>'name'))
            RETURNING id INTO v_order_db_id;
            v_order_created := TRUE;

            IF v_status IN ('COMPLETED', 'DELIVERED') THEN
                UPDATE contacts SET
                    total_orders  = total_orders + 1,
                    last_order_at = GREATEST(last_order_at, COALESCE(v_actual_del, v_created_at))
                WHERE id = v_contact_id AND NOT v_contact_created;
            END IF;
        END IF;
    END IF;

    IF v_status IN ('COMPLETED', 'DELIVERED') AND v_contact_id IS NOT NULL THEN
        INSERT INTO events (contact_id, event_type, occurred_at, metadata)
        VALUES (v_contact_id, 'order_placed', COALESCE(v_actual_del, v_created_at),
                jsonb_build_object('source', 'shipday_historical', 'order_number', v_order_number,
                                   'shipday_order_id', v_order_id, 'order_db_id', v_order_db_id,
                                   'contact_created', v_contact_created))
        ON CONFLICT DO NOTHING;

        INSERT INTO delivery_status (contact_id, order_ref, status, updated_by, occurred_at, metadata)
        VALUES (v_contact_id, COALESCE(v_order_number, v_order_id), 'delivered',
                'shipday_historical_sync', COALESCE(v_actual_del, v_created_at),
                jsonb_build_object('shipday_order_id', v_order_id, 'source', 'historical',
                                   'contact_created', v_contact_created, 'order_db_id', v_order_db_id))
        ON CONFLICT DO NOTHING;
    END IF;

    RETURN jsonb_build_object('status', 'ok', 'order_id', v_order_id, 'order_number', v_order_number,
        'contact_id', v_contact_id, 'order_db_id', v_order_db_id, 'matched', (v_contact_id IS NOT NULL),
        'contact_created', v_contact_created, 'order_created', v_order_created,
        'order_updated', v_order_updated);
END;
$$;

-- ---------------------------------------------------------------------------
-- Opportunities
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION create_opportunity(
    p_contact_id BIGINT, p_action opportunity_action, p_priority TEXT, p_reason TEXT,
    p_suggested_message TEXT DEFAULT NULL, p_confidence_score NUMERIC(3,2) DEFAULT NULL
)
RETURNS BIGINT
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE v_id BIGINT;
BEGIN
    INSERT INTO opportunities (contact_id, action, priority, reason, suggested_message, confidence_score)
    VALUES (p_contact_id, p_action, p_priority, p_reason, p_suggested_message, p_confidence_score)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;


CREATE OR REPLACE FUNCTION get_pending_opportunities()
RETURNS TABLE(
    id BIGINT, contact_id BIGINT, action opportunity_action, priority TEXT,
    reason TEXT, suggested_message TEXT, confidence_score NUMERIC,
    email TEXT, phone TEXT, first_name TEXT, last_name TEXT,
    lifecycle_segment lifecycle_segment, total_orders INT, last_order_at TIMESTAMPTZ
)
LANGUAGE sql
SET search_path TO dabbahwala
AS $$
    SELECT o.id, o.contact_id, o.action, o.priority, o.reason,
           o.suggested_message, o.confidence_score,
           c.email, c.phone, c.first_name, c.last_name,
           c.lifecycle_segment, c.total_orders, c.last_order_at
    FROM opportunities o
    JOIN contacts c ON c.id = o.contact_id
    WHERE o.status = 'pending'
    ORDER BY CASE o.priority WHEN 'hot' THEN 1 WHEN 'warm' THEN 2 ELSE 3 END, o.created_at;
$$;


CREATE OR REPLACE FUNCTION mark_opportunity_dispatched(p_opportunity_id BIGINT, p_airtable_record_id TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
BEGIN
    UPDATE opportunities SET status = 'dispatched', airtable_record_id = p_airtable_record_id,
           dispatched_at = now()
    WHERE id = p_opportunity_id AND status = 'pending';
    RETURN FOUND;
END;
$$;


CREATE OR REPLACE FUNCTION update_opportunity_outcome(
    p_opportunity_id BIGINT, p_status opportunity_status,
    p_outcome TEXT, p_outcome_notes TEXT DEFAULT NULL
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
BEGIN
    UPDATE opportunities SET status = p_status, outcome = p_outcome,
           outcome_notes = COALESCE(p_outcome_notes, outcome_notes), completed_at = now()
    WHERE id = p_opportunity_id;
    RETURN FOUND;
END;
$$;


CREATE OR REPLACE FUNCTION detect_engaged_no_order()
RETURNS TABLE(
    id BIGINT, email TEXT, first_name TEXT, last_name TEXT, phone TEXT,
    lifecycle_segment lifecycle_segment, total_orders INT, last_order_at TIMESTAMPTZ,
    opens_7d INT, clicks_7d INT
)
LANGUAGE sql SET search_path TO dabbahwala AS $$
    SELECT c.id, c.email, c.first_name, c.last_name, c.phone,
           c.lifecycle_segment, c.total_orders, c.last_order_at, r.opens_7d, r.clicks_7d
    FROM contacts c JOIN engagement_rollups r ON r.contact_id = c.id
    WHERE r.opens_7d >= 2 AND r.orders_7d = 0
      AND c.lifecycle_segment NOT IN ('optout', 'cooling', 'new_customer')
      AND NOT EXISTS (SELECT 1 FROM opportunities o
          WHERE o.contact_id = c.id AND o.status IN ('pending', 'dispatched'));
$$;


CREATE OR REPLACE FUNCTION detect_new_customer_no_repeat()
RETURNS TABLE(
    id BIGINT, email TEXT, first_name TEXT, last_name TEXT, phone TEXT,
    lifecycle_segment lifecycle_segment, total_orders INT, last_order_at TIMESTAMPTZ
)
LANGUAGE sql SET search_path TO dabbahwala AS $$
    SELECT c.id, c.email, c.first_name, c.last_name, c.phone,
           c.lifecycle_segment, c.total_orders, c.last_order_at
    FROM contacts c
    WHERE c.lifecycle_segment = 'new_customer' AND c.total_orders = 1
      AND c.last_order_at < now() - interval '5 days'
      AND NOT EXISTS (SELECT 1 FROM opportunities o
          WHERE o.contact_id = c.id AND o.status IN ('pending', 'dispatched'));
$$;


CREATE OR REPLACE FUNCTION detect_lapsed_reengaged()
RETURNS TABLE(
    id BIGINT, email TEXT, first_name TEXT, last_name TEXT, phone TEXT,
    lifecycle_segment lifecycle_segment, total_orders INT, last_order_at TIMESTAMPTZ
)
LANGUAGE sql SET search_path TO dabbahwala AS $$
    SELECT DISTINCT c.id, c.email, c.first_name, c.last_name, c.phone,
           c.lifecycle_segment, c.total_orders, c.last_order_at
    FROM contacts c JOIN decision_log dl ON dl.contact_id = c.id
    WHERE dl.prev_lifecycle IN ('lapsed_customer', 'reactivation_candidate')
      AND dl.new_lifecycle = 'engaged' AND dl.decided_at > now() - interval '3 days'
      AND NOT EXISTS (SELECT 1 FROM opportunities o
          WHERE o.contact_id = c.id AND o.status IN ('pending', 'dispatched'));
$$;


CREATE OR REPLACE FUNCTION detect_reorder_intent()
RETURNS TABLE(
    id BIGINT, email TEXT, first_name TEXT, last_name TEXT, phone TEXT,
    lifecycle_segment lifecycle_segment, total_orders INT, last_order_at TIMESTAMPTZ,
    transcript TEXT
)
LANGUAGE sql SET search_path TO dabbahwala AS $$
    SELECT DISTINCT c.id, c.email, c.first_name, c.last_name, c.phone,
           c.lifecycle_segment, c.total_orders, c.last_order_at, tc.transcript
    FROM contacts c JOIN telnyx_calls tc ON tc.contact_id = c.id
    WHERE tc.started_at > now() - interval '3 days' AND tc.transcript IS NOT NULL
      AND (tc.transcript ILIKE '%reorder%' OR tc.transcript ILIKE '%order again%'
           OR tc.transcript ILIKE '%next delivery%' OR tc.transcript ILIKE '%loved it%')
      AND NOT EXISTS (SELECT 1 FROM opportunities o
          WHERE o.contact_id = c.id AND o.status IN ('pending', 'dispatched'));
$$;


CREATE OR REPLACE FUNCTION get_opportunity_outcomes(p_days INT DEFAULT 30)
RETURNS JSONB
LANGUAGE plpgsql SET search_path TO dabbahwala
AS $$
DECLARE v_summary JSONB; v_rates JSONB;
BEGIN
    SELECT jsonb_agg(row_to_json(t)) INTO v_summary FROM (
        SELECT action::text, priority, outcome, count(*) AS count
        FROM opportunities WHERE completed_at > now() - (p_days || ' days')::interval AND outcome IS NOT NULL
        GROUP BY action, priority, outcome ORDER BY count DESC) t;

    SELECT row_to_json(t)::jsonb INTO v_rates FROM (
        SELECT count(*) AS total,
               count(*) FILTER (WHERE outcome = 'ordered') AS converted,
               count(*) FILTER (WHERE outcome = 'not_interested') AS not_interested,
               count(*) FILTER (WHERE outcome = 'no_answer') AS no_answer
        FROM opportunities WHERE completed_at > now() - (p_days || ' days')::interval) t;

    RETURN jsonb_build_object('days', p_days, 'outcome_summary', COALESCE(v_summary, '[]'::jsonb),
                              'rates', COALESCE(v_rates, '{}'::jsonb));
END;
$$;


CREATE OR REPLACE FUNCTION get_high_intent_signals(p_contact_id BIGINT)
RETURNS JSONB
LANGUAGE plpgsql SET search_path TO dabbahwala
AS $$
DECLARE v_contact JSONB; v_sms JSONB; v_calls JSONB; v_deliveries JSONB;
BEGIN
    SELECT row_to_json(t)::jsonb INTO v_contact FROM (
        SELECT c.*, r.opens_7d, r.clicks_7d, r.sms_sent_7d, r.sms_clicks_7d, r.orders_7d
        FROM contacts c LEFT JOIN engagement_rollups r ON r.contact_id = c.id
        WHERE c.id = p_contact_id) t;

    IF v_contact IS NULL THEN
        RETURN jsonb_build_object('error', 'Contact not found: ' || p_contact_id);
    END IF;

    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_sms FROM (
        SELECT direction, body, is_delivery_staff, sent_at
        FROM telnyx_messages WHERE contact_id = p_contact_id ORDER BY sent_at DESC LIMIT 10) t;

    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_calls FROM (
        SELECT transcript, summary, is_delivery_staff, duration_sec, started_at
        FROM telnyx_calls WHERE contact_id = p_contact_id AND transcript IS NOT NULL
        ORDER BY started_at DESC LIMIT 5) t;

    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_deliveries FROM (
        SELECT status, notes, updated_by, occurred_at
        FROM delivery_status WHERE contact_id = p_contact_id ORDER BY occurred_at DESC LIMIT 10) t;

    RETURN jsonb_build_object('contact', v_contact, 'recent_sms', v_sms,
                              'call_transcripts', v_calls, 'delivery_notes', v_deliveries);
END;
$$;

-- ---------------------------------------------------------------------------
-- Analytics + reporting
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION get_lifecycle_summary()
RETURNS TABLE(lifecycle_segment lifecycle_segment, count BIGINT)
LANGUAGE sql SET search_path TO dabbahwala AS $$
    SELECT lifecycle_segment, count(*) FROM contacts GROUP BY lifecycle_segment ORDER BY count DESC;
$$;


CREATE OR REPLACE FUNCTION get_campaign_performance(p_campaign campaign_name, p_days INT DEFAULT 7)
RETURNS JSONB
LANGUAGE plpgsql SET search_path TO dabbahwala
AS $$
DECLARE v_contact_count BIGINT; v_activity JSONB;
BEGIN
    SELECT count(*) INTO v_contact_count
    FROM contacts c JOIN campaign_routing cr ON cr.lifecycle_segment = c.lifecycle_segment
    WHERE cr.default_campaign = p_campaign;

    SELECT COALESCE(jsonb_object_agg(event_type::text, cnt), '{}'::jsonb) INTO v_activity FROM (
        SELECT e.event_type, count(*) AS cnt
        FROM events e JOIN contacts c ON c.id = e.contact_id
        JOIN campaign_routing cr ON cr.lifecycle_segment = c.lifecycle_segment
        WHERE cr.default_campaign = p_campaign AND e.occurred_at > now() - (p_days || ' days')::interval
        GROUP BY e.event_type) t;

    RETURN jsonb_build_object('campaign', p_campaign::text, 'days', p_days,
                              'contacts_in_campaign', v_contact_count, 'activity', v_activity);
END;
$$;


CREATE OR REPLACE FUNCTION get_engagement_trends(p_days INT DEFAULT 30)
RETURNS TABLE(day DATE, event_type event_type, count BIGINT)
LANGUAGE sql SET search_path TO dabbahwala AS $$
    SELECT occurred_at::date, event_type, count(*)
    FROM events WHERE occurred_at > now() - (p_days || ' days')::interval
    GROUP BY 1, 2 ORDER BY 1 DESC, 2;
$$;


CREATE OR REPLACE FUNCTION get_order_attribution(p_days_lookback INT DEFAULT 7)
RETURNS JSONB
LANGUAGE plpgsql SET search_path TO dabbahwala
AS $$
DECLARE v_result JSONB;
BEGIN
    SELECT row_to_json(t)::jsonb INTO v_result FROM (
        WITH ord AS (
            SELECT e.id, e.contact_id, e.occurred_at AS order_at,
                   c.email, cr.default_campaign AS current_campaign
            FROM events e JOIN contacts c ON c.id = e.contact_id
            LEFT JOIN campaign_routing cr ON cr.lifecycle_segment = c.lifecycle_segment
            WHERE e.event_type = 'order_placed' AND e.occurred_at > now() - interval '30 days'
        ),
        attributed AS (
            SELECT o.*,
                   (SELECT e2.event_type FROM events e2
                    WHERE e2.contact_id = o.contact_id
                      AND e2.event_type IN ('email_open', 'email_click', 'sms_click')
                      AND e2.occurred_at BETWEEN o.order_at - (p_days_lookback || ' days')::interval AND o.order_at
                    ORDER BY e2.occurred_at DESC LIMIT 1) AS attributed_touch
            FROM ord o
        )
        SELECT count(*) AS total_orders,
               count(attributed_touch) AS attributed_orders,
               count(*) - count(attributed_touch) AS unattributed_orders
        FROM attributed) t;

    RETURN COALESCE(v_result, '{}'::jsonb);
END;
$$;


CREATE OR REPLACE FUNCTION get_decision_history(p_contact_id BIGINT, p_limit INT DEFAULT 20)
RETURNS TABLE(
    id BIGINT, rule_name TEXT, prev_lifecycle lifecycle_segment,
    new_lifecycle lifecycle_segment, changes_applied JSONB, decided_at TIMESTAMPTZ
)
LANGUAGE sql SET search_path TO dabbahwala AS $$
    SELECT dl.id, r.rule_name, dl.prev_lifecycle, dl.new_lifecycle, dl.changes_applied, dl.decided_at
    FROM decision_log dl LEFT JOIN rules r ON r.id = dl.rule_id
    WHERE dl.contact_id = p_contact_id ORDER BY dl.decided_at DESC LIMIT p_limit;
$$;


CREATE OR REPLACE FUNCTION get_daily_report(p_report_date DATE)
RETURNS TABLE(id BIGINT, report_date DATE, report_data JSONB, net_new_orders INT, created_at TIMESTAMPTZ)
LANGUAGE sql SET search_path TO dabbahwala AS $$
    SELECT id, report_date, report_data, net_new_orders, created_at
    FROM daily_reports WHERE report_date = p_report_date;
$$;


CREATE OR REPLACE FUNCTION generate_daily_report(p_report_date DATE)
RETURNS JSONB
LANGUAGE plpgsql SET search_path TO dabbahwala
AS $$
DECLARE
    v_activity JSONB; v_transitions JSONB; v_pipeline JSONB;
    v_net_new INT; v_report_data JSONB; v_report_id BIGINT;
BEGIN
    SELECT COALESCE(jsonb_object_agg(event_type::text, count), '{}'::jsonb) INTO v_activity FROM (
        SELECT e.event_type, count(*) FROM events e
        WHERE e.occurred_at::date = p_report_date
          AND e.event_type IN ('email_open', 'email_click', 'sms_sent', 'sms_click',
                               'order_placed', 'unsubscribe')
        GROUP BY e.event_type) t;

    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_transitions FROM (
        SELECT prev_lifecycle::text, new_lifecycle::text, count(*)
        FROM decision_log WHERE decided_at::date = p_report_date
        GROUP BY 1, 2) t;

    SELECT COALESCE(jsonb_object_agg(lifecycle_segment::text, count), '{}'::jsonb) INTO v_pipeline FROM (
        SELECT lifecycle_segment, count(*) FROM contacts GROUP BY lifecycle_segment) t;

    SELECT count(DISTINCT e_order.id)::int INTO v_net_new
    FROM events e_order WHERE e_order.event_type = 'order_placed'
      AND e_order.occurred_at::date = p_report_date
      AND EXISTS (SELECT 1 FROM events e_touch WHERE e_touch.contact_id = e_order.contact_id
                  AND e_touch.event_type IN ('email_open', 'email_click', 'sms_click')
                  AND e_touch.occurred_at BETWEEN e_order.occurred_at - interval '7 days' AND e_order.occurred_at);

    v_net_new := COALESCE(v_net_new, 0);
    v_report_data := jsonb_build_object('activity', v_activity, 'transitions', v_transitions,
                                        'pipeline', v_pipeline, 'net_new_orders', v_net_new);

    INSERT INTO daily_reports (report_date, report_data, net_new_orders)
    VALUES (p_report_date, v_report_data, v_net_new)
    ON CONFLICT (report_date) DO UPDATE SET
        report_data = EXCLUDED.report_data, net_new_orders = EXCLUDED.net_new_orders,
        created_at = now()
    RETURNING daily_reports.id INTO v_report_id;

    RETURN jsonb_build_object('id', v_report_id, 'report_date', p_report_date,
        'activity', v_activity, 'transitions', v_transitions,
        'pipeline', v_pipeline, 'net_new_orders', v_net_new);
END;
$$;

-- ---------------------------------------------------------------------------
-- Contact lookup + search
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION get_contact_detail(p_email_or_id TEXT)
RETURNS JSONB
LANGUAGE plpgsql SET search_path TO dabbahwala
AS $$
DECLARE
    v_contact_id BIGINT; v_contact JSONB; v_events JSONB;
    v_decisions JSONB; v_rollups JSONB;
BEGIN
    IF p_email_or_id ~ '^\d+$' THEN
        SELECT id INTO v_contact_id FROM contacts WHERE id = p_email_or_id::bigint;
    ELSE
        SELECT id INTO v_contact_id FROM contacts WHERE email = p_email_or_id;
    END IF;

    IF v_contact_id IS NULL THEN
        RETURN jsonb_build_object('error', 'Contact not found: ' || p_email_or_id);
    END IF;

    SELECT row_to_json(c)::jsonb INTO v_contact FROM contacts c WHERE c.id = v_contact_id;
    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_events FROM (
        SELECT event_type, occurred_at, metadata FROM events
        WHERE contact_id = v_contact_id AND occurred_at > now() - interval '30 days'
        ORDER BY occurred_at DESC LIMIT 20) t;
    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_decisions FROM (
        SELECT r.rule_name, dl.prev_lifecycle, dl.new_lifecycle, dl.changes_applied, dl.decided_at
        FROM decision_log dl LEFT JOIN rules r ON r.id = dl.rule_id
        WHERE dl.contact_id = v_contact_id ORDER BY dl.decided_at DESC LIMIT 10) t;
    SELECT COALESCE(row_to_json(er)::jsonb, '{}'::jsonb) INTO v_rollups
    FROM engagement_rollups er WHERE er.contact_id = v_contact_id;

    RETURN jsonb_build_object('contact', v_contact,
        'engagement_rollups', COALESCE(v_rollups, '{}'::jsonb),
        'recent_events', v_events, 'recent_decisions', v_decisions);
END;
$$;


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
    id BIGINT, email TEXT, first_name TEXT, last_name TEXT,
    lifecycle_segment lifecycle_segment, email_promo_enabled BOOLEAN,
    sms_promo_enabled BOOLEAN, sms_level SMALLINT,
    current_campaign campaign_name, total_orders INT, last_order_at TIMESTAMPTZ
)
LANGUAGE plpgsql SET search_path TO dabbahwala
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
    WHERE (p_lifecycle_segment   IS NULL OR c.lifecycle_segment   = p_lifecycle_segment::lifecycle_segment)
      AND (p_email_promo_enabled IS NULL OR c.email_promo_enabled = p_email_promo_enabled)
      AND (p_sms_promo_enabled   IS NULL OR c.sms_promo_enabled   = p_sms_promo_enabled)
      AND (p_min_orders          IS NULL OR c.total_orders        >= p_min_orders)
      AND (p_max_orders          IS NULL OR c.total_orders        <= p_max_orders)
    ORDER BY c.updated_at DESC LIMIT p_limit;
END;
$$;


CREATE OR REPLACE FUNCTION get_communication_history(p_contact_id BIGINT, p_days INT DEFAULT 30)
RETURNS JSONB
LANGUAGE plpgsql SET search_path TO dabbahwala
AS $$
DECLARE v_sms JSONB; v_calls JSONB; v_deliveries JSONB; v_shipday JSONB;
BEGIN
    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_sms FROM (
        SELECT id, direction, from_number, to_number, body, status,
               source, agent_name, is_delivery_staff, sent_at
        FROM telnyx_messages WHERE contact_id = p_contact_id
          AND sent_at > now() - (p_days || ' days')::interval ORDER BY sent_at DESC) t;

    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_calls FROM (
        SELECT id, direction, from_number, to_number, duration_sec,
               transcript, summary, is_delivery_staff, started_at, ended_at
        FROM telnyx_calls WHERE contact_id = p_contact_id
          AND started_at > now() - (p_days || ' days')::interval ORDER BY started_at DESC) t;

    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_deliveries FROM (
        SELECT id, order_ref, status, updated_by, notes, location, occurred_at
        FROM delivery_status WHERE contact_id = p_contact_id
          AND occurred_at > now() - (p_days || ' days')::interval ORDER BY occurred_at DESC) t;

    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_shipday FROM (
        SELECT id, shipday_order_id, order_number, comm_type,
               content, proof_image_urls, sentiment, occurred_at
        FROM shipday_communications WHERE contact_id = p_contact_id
          AND occurred_at > now() - (p_days || ' days')::interval ORDER BY occurred_at DESC) t;

    RETURN jsonb_build_object('contact_id', p_contact_id, 'days', p_days,
        'sms_messages', v_sms, 'voice_calls', v_calls,
        'delivery_updates', v_deliveries, 'shipday_comms', v_shipday);
END;
$$;


CREATE OR REPLACE FUNCTION get_delivery_tracking(p_contact_id BIGINT)
RETURNS TABLE(
    id BIGINT, order_ref TEXT, status delivery_status_type,
    updated_by TEXT, notes TEXT, location TEXT, occurred_at TIMESTAMPTZ
)
LANGUAGE sql SET search_path TO dabbahwala AS $$
    SELECT ds.id, ds.order_ref, ds.status, ds.updated_by, ds.notes, ds.location, ds.occurred_at
    FROM delivery_status ds WHERE ds.contact_id = p_contact_id ORDER BY ds.occurred_at DESC LIMIT 50;
$$;


DROP FUNCTION IF EXISTS suggest_reactivation_targets(INT);

CREATE OR REPLACE FUNCTION suggest_reactivation_targets(p_limit INT DEFAULT 20)
RETURNS TABLE(
    id BIGINT, email TEXT, first_name TEXT, last_name TEXT, phone TEXT,
    lifecycle_segment lifecycle_segment, total_orders INT, last_order_at TIMESTAMPTZ,
    sms_level SMALLINT, current_campaign campaign_name,
    opens_7d INT, clicks_7d INT, sms_clicks_7d INT, reactivation_score NUMERIC
)
LANGUAGE sql SET search_path TO dabbahwala AS $$
    SELECT c.id, c.email, c.first_name, c.last_name, c.phone,
           c.lifecycle_segment, c.total_orders, c.last_order_at, c.sms_level,
           cr.default_campaign AS current_campaign,
           r.opens_7d, r.clicks_7d, r.sms_clicks_7d,
           (COALESCE(r.opens_7d, 0) * 1 + COALESCE(r.clicks_7d, 0) * 3
          + COALESCE(r.sms_clicks_7d, 0) * 2 + c.total_orders * 2)::numeric AS reactivation_score
    FROM contacts c
    LEFT JOIN engagement_rollups r  ON r.contact_id          = c.id
    LEFT JOIN campaign_routing   cr ON cr.lifecycle_segment  = c.lifecycle_segment
    WHERE c.lifecycle_segment IN ('lapsed_customer', 'reactivation_candidate')
    ORDER BY reactivation_score DESC, c.last_order_at DESC LIMIT p_limit;
$$;


CREATE OR REPLACE FUNCTION get_content_strategy_data(p_contact_id BIGINT)
RETURNS JSONB
LANGUAGE plpgsql SET search_path TO dabbahwala
AS $$
DECLARE
    v_contact JSONB; v_rollups JSONB; v_event_summary JSONB;
    v_transcripts JSONB; v_delivery_notes JSONB; v_past_outcomes JSONB;
BEGIN
    SELECT row_to_json(c)::jsonb INTO v_contact FROM contacts c WHERE c.id = p_contact_id;
    IF v_contact IS NULL THEN
        RETURN jsonb_build_object('error', 'Contact not found: ' || p_contact_id);
    END IF;

    SELECT COALESCE(row_to_json(er)::jsonb, '{}'::jsonb) INTO v_rollups
    FROM engagement_rollups er WHERE er.contact_id = p_contact_id;
    SELECT COALESCE(jsonb_object_agg(event_type::text, count), '{}'::jsonb) INTO v_event_summary FROM (
        SELECT event_type, count(*) FROM events
        WHERE contact_id = p_contact_id AND occurred_at > now() - interval '30 days'
        GROUP BY event_type) t;
    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_transcripts FROM (
        SELECT transcript, summary, started_at FROM telnyx_calls
        WHERE contact_id = p_contact_id AND transcript IS NOT NULL
        ORDER BY started_at DESC LIMIT 5) t;
    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_delivery_notes FROM (
        SELECT status, notes, occurred_at FROM delivery_status
        WHERE contact_id = p_contact_id ORDER BY occurred_at DESC LIMIT 10) t;
    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_past_outcomes FROM (
        SELECT action::text, priority, reason, outcome, created_at FROM opportunities
        WHERE contact_id = p_contact_id AND outcome IS NOT NULL ORDER BY created_at DESC LIMIT 10) t;

    RETURN jsonb_build_object('contact', v_contact,
        'engagement_7d', COALESCE(v_rollups, '{}'::jsonb),
        'event_summary_30d', v_event_summary, 'recent_transcripts', v_transcripts,
        'delivery_feedback', v_delivery_notes, 'past_opportunity_outcomes', v_past_outcomes);
END;
$$;

-- ---------------------------------------------------------------------------
-- SMS templates
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION get_sms_template(p_scenario TEXT, p_sms_level SMALLINT DEFAULT 1)
RETURNS TABLE(template_key TEXT, body TEXT, variant CHAR)
LANGUAGE sql SET search_path TO dabbahwala AS $$
    SELECT template_key, body, variant FROM sms_templates
    WHERE scenario = p_scenario AND sms_level <= p_sms_level AND is_active = true
    ORDER BY sms_level DESC, variant LIMIT 1;
$$;

-- ---------------------------------------------------------------------------
-- Team content search
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION search_team_content(
    p_content_type TEXT DEFAULT NULL, p_search_query TEXT DEFAULT NULL,
    p_limit INT DEFAULT 20, p_offset INT DEFAULT 0
)
RETURNS TABLE(
    id BIGINT, content_type TEXT, title TEXT, body TEXT, author TEXT,
    source TEXT, tags TEXT[], created_at TIMESTAMPTZ, doc_url TEXT, relevance REAL
)
LANGUAGE sql STABLE SET search_path TO dabbahwala
AS $$
    SELECT tc.id, tc.content_type, tc.title, tc.body, tc.author, tc.source,
           tc.tags, tc.created_at, tc.doc_url,
           CASE WHEN p_search_query IS NOT NULL AND p_search_query != ''
                THEN ts_rank(to_tsvector('english', tc.title || ' ' || tc.body),
                             plainto_tsquery('english', p_search_query))
                ELSE 0.0 END AS relevance
    FROM team_content tc
    WHERE tc.is_active = true
      AND (p_content_type IS NULL OR tc.content_type = p_content_type)
      AND (p_search_query IS NULL OR p_search_query = ''
           OR to_tsvector('english', tc.title || ' ' || tc.body)
              @@ plainto_tsquery('english', p_search_query))
    ORDER BY relevance DESC, tc.created_at DESC LIMIT p_limit OFFSET p_offset;
$$;


CREATE OR REPLACE FUNCTION get_recent_team_content(
    p_content_type TEXT DEFAULT NULL, p_limit INT DEFAULT 10
)
RETURNS TABLE(
    id BIGINT, content_type TEXT, title TEXT, body TEXT,
    author TEXT, tags TEXT[], created_at TIMESTAMPTZ
)
LANGUAGE sql STABLE SET search_path TO dabbahwala
AS $$
    SELECT tc.id, tc.content_type, tc.title, tc.body, tc.author, tc.tags, tc.created_at
    FROM team_content tc
    WHERE tc.is_active = true
      AND (p_content_type IS NULL OR tc.content_type = p_content_type)
    ORDER BY tc.created_at DESC LIMIT p_limit;
$$;

-- ---------------------------------------------------------------------------
-- Vector / semantic search
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION store_embedding(
    p_source_type TEXT, p_source_id BIGINT, p_contact_id BIGINT,
    p_content_text TEXT, p_embedding vector(1536)
)
RETURNS BIGINT
LANGUAGE plpgsql SET search_path TO dabbahwala
AS $$
DECLARE v_id BIGINT;
BEGIN
    INSERT INTO content_embeddings (source_type, source_id, contact_id, content_text, embedding)
    VALUES (p_source_type, p_source_id, p_contact_id, p_content_text, p_embedding)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;


CREATE OR REPLACE FUNCTION detect_reorder_intent_semantic(
    p_query_embedding vector(1536), p_similarity_threshold FLOAT DEFAULT 0.75,
    p_limit INT DEFAULT 20
)
RETURNS TABLE(
    contact_id BIGINT, email TEXT, first_name TEXT, last_name TEXT,
    phone TEXT, lifecycle_segment lifecycle_segment,
    total_orders INT, last_order_at TIMESTAMPTZ,
    content_text TEXT, similarity FLOAT
)
LANGUAGE sql SET search_path TO dabbahwala
AS $$
    SELECT DISTINCT ON (c.id)
           c.id, c.email, c.first_name, c.last_name, c.phone,
           c.lifecycle_segment, c.total_orders, c.last_order_at,
           ce.content_text,
           1 - (ce.embedding <=> p_query_embedding) AS similarity
    FROM content_embeddings ce
    JOIN contacts c ON c.id = ce.contact_id
    WHERE ce.source_type IN ('transcript', 'sms')
      AND 1 - (ce.embedding <=> p_query_embedding) >= p_similarity_threshold
      AND NOT EXISTS (SELECT 1 FROM opportunities o
          WHERE o.contact_id = c.id AND o.status IN ('pending', 'dispatched'))
    ORDER BY c.id, similarity DESC
    LIMIT p_limit;
$$;


CREATE OR REPLACE FUNCTION find_similar_customers(
    p_contact_id BIGINT, p_limit INT DEFAULT 10
)
RETURNS TABLE(
    contact_id BIGINT, email TEXT, first_name TEXT, last_name TEXT,
    phone TEXT, lifecycle_segment lifecycle_segment,
    total_orders INT, last_order_at TIMESTAMPTZ, similarity FLOAT
)
LANGUAGE sql SET search_path TO dabbahwala
AS $$
    WITH target_embedding AS (
        SELECT embedding FROM content_embeddings
        WHERE contact_id = p_contact_id AND source_type = 'customer_profile'
        ORDER BY created_at DESC LIMIT 1
    )
    SELECT DISTINCT ON (c.id) c.id, c.email, c.first_name, c.last_name, c.phone,
           c.lifecycle_segment, c.total_orders, c.last_order_at,
           1 - (ce.embedding <=> te.embedding) AS similarity
    FROM content_embeddings ce
    JOIN contacts c ON c.id = ce.contact_id
    CROSS JOIN target_embedding te
    WHERE ce.source_type = 'customer_profile' AND ce.contact_id != p_contact_id
      AND 1 - (ce.embedding <=> te.embedding) > 0.7
    ORDER BY c.id, similarity DESC LIMIT p_limit;
$$;


CREATE OR REPLACE FUNCTION search_content_semantic(
    p_query_embedding vector(1536), p_source_types TEXT[] DEFAULT ARRAY['transcript', 'sms'],
    p_limit INT DEFAULT 10
)
RETURNS TABLE(
    id BIGINT, source_type TEXT, source_id BIGINT, contact_id BIGINT,
    content_text TEXT, similarity FLOAT, created_at TIMESTAMPTZ
)
LANGUAGE sql SET search_path TO dabbahwala
AS $$
    SELECT ce.id, ce.source_type, ce.source_id, ce.contact_id, ce.content_text,
           1 - (ce.embedding <=> p_query_embedding) AS similarity, ce.created_at
    FROM content_embeddings ce
    WHERE ce.source_type = ANY(p_source_types)
    ORDER BY ce.embedding <=> p_query_embedding LIMIT p_limit;
$$;

-- ---------------------------------------------------------------------------
-- Order pattern analytics
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION get_order_day_patterns(p_days INT DEFAULT 90)
RETURNS JSON AS $$
DECLARE result JSON;
BEGIN
    SELECT json_agg(row_to_json(t)) INTO result FROM (
        SELECT TRIM(TO_CHAR(o.order_date, 'Day')) AS day_name,
               EXTRACT(DOW FROM o.order_date)::INT AS day_num,
               COUNT(*) AS order_count,
               ROUND(SUM(o.total_amount)::numeric, 2) AS revenue,
               ROUND(AVG(o.total_amount)::numeric, 2) AS avg_order_value
        FROM orders o WHERE o.order_date >= CURRENT_DATE - p_days
        GROUP BY 1, 2 ORDER BY 2) t;
    RETURN COALESCE(result, '[]'::json);
END;
$$ LANGUAGE plpgsql;


CREATE OR REPLACE FUNCTION get_top_menu_items(p_days INT DEFAULT 30, p_limit INT DEFAULT 15)
RETURNS JSON AS $$
DECLARE result JSON;
BEGIN
    SELECT json_agg(row_to_json(t)) INTO result FROM (
        SELECT oi.item_name, mc.category, mc.is_veg,
               SUM(oi.quantity) AS total_qty,
               COUNT(DISTINCT oi.order_id) AS order_count,
               ROUND(AVG(oi.unit_price)::numeric, 2) AS avg_price,
               ROUND(SUM(oi.line_total)::numeric, 2) AS total_revenue
        FROM order_items oi JOIN orders o ON o.id = oi.order_id
        LEFT JOIN menu_catalog mc ON mc.item_name = oi.item_name
        WHERE o.order_date >= CURRENT_DATE - p_days
        GROUP BY oi.item_name, mc.category, mc.is_veg
        ORDER BY total_qty DESC LIMIT p_limit) t;
    RETURN COALESCE(result, '[]'::json);
END;
$$ LANGUAGE plpgsql;


CREATE OR REPLACE FUNCTION get_customer_frequency_segments(p_days INT DEFAULT 90)
RETURNS JSON AS $$
DECLARE result JSON;
BEGIN
    SELECT json_agg(row_to_json(t)) INTO result FROM (
        SELECT CASE WHEN order_count >= 10 THEN 'weekly_plus'
                    WHEN order_count >= 4  THEN 'regular'
                    WHEN order_count >= 2  THEN 'occasional'
                    ELSE 'one_time' END AS segment,
               COUNT(*) AS customer_count,
               ROUND(AVG(total_spend)::numeric, 2) AS avg_spend,
               ROUND(SUM(total_spend)::numeric, 2) AS total_revenue
        FROM (SELECT contact_id, COUNT(*) AS order_count, SUM(total_amount) AS total_spend
              FROM orders WHERE order_date >= CURRENT_DATE - p_days GROUP BY contact_id) sub
        GROUP BY 1 ORDER BY customer_count DESC) t;
    RETURN COALESCE(result, '[]'::json);
END;
$$ LANGUAGE plpgsql;


CREATE OR REPLACE FUNCTION get_top_contacts_to_call(p_limit INT DEFAULT 15)
RETURNS JSON AS $$
DECLARE result JSON;
BEGIN
    SELECT json_agg(row_to_json(t)) INTO result FROM (
        SELECT c.id AS contact_id, c.first_name, c.last_name, c.phone, c.email,
               c.lifecycle_segment, c.total_orders, c.last_order_at,
               (CURRENT_DATE - c.last_order_at::date) AS days_since_last_order,
               ROUND(COALESCE(SUM(o.total_amount), 0)::numeric, 2) AS lifetime_spend
        FROM contacts c LEFT JOIN orders o ON o.contact_id = c.id
        WHERE c.total_orders >= 10 AND c.last_order_at IS NOT NULL
          AND c.last_order_at::date BETWEEN CURRENT_DATE - 90 AND CURRENT_DATE - 14
          AND c.lifecycle_segment NOT IN ('churned', 'optout', 'cooling')
          AND NOT EXISTS (SELECT 1 FROM opportunities op WHERE op.contact_id = c.id
              AND op.action = 'field_sales_call' AND op.status IN ('pending', 'dispatched'))
        GROUP BY c.id ORDER BY c.total_orders DESC, lifetime_spend DESC LIMIT p_limit) t;
    RETURN COALESCE(result, '[]'::json);
END;
$$ LANGUAGE plpgsql;


CREATE OR REPLACE FUNCTION get_field_agent_scorecard(p_days INT DEFAULT 30)
RETURNS JSON AS $$
DECLARE result JSON;
BEGIN
    SELECT json_agg(row_to_json(t)) INTO result FROM (
        SELECT r.agent_name, COUNT(*) AS calls_reviewed,
               ROUND(AVG(r.pitch_quality_score)::numeric, 1) AS avg_pitch_quality,
               SUM(CASE WHEN r.asked_for_order THEN 1 ELSE 0 END) AS asked_for_order_count,
               SUM(CASE WHEN r.customer_signal = 'interested' THEN 1 ELSE 0 END) AS interested_customers,
               SUM(CASE WHEN r.recommended_next_action = 'close_won'  THEN 1 ELSE 0 END) AS calls_closed_won,
               SUM(CASE WHEN r.recommended_next_action = 'close_lost' THEN 1 ELSE 0 END) AS calls_closed_lost,
               SUM(CASE WHEN r.recommended_next_action = 'call_again_today' THEN 1 ELSE 0 END) AS needs_follow_up,
               SUM(CASE WHEN r.recommended_next_action = 'hand_to_automation' THEN 1 ELSE 0 END) AS handed_to_automation
        FROM field_agent_reviews r
        WHERE r.reviewed_at >= NOW() - (p_days || ' days')::INTERVAL AND r.agent_name IS NOT NULL
        GROUP BY r.agent_name ORDER BY calls_closed_won DESC) t;
    RETURN COALESCE(result, '[]'::json);
END;
$$ LANGUAGE plpgsql;


CREATE OR REPLACE FUNCTION get_recent_field_agent_reviews(p_days INT DEFAULT 7)
RETURNS JSON AS $$
DECLARE result JSON;
BEGIN
    SELECT json_agg(row_to_json(t)) INTO result FROM (
        SELECT r.id, r.agent_name, c.first_name, c.last_name, c.phone, r.reviewed_at,
               r.pitch_quality_score, r.asked_for_order, r.customer_signal,
               r.objections_raised, r.honest_assessment, r.recommended_next_action,
               r.next_action_reason
        FROM field_agent_reviews r JOIN contacts c ON c.id = r.contact_id
        WHERE r.reviewed_at >= NOW() - (p_days || ' days')::INTERVAL
        ORDER BY r.reviewed_at DESC LIMIT 100) t;
    RETURN COALESCE(result, '[]'::json);
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- Customer goals updated_at trigger function
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION update_customer_goals_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;
