-- 045_shipday_communications.sql
-- Pull customer feedback, delivery instructions, and proof-of-delivery
-- from Shipday into the evidence layer. Wire into get_communication_history()
-- so inference agents see the full Shipday → customer touchpoint history.
--
-- What Shipday exposes via API (GET /orders/{orderId}):
--   feedback            — customer text feedback left after delivery
--   deliveryInstruction — customer's delivery note / special request
--   proofOfDelivery     — {signaturePath, picturePaths[], lat, lng}
--
-- Each record fires a delivery_update event so lifecycle rules and inference
-- agents can react to positive/negative sentiment immediately.

SET search_path TO dabbahwala;


-- ─── 1. Communications table ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS shipday_communications (
    id                  BIGSERIAL PRIMARY KEY,
    contact_id          BIGINT REFERENCES contacts(id) ON DELETE SET NULL,
    shipday_order_id    TEXT NOT NULL,
    order_number        TEXT,
    comm_type           TEXT NOT NULL
                            CHECK (comm_type IN (
                                'customer_feedback',
                                'delivery_instruction',
                                'proof_of_delivery'
                            )),
    content             TEXT,           -- feedback text / instruction text
    proof_image_urls    TEXT[],         -- proof of delivery photo URLs
    sentiment           TEXT            -- keyword-classified: positive/negative/neutral
                            CHECK (sentiment IN ('positive','negative','neutral') OR sentiment IS NULL),
    raw_payload         JSONB DEFAULT '{}',
    synced_at           TIMESTAMPTZ DEFAULT NOW(),
    occurred_at         TIMESTAMPTZ     -- delivery / feedback timestamp
);

-- One feedback row per order (upsertable)
CREATE UNIQUE INDEX IF NOT EXISTS idx_shipday_comm_uniq
    ON shipday_communications (shipday_order_id, comm_type);

CREATE INDEX IF NOT EXISTS idx_shipday_comm_contact
    ON shipday_communications (contact_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_shipday_comm_sentiment
    ON shipday_communications (sentiment, occurred_at DESC);


-- ─── 2. Keyword-based sentiment classifier ────────────────────────────────

CREATE OR REPLACE FUNCTION classify_sentiment(p_text TEXT)
RETURNS TEXT
LANGUAGE plpgsql IMMUTABLE
SET search_path TO dabbahwala
AS $$
DECLARE
    v_lower TEXT := lower(coalesce(p_text, ''));
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


-- ─── 3. store_shipday_communication ───────────────────────────────────────
-- Called from Python after fetching each order from the Shipday API.
-- Upserts the communication record, classifies sentiment, and fires a
-- delivery_update event so the lifecycle engine picks it up.

CREATE OR REPLACE FUNCTION store_shipday_communication(
    p_shipday_order_id  TEXT,
    p_comm_type         TEXT,
    p_content           TEXT          DEFAULT NULL,
    p_proof_image_urls  TEXT[]        DEFAULT NULL,
    p_raw_payload       JSONB         DEFAULT '{}',
    p_occurred_at       TIMESTAMPTZ   DEFAULT NULL
)
RETURNS BIGINT
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_id           BIGINT;
    v_contact_id   BIGINT;
    v_order_number TEXT;
    v_sentiment    TEXT;
    v_ts           TIMESTAMPTZ := COALESCE(p_occurred_at, now());
BEGIN
    -- Resolve contact from the already-synced shipday order
    SELECT contact_id, order_number
    INTO   v_contact_id, v_order_number
    FROM   shipday_orders_raw
    WHERE  shipday_order_id = p_shipday_order_id
    LIMIT  1;

    -- Sentiment is only meaningful for free-text feedback
    v_sentiment := CASE
        WHEN p_comm_type = 'customer_feedback' AND p_content IS NOT NULL
        THEN classify_sentiment(p_content)
        ELSE NULL
    END;

    INSERT INTO shipday_communications
        (contact_id, shipday_order_id, order_number, comm_type,
         content, proof_image_urls, sentiment, raw_payload,
         occurred_at, synced_at)
    VALUES
        (v_contact_id, p_shipday_order_id, v_order_number, p_comm_type,
         p_content, p_proof_image_urls, v_sentiment, p_raw_payload,
         v_ts, now())
    ON CONFLICT (shipday_order_id, comm_type) DO UPDATE
        SET content          = EXCLUDED.content,
            proof_image_urls = EXCLUDED.proof_image_urls,
            sentiment        = EXCLUDED.sentiment,
            raw_payload      = EXCLUDED.raw_payload,
            synced_at        = now()
    RETURNING id INTO v_id;

    -- Fire a delivery_update event so lifecycle rules and inference agents
    -- can react to the customer sentiment immediately.
    IF v_contact_id IS NOT NULL
       AND p_comm_type = 'customer_feedback'
       AND p_content IS NOT NULL
       AND p_content != ''
    THEN
        INSERT INTO events (contact_id, event_type, metadata, occurred_at)
        VALUES (
            v_contact_id,
            'delivery_update',
            jsonb_build_object(
                'source',           'shipday_feedback',
                'shipday_order_id', p_shipday_order_id,
                'comm_id',          v_id,
                'sentiment',        v_sentiment,
                'content_preview',  left(p_content, 200)
            ),
            v_ts
        );
    END IF;

    RETURN v_id;
END;
$$;


-- ─── 4. Updated get_communication_history ─────────────────────────────────
-- Adds 'shipday_comms' key so inference agents see Shipday customer
-- communications alongside SMS, calls, and delivery status updates.

CREATE OR REPLACE FUNCTION get_communication_history(p_contact_id BIGINT, p_days INT DEFAULT 30)
RETURNS JSONB
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
DECLARE
    v_sms        JSONB;
    v_calls      JSONB;
    v_deliveries JSONB;
    v_shipday    JSONB;
BEGIN
    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_sms
    FROM (
        SELECT id, direction, from_number, to_number, body, status,
               source, agent_name, is_delivery_staff, sent_at
        FROM telnyx_messages
        WHERE contact_id = p_contact_id
          AND sent_at > now() - (p_days || ' days')::interval
        ORDER BY sent_at DESC
    ) t;

    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_calls
    FROM (
        SELECT id, direction, from_number, to_number, duration_sec,
               transcript, summary, is_delivery_staff, started_at, ended_at
        FROM telnyx_calls
        WHERE contact_id = p_contact_id
          AND started_at > now() - (p_days || ' days')::interval
        ORDER BY started_at DESC
    ) t;

    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_deliveries
    FROM (
        SELECT id, order_ref, status, updated_by, notes, location, occurred_at
        FROM delivery_status
        WHERE contact_id = p_contact_id
          AND occurred_at > now() - (p_days || ' days')::interval
        ORDER BY occurred_at DESC
    ) t;

    -- NEW: Shipday customer communications (feedback, delivery notes, proof of delivery)
    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO v_shipday
    FROM (
        SELECT id, shipday_order_id, order_number, comm_type,
               content, proof_image_urls, sentiment, occurred_at
        FROM shipday_communications
        WHERE contact_id = p_contact_id
          AND occurred_at > now() - (p_days || ' days')::interval
        ORDER BY occurred_at DESC
    ) t;

    RETURN jsonb_build_object(
        'contact_id',       p_contact_id,
        'days',             p_days,
        'sms_messages',     v_sms,
        'voice_calls',      v_calls,
        'delivery_updates', v_deliveries,
        'shipday_comms',    v_shipday
    );
END;
$$;


-- ─── 5. Feedback summary view ─────────────────────────────────────────────
-- Quick view for dashboard: sentiment breakdown by recent period.

CREATE OR REPLACE VIEW shipday_feedback_summary AS
SELECT
    date_trunc('week', occurred_at)                AS week,
    sentiment,
    COUNT(*)                                        AS count,
    round(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (PARTITION BY date_trunc('week', occurred_at)), 1) AS pct
FROM shipday_communications
WHERE comm_type = 'customer_feedback'
  AND occurred_at IS NOT NULL
GROUP BY 1, 2
ORDER BY 1 DESC, 2;
