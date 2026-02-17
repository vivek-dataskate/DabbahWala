-- 010_fn_rule_engine.sql
-- Core rule engine: evaluates all active rules against contacts
-- This is the "Inference → Decision" layer

CREATE OR REPLACE FUNCTION evaluate_rules(p_contact_id BIGINT DEFAULT NULL)
RETURNS INT
LANGUAGE plpgsql
AS $$
DECLARE
    v_rule RECORD;
    v_contact RECORD;
    v_matched BOOLEAN;
    v_changes JSONB;
    v_prev_lifecycle lifecycle_segment;
    v_prev_campaign campaign_name;
    v_new_campaign campaign_name;
    v_updated_count INT := 0;
    v_contact_cursor CURSOR FOR
        SELECT c.*, r.opens_7d, r.clicks_7d, r.sms_sent_7d, r.sms_clicks_7d, r.orders_7d
        FROM contacts c
        LEFT JOIN engagement_rollups r ON r.contact_id = c.id
        WHERE c.lifecycle_segment != 'optout'
          AND (p_contact_id IS NULL OR c.id = p_contact_id);
BEGIN
    -- First: auto-clear expired cooling states
    UPDATE contacts
    SET lifecycle_segment = 'cold',
        cooling_until = NULL,
        updated_at = now()
    WHERE lifecycle_segment = 'cooling'
      AND cooling_until IS NOT NULL
      AND cooling_until < now()
      AND (p_contact_id IS NULL OR id = p_contact_id);

    -- Iterate over each eligible contact
    FOR v_contact IN
        SELECT c.id, c.email, c.lifecycle_segment, c.email_nurture_enabled,
               c.email_promo_enabled, c.sms_promo_enabled, c.sms_level,
               c.current_campaign, c.cooling_until, c.total_orders, c.last_order_at,
               COALESCE(r.opens_7d, 0) AS opens_7d,
               COALESCE(r.clicks_7d, 0) AS clicks_7d,
               COALESCE(r.sms_sent_7d, 0) AS sms_sent_7d,
               COALESCE(r.sms_clicks_7d, 0) AS sms_clicks_7d,
               COALESCE(r.orders_7d, 0) AS orders_7d
        FROM contacts c
        LEFT JOIN engagement_rollups r ON r.contact_id = c.id
        WHERE c.lifecycle_segment != 'optout'
          AND (p_contact_id IS NULL OR c.id = p_contact_id)
    LOOP
        -- Try each rule in priority order (highest first)
        FOR v_rule IN
            SELECT * FROM rules
            WHERE is_active = true
            ORDER BY priority DESC
        LOOP
            -- Dynamically evaluate the rule predicate
            EXECUTE format(
                'SELECT EXISTS(SELECT 1 FROM contacts c '
                'LEFT JOIN engagement_rollups r ON r.contact_id = c.id '
                'WHERE c.id = $1 AND (%s))',
                v_rule.predicate_sql
            ) INTO v_matched USING v_contact.id;

            IF v_matched THEN
                v_prev_lifecycle := v_contact.lifecycle_segment;
                v_prev_campaign := v_contact.current_campaign;
                v_changes := '{}'::jsonb;

                -- Apply rule effects
                IF v_rule.set_lifecycle IS NOT NULL AND v_rule.set_lifecycle != v_contact.lifecycle_segment THEN
                    v_changes := v_changes || jsonb_build_object(
                        'lifecycle_segment', jsonb_build_object('from', v_contact.lifecycle_segment::text, 'to', v_rule.set_lifecycle::text)
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

                -- Only apply if something actually changes
                IF v_changes != '{}'::jsonb OR v_rule.set_cooling_days IS NOT NULL THEN
                    UPDATE contacts SET
                        lifecycle_segment     = COALESCE(v_rule.set_lifecycle, lifecycle_segment),
                        email_nurture_enabled = COALESCE(v_rule.set_email_nurture, email_nurture_enabled),
                        email_promo_enabled   = COALESCE(v_rule.set_email_promo, email_promo_enabled),
                        sms_promo_enabled     = COALESCE(v_rule.set_sms_promo, sms_promo_enabled),
                        sms_level             = COALESCE(v_rule.set_sms_level, sms_level),
                        current_campaign      = COALESCE(v_rule.set_campaign, current_campaign),
                        cooling_until         = CASE
                                                  WHEN v_rule.set_cooling_days IS NOT NULL
                                                  THEN now() + (v_rule.set_cooling_days || ' days')::interval
                                                  ELSE cooling_until
                                                END,
                        updated_at            = now()
                    WHERE id = v_contact.id;

                    -- Determine new campaign (from rule or from routing table)
                    IF v_rule.set_campaign IS NOT NULL THEN
                        v_new_campaign := v_rule.set_campaign;
                    ELSIF v_rule.set_lifecycle IS NOT NULL THEN
                        SELECT default_campaign INTO v_new_campaign
                        FROM campaign_routing
                        WHERE campaign_routing.lifecycle_segment = v_rule.set_lifecycle;
                    ELSE
                        v_new_campaign := v_prev_campaign;
                    END IF;

                    -- Queue campaign move if campaign changed
                    IF v_new_campaign IS DISTINCT FROM v_prev_campaign THEN
                        INSERT INTO campaign_queue (contact_id, from_campaign, to_campaign)
                        VALUES (v_contact.id, v_prev_campaign, v_new_campaign);

                        UPDATE contacts SET current_campaign = v_new_campaign WHERE id = v_contact.id;

                        v_changes := v_changes || jsonb_build_object(
                            'campaign', jsonb_build_object('from', v_prev_campaign::text, 'to', v_new_campaign::text)
                        );
                    END IF;

                    -- Log the decision
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

                -- First matching rule wins — stop checking more rules for this contact
                EXIT;
            END IF;
        END LOOP;
    END LOOP;

    RETURN v_updated_count;
END;
$$;
