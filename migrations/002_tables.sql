-- 002_tables.sql
-- All tables in FK dependency order — final consolidated state.
-- Uses CREATE TABLE IF NOT EXISTS and CREATE INDEX IF NOT EXISTS throughout.

SET search_path TO dabbahwala;

-- ---------------------------------------------------------------------------
-- Core lifecycle tables
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS contacts (
    id                   BIGSERIAL PRIMARY KEY,
    email                TEXT UNIQUE,
    phone                TEXT,
    first_name           TEXT,
    last_name            TEXT,
    lifecycle_segment    lifecycle_segment NOT NULL DEFAULT 'cold',
    email_nurture_enabled BOOLEAN NOT NULL DEFAULT true,
    email_promo_enabled  BOOLEAN NOT NULL DEFAULT false,
    sms_promo_enabled    BOOLEAN NOT NULL DEFAULT false,
    sms_level            SMALLINT NOT NULL DEFAULT 1 CHECK (sms_level BETWEEN 1 AND 3),
    cooling_until        TIMESTAMPTZ,
    total_orders         INT NOT NULL DEFAULT 0,
    last_order_at        TIMESTAMPTZ,
    address              TEXT,
    subscription_type    TEXT,
    primary_source       TEXT DEFAULT 'Website',
    merged_from_app      BOOLEAN DEFAULT false,
    priority_override    TEXT NOT NULL DEFAULT 'none'
                             CHECK (priority_override IN ('none', 'high', 'do_not_contact')),
    sales_notes          TEXT,
    source               TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_contacts_lifecycle ON contacts (lifecycle_segment);
CREATE INDEX IF NOT EXISTS idx_contacts_cooling   ON contacts (cooling_until) WHERE cooling_until IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_contacts_source    ON contacts (source)        WHERE source IS NOT NULL;

CREATE TABLE IF NOT EXISTS events (
    id          BIGSERIAL PRIMARY KEY,
    contact_id  BIGINT NOT NULL REFERENCES contacts(id),
    event_type  event_type NOT NULL,
    metadata    JSONB DEFAULT '{}',
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_events_contact_time ON events (contact_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_type_time    ON events (event_type, occurred_at DESC);

CREATE TABLE IF NOT EXISTS engagement_rollups (
    contact_id    BIGINT PRIMARY KEY REFERENCES contacts(id),
    opens_7d      INT NOT NULL DEFAULT 0,
    clicks_7d     INT NOT NULL DEFAULT 0,
    sms_sent_7d   INT NOT NULL DEFAULT 0,
    sms_clicks_7d INT NOT NULL DEFAULT 0,
    orders_7d     INT NOT NULL DEFAULT 0,
    opens_30d     INT NOT NULL DEFAULT 0,
    clicks_30d    INT NOT NULL DEFAULT 0,
    sms_sent_30d  INT NOT NULL DEFAULT 0,
    orders_90d    INT NOT NULL DEFAULT 0,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rules (
    id                SERIAL PRIMARY KEY,
    rule_name         TEXT UNIQUE NOT NULL,
    priority          INT NOT NULL,
    predicate_sql     TEXT NOT NULL,
    set_lifecycle     lifecycle_segment,
    set_email_nurture BOOLEAN,
    set_email_promo   BOOLEAN,
    set_sms_promo     BOOLEAN,
    set_sms_level     SMALLINT,
    set_campaign      campaign_name,
    set_cooling_days  INT,
    is_active         BOOLEAN NOT NULL DEFAULT true,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS decision_log (
    id              BIGSERIAL PRIMARY KEY,
    contact_id      BIGINT NOT NULL REFERENCES contacts(id),
    rule_id         INT REFERENCES rules(id),
    prev_lifecycle  lifecycle_segment,
    new_lifecycle   lifecycle_segment,
    changes_applied JSONB NOT NULL,
    decided_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_decision_log_contact ON decision_log (contact_id, decided_at DESC);
CREATE INDEX IF NOT EXISTS idx_decision_log_time    ON decision_log (decided_at DESC);

-- campaign_routing: single source of truth for lifecycle → Instantly campaign mapping + stats
CREATE TABLE IF NOT EXISTS campaign_routing (
    lifecycle_segment     lifecycle_segment PRIMARY KEY,
    default_campaign      campaign_name,
    instantly_campaign_id TEXT,
    instantly_campaign_name TEXT,
    template_file         TEXT,
    leads_count           INTEGER DEFAULT 0,
    emails_sent           INTEGER DEFAULT 0,
    unique_opens          INTEGER DEFAULT 0,
    opens                 INTEGER DEFAULT 0,
    replies               INTEGER DEFAULT 0,
    clicks                INTEGER DEFAULT 0,
    bounces               INTEGER DEFAULT 0,
    unsubscribes          INTEGER DEFAULT 0,
    open_rate             NUMERIC(5,2) DEFAULT 0,
    reply_rate            NUMERIC(5,2) DEFAULT 0,
    stats_synced_at       TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS campaign_queue (
    id            BIGSERIAL PRIMARY KEY,
    contact_id    BIGINT NOT NULL REFERENCES contacts(id),
    from_campaign campaign_name,
    to_campaign   campaign_name NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'executed', 'failed')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    executed_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_campaign_queue_pending ON campaign_queue (status) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS daily_reports (
    id             BIGSERIAL PRIMARY KEY,
    report_date    DATE NOT NULL UNIQUE,
    report_data    JSONB NOT NULL,
    summary        TEXT,
    net_new_orders INT NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Opportunities
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS opportunities (
    id                 BIGSERIAL PRIMARY KEY,
    contact_id         BIGINT NOT NULL REFERENCES contacts(id),
    action             opportunity_action NOT NULL,
    priority           TEXT NOT NULL CHECK (priority IN ('hot', 'warm', 'cold')),
    reason             TEXT NOT NULL,
    suggested_message  TEXT,
    confidence_score   NUMERIC(3,2),
    status             opportunity_status NOT NULL DEFAULT 'pending',
    airtable_record_id TEXT,
    outcome_notes      TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    dispatched_at      TIMESTAMPTZ,
    completed_at       TIMESTAMPTZ,
    outcome            TEXT
);

CREATE INDEX IF NOT EXISTS idx_opportunities_pending ON opportunities (status) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_opportunities_contact ON opportunities (contact_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Communications: SMS, calls, delivery
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS telnyx_messages (
    id                BIGSERIAL PRIMARY KEY,
    contact_id        BIGINT NOT NULL REFERENCES contacts(id),
    direction         TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    from_number       TEXT NOT NULL,
    to_number         TEXT NOT NULL,
    body              TEXT,
    telnyx_msg_id     TEXT,
    status            TEXT,
    is_delivery_staff BOOLEAN DEFAULT false,
    source            TEXT NOT NULL DEFAULT 'telnyx_auto'
                          CHECK (source IN ('telnyx_auto', 'field_agent', 'delivery_staff')),
    agent_name        TEXT,
    metadata          JSONB DEFAULT '{}',
    sent_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_telnyx_msg_contact  ON telnyx_messages (contact_id, sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_telnyx_msg_delivery ON telnyx_messages (is_delivery_staff, sent_at DESC)
    WHERE is_delivery_staff = true;

CREATE TABLE IF NOT EXISTS telnyx_calls (
    id                BIGSERIAL PRIMARY KEY,
    contact_id        BIGINT NOT NULL REFERENCES contacts(id),
    direction         TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    from_number       TEXT NOT NULL,
    to_number         TEXT NOT NULL,
    duration_sec      INT,
    recording_url     TEXT,
    transcript        TEXT,
    summary           TEXT,
    is_delivery_staff BOOLEAN DEFAULT false,
    agent_name        TEXT,
    metadata          JSONB DEFAULT '{}',
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_telnyx_call_contact ON telnyx_calls (contact_id, started_at DESC);

CREATE TABLE IF NOT EXISTS delivery_status (
    id          BIGSERIAL PRIMARY KEY,
    contact_id  BIGINT NOT NULL REFERENCES contacts(id),
    order_ref   TEXT,
    status      delivery_status_type NOT NULL,
    updated_by  TEXT,
    notes       TEXT,
    location    TEXT,
    metadata    JSONB DEFAULT '{}',
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_delivery_contact ON delivery_status (contact_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_delivery_order   ON delivery_status (order_ref);

CREATE TABLE IF NOT EXISTS shipday_orders_raw (
    id                  BIGSERIAL PRIMARY KEY,
    shipday_order_id    TEXT NOT NULL UNIQUE,
    order_number        TEXT,
    contact_id          BIGINT REFERENCES contacts(id) ON DELETE SET NULL,
    customer_email      TEXT,
    customer_phone      TEXT,
    customer_name       TEXT,
    delivery_address    TEXT,
    shipday_status      TEXT,
    driver_name         TEXT,
    driver_phone        TEXT,
    estimated_delivery  TIMESTAMPTZ,
    actual_delivery     TIMESTAMPTZ,
    order_created_at    TIMESTAMPTZ,
    synced_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_payload         JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS shipday_communications (
    id                  BIGSERIAL PRIMARY KEY,
    contact_id          BIGINT REFERENCES contacts(id) ON DELETE SET NULL,
    shipday_order_id    TEXT NOT NULL,
    order_number        TEXT,
    comm_type           TEXT NOT NULL CHECK (comm_type IN (
                            'customer_feedback', 'delivery_instruction', 'proof_of_delivery')),
    content             TEXT,
    proof_image_urls    TEXT[],
    sentiment           TEXT CHECK (sentiment IN ('positive', 'negative', 'neutral') OR sentiment IS NULL),
    raw_payload         JSONB DEFAULT '{}',
    synced_at           TIMESTAMPTZ DEFAULT NOW(),
    occurred_at         TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_shipday_comm_uniq
    ON shipday_communications (shipday_order_id, comm_type);
CREATE INDEX IF NOT EXISTS idx_shipday_comm_contact
    ON shipday_communications (contact_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_shipday_comm_sentiment
    ON shipday_communications (sentiment, occurred_at DESC);

-- ---------------------------------------------------------------------------
-- Orders + menu
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS orders (
    id                BIGSERIAL PRIMARY KEY,
    contact_id        BIGINT REFERENCES contacts(id),
    order_id_external TEXT,
    order_date        DATE NOT NULL,
    delivery_date     DATE,
    source            TEXT NOT NULL DEFAULT 'Website',
    app_platform      TEXT,
    total_amount      NUMERIC(10,2) DEFAULT 0,
    order_type        TEXT,
    delivery_slot     TEXT,
    customer_name_raw TEXT,
    notes             TEXT,
    metadata          JSONB DEFAULT '{}',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_orders_contact       ON orders (contact_id, order_date DESC);
CREATE INDEX IF NOT EXISTS idx_orders_date          ON orders (order_date DESC);
CREATE INDEX IF NOT EXISTS idx_orders_source        ON orders (source);
CREATE INDEX IF NOT EXISTS idx_orders_delivery_date ON orders (delivery_date DESC);

CREATE TABLE IF NOT EXISTS order_items (
    id           BIGSERIAL PRIMARY KEY,
    order_id     BIGINT NOT NULL REFERENCES orders(id),
    menu_item_id BIGINT,    -- bare BIGINT, FK dropped (menu_items replaced by menu_catalog)
    item_name    TEXT NOT NULL,
    quantity     INT NOT NULL DEFAULT 1,
    unit_price   NUMERIC(8,2) DEFAULT 0,
    line_total   NUMERIC(8,2) DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items (order_id);

CREATE TABLE IF NOT EXISTS menu_catalog (
    id                 BIGSERIAL PRIMARY KEY,
    item_name          TEXT UNIQUE NOT NULL,
    category           TEXT,
    is_veg             BOOLEAN,
    description        TEXT,
    image_url          TEXT,
    price              NUMERIC(8,2),
    active             BOOLEAN NOT NULL DEFAULT TRUE,
    added_date         DATE,
    discarded_date     DATE,
    airtable_record_id TEXT,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_menu_catalog_airtable_id
    ON menu_catalog (airtable_record_id) WHERE airtable_record_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_menu_catalog_active ON menu_catalog (active);
CREATE INDEX IF NOT EXISTS idx_menu_catalog_name   ON menu_catalog (item_name);

CREATE TABLE IF NOT EXISTS menu_catalog_history (
    id              BIGSERIAL PRIMARY KEY,
    menu_catalog_id BIGINT NOT NULL REFERENCES menu_catalog(id),
    item_name       TEXT NOT NULL,
    change_type     TEXT NOT NULL,
    field_changed   TEXT,
    old_value       TEXT,
    new_value       TEXT,
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    source          TEXT DEFAULT 'airtable_sync'
);

CREATE INDEX IF NOT EXISTS idx_menu_history_item
    ON menu_catalog_history (menu_catalog_id, changed_at DESC);

-- ---------------------------------------------------------------------------
-- SMS templates + playbook + team content
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sms_templates (
    id           BIGSERIAL PRIMARY KEY,
    template_key TEXT UNIQUE NOT NULL,
    scenario     TEXT NOT NULL,
    sms_level    SMALLINT NOT NULL DEFAULT 1 CHECK (sms_level BETWEEN 1 AND 3),
    body         TEXT NOT NULL,
    variant      CHAR(1) NOT NULL DEFAULT 'A',
    is_active    BOOLEAN NOT NULL DEFAULT true,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sms_templates_scenario ON sms_templates (scenario, sms_level, is_active);

CREATE TABLE IF NOT EXISTS agent_playbook (
    id         BIGSERIAL PRIMARY KEY,
    rule_name  TEXT NOT NULL,
    category   TEXT NOT NULL DEFAULT 'general',
    instruction TEXT NOT NULL,
    priority   INT NOT NULL DEFAULT 50,
    is_active  BOOLEAN NOT NULL DEFAULT true,
    created_by TEXT DEFAULT 'system',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_playbook_rule_name'
          AND conrelid = 'agent_playbook'::regclass
    ) THEN
        ALTER TABLE agent_playbook ADD CONSTRAINT uq_playbook_rule_name UNIQUE (rule_name);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS team_content (
    id                   BIGSERIAL PRIMARY KEY,
    google_doc_id        TEXT,
    content_type         TEXT NOT NULL CHECK (content_type IN (
                             'ground_note', 'ad_copy', 'observation', 'question',
                             'customer_feedback', 'delivery_issue')),
    title                TEXT NOT NULL,
    body                 TEXT NOT NULL,
    author               TEXT,
    source               TEXT NOT NULL DEFAULT 'google_docs',
    google_last_modified TIMESTAMPTZ,
    doc_url              TEXT,
    tags                 TEXT[] DEFAULT '{}',
    is_active            BOOLEAN NOT NULL DEFAULT true,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_team_content_gdoc
    ON team_content (google_doc_id) WHERE google_doc_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_team_content_type    ON team_content (content_type);
CREATE INDEX IF NOT EXISTS idx_team_content_active  ON team_content (is_active, content_type);
CREATE INDEX IF NOT EXISTS idx_team_content_created ON team_content (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_team_content_fts
    ON team_content USING gin (to_tsvector('english', title || ' ' || body));

-- ---------------------------------------------------------------------------
-- AI Stack tables: customer_goals, contact_observations, action_plans,
--                  orchestrator_log, action_queue
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS customer_goals (
    id             SERIAL PRIMARY KEY,
    contact_id     INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    goal           TEXT NOT NULL CHECK (goal IN ('convert_to_order', 'retain', 'reactivate')),
    deadline       DATE,
    status         TEXT NOT NULL DEFAULT 'active'
                       CHECK (status IN ('active', 'achieved', 'expired', 'failed')),
    converted      BOOLEAN NOT NULL DEFAULT FALSE,
    progress_notes TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_customer_goals_contact ON customer_goals (contact_id);
CREATE INDEX IF NOT EXISTS idx_customer_goals_status  ON customer_goals (status);

CREATE TABLE IF NOT EXISTS contact_observations (
    id                    SERIAL PRIMARY KEY,
    contact_id            INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    goal_id               INTEGER REFERENCES customer_goals(id),
    run_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sentiment             TEXT CHECK (sentiment IN ('positive', 'neutral', 'negative')),
    sentiment_confidence  FLOAT,
    sentiment_summary     TEXT,
    intent                TEXT CHECK (intent IN (
                              'ready_to_order', 'needs_info', 'price_sensitive',
                              'not_interested', 'unknown')),
    intent_signals        JSONB DEFAULT '[]',
    intent_confidence     FLOAT,
    engagement_score      FLOAT CHECK (engagement_score BETWEEN 0 AND 1),
    engagement_trend      TEXT CHECK (engagement_trend IN ('rising', 'falling', 'flat')),
    last_touch_hours_ago  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_observations_contact ON contact_observations (contact_id);
CREATE INDEX IF NOT EXISTS idx_observations_run_at  ON contact_observations (run_at DESC);

CREATE TABLE IF NOT EXISTS action_plans (
    id                   SERIAL PRIMARY KEY,
    contact_id           INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    observation_id       INTEGER REFERENCES contact_observations(id),
    run_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    recommended_stage    TEXT,
    stage_confidence     FLOAT,
    stage_reason         TEXT,
    recommended_channel  TEXT CHECK (recommended_channel IN (
                             'email', 'sms', 'field_call', 'none')),
    channel_timing       TEXT CHECK (channel_timing IN (
                             'immediate', 'today', 'tomorrow', 'this_week', 'next_week')),
    channel_reason       TEXT,
    offer_type           TEXT CHECK (offer_type IN (
                             'none', 'discount', 'free_item', 'subscription', 'loyalty')),
    suggested_copy       TEXT,
    offer_reason         TEXT,
    should_escalate      BOOLEAN NOT NULL DEFAULT FALSE,
    escalation_urgency   TEXT CHECK (escalation_urgency IN ('low', 'medium', 'high')),
    escalation_reason    TEXT
);

CREATE INDEX IF NOT EXISTS idx_action_plans_contact ON action_plans (contact_id);
CREATE INDEX IF NOT EXISTS idx_action_plans_run_at  ON action_plans (run_at DESC);

CREATE TABLE IF NOT EXISTS orchestrator_log (
    id                          SERIAL PRIMARY KEY,
    contact_id                  INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    decision_recommendation_id  INTEGER REFERENCES action_plans(id),
    goal_id                     INTEGER REFERENCES customer_goals(id),
    run_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    chosen_action               TEXT,
    chosen_channel              TEXT,
    reasoning                   TEXT,
    guardrails_applied          JSONB DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS action_queue (
    id                  SERIAL PRIMARY KEY,
    contact_id          INTEGER,
    orchestrator_log_id INTEGER REFERENCES orchestrator_log(id),
    action_type         TEXT NOT NULL CHECK (action_type IN (
                            'send_sms', 'move_campaign', 'escalate_airtable', 'none',
                            'sync_airtable_task', 'log_query_to_airtable',
                            'push_instantly_lead', 'push_instantly_sequences',
                            'upload_google_drive', 'send_email_report')),
    payload             JSONB NOT NULL DEFAULT '{}',
    status              TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'executing', 'done', 'failed')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    executed_at         TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- Agent config + signals
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS agent_signal_config (
    id          SERIAL PRIMARY KEY,
    signal_key  TEXT NOT NULL UNIQUE,
    signal_value TEXT NOT NULL,
    description TEXT,
    category    TEXT DEFAULT 'threshold',
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Field agent reviews
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS field_agent_reviews (
    id                       BIGSERIAL PRIMARY KEY,
    contact_id               BIGINT REFERENCES contacts(id),
    call_id                  BIGINT REFERENCES telnyx_calls(id),
    opportunity_id           BIGINT REFERENCES opportunities(id),
    agent_name               TEXT,
    reviewed_at              TIMESTAMPTZ DEFAULT NOW(),
    pitch_quality_score      FLOAT,
    asked_for_order          BOOLEAN,
    customer_signal          TEXT,
    objections_raised        TEXT[],
    honest_assessment        TEXT,
    recommended_next_action  TEXT,
    next_action_timing       TEXT,
    next_action_reason       TEXT,
    follow_up_opportunity_id BIGINT,
    status                   TEXT DEFAULT 'pending'
);

CREATE INDEX IF NOT EXISTS idx_far_contact ON field_agent_reviews (contact_id);
CREATE INDEX IF NOT EXISTS idx_far_agent   ON field_agent_reviews (agent_name);
CREATE INDEX IF NOT EXISTS idx_far_date    ON field_agent_reviews (reviewed_at);

-- ---------------------------------------------------------------------------
-- Broadcast
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS broadcast_jobs (
    id               SERIAL PRIMARY KEY,
    title            TEXT NOT NULL,
    broadcast_type   TEXT NOT NULL CHECK (broadcast_type IN (
                         'promo', 'announcement', 'reactivation', 'event', 'custom')),
    channels         TEXT[] NOT NULL DEFAULT ARRAY['sms', 'email'],
    sms_message      TEXT,
    email_subject    TEXT,
    email_body       TEXT,
    target_type      TEXT NOT NULL CHECK (target_type IN (
                         'all_active', 'lapsed', 'segment', 'manual_list')),
    target_date      DATE,
    status           TEXT NOT NULL DEFAULT 'draft'
                         CHECK (status IN ('draft', 'scheduled', 'running', 'completed', 'cancelled')),
    total_recipients INT NOT NULL DEFAULT 0,
    sent_sms         INT NOT NULL DEFAULT 0,
    sent_email       INT NOT NULL DEFAULT 0,
    failed_count     INT NOT NULL DEFAULT 0,
    created_by       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at       TIMESTAMPTZ,
    completed_at     TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS broadcast_recipients (
    id            SERIAL PRIMARY KEY,
    job_id        INT NOT NULL REFERENCES broadcast_jobs(id) ON DELETE CASCADE,
    contact_id    BIGINT NOT NULL REFERENCES contacts(id),
    channel       TEXT NOT NULL CHECK (channel IN ('sms', 'email')),
    status        TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'sent', 'failed', 'skipped')),
    error_message TEXT,
    sent_at       TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (job_id, contact_id, channel)
);

-- ---------------------------------------------------------------------------
-- Chatbot
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS chatbot_doc_chunks (
    id           SERIAL PRIMARY KEY,
    source_file  TEXT NOT NULL,
    chunk_index  INTEGER NOT NULL,
    content      TEXT NOT NULL,
    content_tsv  TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chatbot_chunks_fts
    ON chatbot_doc_chunks USING gin (content_tsv);

CREATE TABLE IF NOT EXISTS chatbot_interactions (
    id         SERIAL PRIMARY KEY,
    question   TEXT NOT NULL,
    answer     TEXT NOT NULL,
    sources    TEXT[] DEFAULT '{}',
    model      TEXT DEFAULT 'claude-sonnet-4-5-20250929',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chatbot_doc_meta (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chatbot_canned_qa (
    question   TEXT PRIMARY KEY,
    answer     TEXT NOT NULL,
    sources    TEXT[] DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Vector embeddings (pgvector)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS content_embeddings (
    id           BIGSERIAL PRIMARY KEY,
    source_type  TEXT NOT NULL CHECK (source_type IN (
                     'transcript', 'sms', 'delivery_note',
                     'customer_profile', 'team_content')),
    source_id    BIGINT NOT NULL,
    contact_id   BIGINT NOT NULL REFERENCES contacts(id),
    content_text TEXT NOT NULL,
    embedding    vector(1536),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_embeddings_source  ON content_embeddings (source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_contact ON content_embeddings (contact_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_vector
    ON content_embeddings USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ---------------------------------------------------------------------------
-- Test runs
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS test_runs (
    id           TEXT PRIMARY KEY,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    triggered_by TEXT NOT NULL DEFAULT 'manual',
    status       TEXT NOT NULL DEFAULT 'running',
    total_tests  INT,
    passed       INT,
    failed       INT,
    skipped      INT,
    summary      JSONB,
    results      JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Growth / experiment tables
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS goal_experiments (
    id                      SERIAL PRIMARY KEY,
    hypothesis              TEXT NOT NULL,
    experiment_type         VARCHAR(50),
    status                  VARCHAR(20) NOT NULL DEFAULT 'pending',
    cohort_description      TEXT,
    cohort_filter           JSONB,
    cohort_sql              TEXT,
    action_type             VARCHAR(30) DEFAULT 'send_sms',
    message_template        TEXT,
    success_metric          VARCHAR(50) DEFAULT 'orders_placed_72h',
    success_threshold       FLOAT DEFAULT 0.10,
    cohort_size             INTEGER DEFAULT 0,
    enrolled_count          INTEGER DEFAULT 0,
    measurement_window_hours INTEGER DEFAULT 72,
    started_at              TIMESTAMPTZ,
    measurement_due_at      TIMESTAMPTZ,
    concluded_at            TIMESTAMPTZ,
    result_conversion_rate  FLOAT,
    result_success_count    INTEGER,
    result_sample_size      INTEGER,
    conclusion              VARCHAR(20),
    conclusion_notes        TEXT,
    generated_signal_id     INTEGER,
    source                  VARCHAR(50) DEFAULT 'goal_agent',
    hypothesis_hash         VARCHAR(64),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_goal_experiments_status     ON goal_experiments (status);
CREATE INDEX IF NOT EXISTS idx_goal_experiments_created_at ON goal_experiments (created_at);
CREATE INDEX IF NOT EXISTS idx_goal_experiments_source     ON goal_experiments (source);
CREATE UNIQUE INDEX IF NOT EXISTS goal_experiments_hypothesis_hash_key
    ON goal_experiments (hypothesis_hash) WHERE hypothesis_hash IS NOT NULL;

CREATE TABLE IF NOT EXISTS goal_experiment_contacts (
    id               SERIAL PRIMARY KEY,
    experiment_id    INTEGER NOT NULL REFERENCES goal_experiments(id) ON DELETE CASCADE,
    contact_id       INTEGER NOT NULL REFERENCES contacts(id),
    action_queue_id  INTEGER,
    message_sent     TEXT,
    enrolled_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    converted        BOOLEAN,
    conversion_at    TIMESTAMPTZ,
    outcome_checked_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_goal_exp_contacts_exp
    ON goal_experiment_contacts (experiment_id);
CREATE INDEX IF NOT EXISTS idx_goal_exp_contacts_contact
    ON goal_experiment_contacts (contact_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_goal_experiment_contacts_unique
    ON goal_experiment_contacts (experiment_id, contact_id);

CREATE TABLE IF NOT EXISTS discovered_signals (
    id                   SERIAL PRIMARY KEY,
    signal_name          VARCHAR(100) NOT NULL UNIQUE,
    signal_description   TEXT,
    source_experiment_id INTEGER REFERENCES goal_experiments(id),
    detection_sql        TEXT,
    confidence           FLOAT,
    activation_count     INTEGER NOT NULL DEFAULT 0,
    is_active            BOOLEAN NOT NULL DEFAULT TRUE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_discovered_signals_is_active ON discovered_signals (is_active);

CREATE TABLE IF NOT EXISTS goal_agent_runs (
    id                    SERIAL PRIMARY KEY,
    run_type              VARCHAR(50),
    experiments_created   INTEGER NOT NULL DEFAULT 0,
    experiments_started   INTEGER NOT NULL DEFAULT 0,
    contacts_enrolled     INTEGER NOT NULL DEFAULT 0,
    experiments_concluded INTEGER NOT NULL DEFAULT 0,
    signals_discovered    INTEGER NOT NULL DEFAULT 0,
    orders_attributed     INTEGER NOT NULL DEFAULT 0,
    reasoning             TEXT,
    error_detail          TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS competitor_agent_runs (
    id                    SERIAL PRIMARY KEY,
    sources_processed     INTEGER NOT NULL DEFAULT 0,
    email_samples_parsed  INTEGER NOT NULL DEFAULT 0,
    websites_scraped      INTEGER NOT NULL DEFAULT 0,
    hypotheses_queued     INTEGER NOT NULL DEFAULT 0,
    status                VARCHAR(20) NOT NULL DEFAULT 'completed',
    summary               TEXT,
    error_detail          TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_competitor_agent_runs_created_at
    ON competitor_agent_runs (created_at);

-- Growth hacker experiments (separate from goal_experiments — weekly campaign experiments)
CREATE TABLE IF NOT EXISTS experiments (
    id                BIGSERIAL PRIMARY KEY,
    name              TEXT NOT NULL,
    hypothesis        TEXT NOT NULL,
    experiment_type   TEXT NOT NULL,
    cohort_size       INT NOT NULL DEFAULT 0,
    channel           TEXT,
    message_template  TEXT,
    offer_detail      TEXT,
    metadata          JSONB NOT NULL DEFAULT '{}',
    status            TEXT NOT NULL DEFAULT 'running',
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    measure_at        TIMESTAMPTZ,
    measured_at       TIMESTAMPTZ,
    contacts_reached  INT DEFAULT 0,
    orders_won        INT DEFAULT 0,
    conversion_rate   NUMERIC(5,4) DEFAULT 0,
    is_winner         BOOLEAN,
    learnings         TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS experiment_contacts (
    id            BIGSERIAL PRIMARY KEY,
    experiment_id BIGINT NOT NULL REFERENCES experiments(id),
    contact_id    BIGINT NOT NULL REFERENCES contacts(id),
    opportunity_id BIGINT,
    action_sent   TEXT,
    message_sent  TEXT,
    sent_at       TIMESTAMPTZ,
    ordered       BOOLEAN NOT NULL DEFAULT false,
    order_id      BIGINT REFERENCES orders(id),
    ordered_at    TIMESTAMPTZ,
    days_to_order INT,
    UNIQUE (experiment_id, contact_id)
);

CREATE TABLE IF NOT EXISTS growth_baseline (
    id                BIGSERIAL PRIMARY KEY,
    measured_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    period_days       INT NOT NULL DEFAULT 7,
    total_contacts    INT NOT NULL DEFAULT 0,
    total_orders      INT NOT NULL DEFAULT 0,
    baseline_conv_rate NUMERIC(5,4) NOT NULL DEFAULT 0,
    notes             TEXT
);

-- ---------------------------------------------------------------------------
-- Campaign push log: records every attempt to push a lead to Instantly
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS campaign_push_log (
    id              SERIAL PRIMARY KEY,
    queue_id        INTEGER,        -- action_queue.id (nullable for test inserts)
    email           TEXT NOT NULL,
    to_campaign     TEXT NOT NULL,
    success         BOOLEAN NOT NULL DEFAULT false,
    status_code     INTEGER,
    error_message   TEXT,
    response_body   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_campaign_push_log_email
    ON campaign_push_log (email);
CREATE INDEX IF NOT EXISTS idx_campaign_push_log_created_at
    ON campaign_push_log (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_campaign_push_log_success
    ON campaign_push_log (success);
