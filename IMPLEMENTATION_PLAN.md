# DabbahWala Marketing System — Implementation Plan

Comprehensive implementation plan for the DabbahWala automated marketing platform. This document covers the full system design — from database schema and API layer through to the 4-layer Claude AI agent pipeline, n8n workflow automation, and external service integrations.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Tech Stack & Infrastructure](#2-tech-stack--infrastructure)
3. [Database Design](#3-database-design)
4. [API Layer](#4-api-layer)
5. [Claude AI Agent Pipeline](#5-claude-ai-agent-pipeline)
6. [Intelligence Cycle](#6-intelligence-cycle)
7. [n8n Workflow Automation](#7-n8n-workflow-automation)
8. [External Service Integration](#8-external-service-integration)
9. [Daily Order Processing](#9-daily-order-processing)
10. [Marketing Query Interface](#10-marketing-query-interface)
11. [MCP Server (Claude Desktop)](#11-mcp-server-claude-desktop)
12. [Deployment & CI/CD](#12-deployment--cicd)
13. [Implementation Status](#13-implementation-status)

---

## 1. Project Overview

### What Is DabbahWala?

DabbahWala is a fresh Indian food delivery service in Atlanta. The marketing system automates the entire customer lifecycle — from cold lead nurture through active customer engagement to lapsed customer reactivation — using a combination of:

- **Rule-based automation** (SQL lifecycle engine + n8n workflows)
- **AI-powered reasoning** (4-layer Claude agent pipeline)
- **Multi-channel outreach** (SMS via Telnyx, email via Instantly, field sales via Airtable)
- **Self-service intelligence** (marketing query form + Claude Desktop MCP)

### Goals

1. **Automate lifecycle management** — Contacts move through 8 lifecycle stages based on order frequency, engagement signals, and time-based rules
2. **AI-driven decision making** — Claude agents analyze each contact's sentiment, intent, and engagement to determine the optimal outreach action
3. **Multi-channel orchestration** — Actions are dispatched to the right channel (SMS, email, or field sales) at the right time
4. **Closed-loop measurement** — Outcomes are tracked back from Airtable to measure conversion effectiveness
5. **Team empowerment** — Ground team notes, ad copies, and playbook rules feed into AI decisions

---

## 2. Tech Stack & Infrastructure

### Application Layer

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Web framework | FastAPI (Python 3.11) | REST API for all system operations |
| HTTP client | httpx | Async calls to external services |
| AI SDK | anthropic (v0.49) | Claude agent pipeline calls |
| Data validation | Pydantic (v2.10) | Request/response schemas |
| File handling | python-multipart | CSV upload for daily orders |
| MCP protocol | mcp (v1.3) | Claude Desktop tool integration |
| Environment | python-dotenv | Configuration management |

### Infrastructure

| Component | Platform | Plan | Region |
|-----------|----------|------|--------|
| Web service | Render | Starter | Oregon |
| PostgreSQL 16 | Render | Starter | Oregon |
| n8n automation | Self-hosted (`digitalworker.dataskate.io`) | — | — |
| Source control | GitHub | — | — |
| CI/CD | GitHub Actions | — | — |

### External Services

| Service | Purpose |
|---------|---------|
| Anthropic Claude (Sonnet 4.5) | AI agent pipeline — inference, decision, orchestration, reporting |
| Telnyx | SMS sending/receiving, voice call tracking, field agent message logging |
| Instantly | Email campaign management — 5 lifecycle-mapped campaigns |
| Airtable | CRM, field sales task queue, playbook rules, outcome tracking |
| Shipday | Delivery tracking — order status, driver location, ETA |
| Google Docs | Ground team notes, social media ad copies |
| SMTP (Gmail/Outlook) | Daily report email delivery |

---

## 3. Database Design

### Schema: `dabbahwala` (PostgreSQL 16)

33 migrations define the complete schema. Tables are organized into four groups.

### 3.1 Core Tables

**`contacts`** — Master customer record
- `id`, `email`, `phone`, `first_name`, `last_name`
- `lifecycle_segment` (enum: cold, engaged, active_customer, new_customer, lapsed_customer, reactivation_candidate, cooling, optout)
- `email_promo_enabled`, `sms_promo_enabled`, `sms_level`
- `total_orders`, `last_order_date`, `current_campaign`
- `created_at`, `updated_at`

**`events`** — Raw event intake
- `id`, `contact_id` (FK), `event_type` (enum), `metadata` (JSONB), `occurred_at`
- Event types: order_placed, email_open, email_click, email_reply, sms_sent, sms_received, delivery_completed, delivery_failed, call_completed, etc.

**`orders`** — Order records
- `id`, `contact_id` (FK), `order_ref`, `order_date`, `total_amount`
- `delivery_slot`, `order_type` (delivery/pickup), `address`

**`order_items`** — Line items per order
- `id`, `order_id` (FK), `menu_item_id` (FK), `quantity`, `unit_price`, `dish_name`

**`menu_items`** — Master menu catalog
- `id`, `item_name`, `category`, `is_veg`, `avg_price`

**`menu_item_aliases`** — Alias resolution for CSV imports
- `id`, `alias_name`, `menu_item_id` (FK)

### 3.2 Communication Tables

**`telnyx_messages`** — SMS tracking
- `id`, `contact_id` (FK), `direction` (inbound/outbound)
- `from_number`, `to_number`, `body`, `status`
- `source` (telnyx_auto / field_agent / delivery_staff), `agent_name`
- `is_delivery_staff`, `telnyx_msg_id`, `metadata` (JSONB)
- `sent_at`

**`telnyx_calls`** — Call tracking
- `id`, `contact_id` (FK), `direction`, `from_number`, `to_number`
- `duration_sec`, `transcript`, `summary`
- `is_delivery_staff`, `started_at`, `ended_at`

**`delivery_status`** — Delivery events
- `id`, `contact_id` (FK), `order_ref`, `status`
- `updated_by`, `notes`, `location`, `occurred_at`

### 3.3 Agent Pipeline Tables (migration 032)

**`customer_goals`** — One active goal per contact
- `goal` (convert_to_order / retain / reactivate)
- `deadline`, `status` (active / achieved / expired / failed), `converted`
- `progress_notes`

**`inference_results`** — Layer 1 outputs per cycle run
- Sentiment: `sentiment`, `sentiment_confidence`, `sentiment_summary`
- Intent: `intent`, `intent_signals` (JSONB), `intent_confidence`
- Engagement: `engagement_score` (0-1), `engagement_trend`, `last_touch_hours_ago`

**`decision_recommendations`** — Layer 2 outputs per cycle run
- Stage: `recommended_stage`, `stage_confidence`, `stage_reason`
- Channel: `recommended_channel`, `channel_timing`, `channel_reason`
- Offer: `offer_type`, `suggested_copy`, `offer_reason`
- Escalation: `should_escalate`, `escalation_urgency`, `escalation_reason`

**`orchestrator_log`** — Layer 3 audit trail
- `chosen_action`, `chosen_channel`, `reasoning`, `guardrails_applied` (JSONB)

**`action_queue`** — Approved actions awaiting execution
- `action_type` (send_sms / move_campaign / escalate_airtable / none)
- `payload` (JSONB), `status` (pending / executing / done / failed)
- `created_at`, `executed_at`

### 3.4 Configuration & Analytics Tables

**`rules`** — Lifecycle rule engine
- `predicate_sql` (WHERE clause), `action` (transition / queue_campaign / send_sms)
- `from_segment`, `to_segment`, `priority`, `active`

**`campaign_routing`** — Lifecycle -> Instantly campaign mapping
- `lifecycle_segment`, `default_campaign`, `instantly_campaign_id`, `instantly_campaign_name`

**`campaign_queue`** — Pending campaign moves
- `contact_id`, `from_campaign`, `to_campaign`, `status` (pending / executed)

**`agent_playbook`** — User-configured rules (Airtable sync)
- `category` (exclusion / priority / inference / decision / messaging / general)
- `rule_text`, `source` (airtable / manual), `active`

**`sms_templates`** — SMS A/B testing variants
- `template_name`, `variant`, `body`, `lifecycle_segment`, `active`

**`team_content`** — Ground team notes, ad copies
- `content_type` (ground_note / ad_copy / observation / question)
- `title`, `body`, `author`, `tags` (JSONB)
- `google_doc_id`, `source` (google_docs / form_submission)

**`engagement_rollups`** — Materialized 7d/30d metrics
- `opens_7d`, `opens_30d`, `clicks_7d`, `clicks_30d`, `sms_sent_30d`, `orders_90d`

**`opportunities`** — Conversion opportunities
- `signal_type`, `confidence_score`, `action` (enum), `status` (pending / dispatched / outcome_recorded)

**`decision_log`** — Lifecycle transition audit trail
**`daily_reports`** — Aggregated daily metrics

### 3.5 Stored Functions

| Function | Migration | Purpose |
|----------|-----------|---------|
| `run_lifecycle_cycle()` | 011 | Main rule engine — evaluate predicates, transition segments, queue campaigns |
| `refresh_engagement_rollups()` | 009 | Recalculate 7d/30d engagement from events |
| `evaluate_rules()` | 010 | Core rule evaluation loop |
| `ingest_event()` | 030 | Event ingestion with audit trail and type validation |
| `store_telnyx_message()` | 017/033 | SMS storage with field agent support |
| `store_telnyx_call()` | 017 | Call record storage |
| `update_delivery_status()` | 017 | Delivery event processing |
| `create_opportunity()` | 018 | Opportunity creation with deduplication |
| `mark_opportunity_dispatched()` | 018 | Mark as dispatched to Airtable |
| `update_opportunity_outcome()` | 018 | Record outcome |
| `get_contact_detail()` | 020 | Full contact profile with all history |
| `search_contacts()` | 020 | Filtered contact search |
| `get_communication_history()` | 020/033 | SMS + calls + deliveries for a contact |
| `get_lifecycle_summary()` | 019 | Pipeline snapshot (contacts per segment) |
| `get_campaign_performance()` | 019 | Campaign stats (opens, clicks, orders) |
| `get_engagement_trends()` | 019 | Engagement metrics over time |
| `suggest_reactivation_targets()` | 021 | Find contacts most likely to reactivate |
| `recommend_content_strategy()` | 021 | Full context bundle for Claude analysis |
| `generate_daily_report()` | 015 | Aggregate metrics for a date |

---

## 4. API Layer

### FastAPI Application (`app/main.py`)

15 routers mounted under `/api/` prefix. Admin endpoints at root level.

### 4.1 Router Map

| Router | Prefix | Module | Lines |
|--------|--------|--------|-------|
| Agent Pipeline | `/api/agents` | `routers/agents.py` | ~1000 |
| Intelligence | `/api/intelligence` | `routers/intelligence.py` | ~400 |
| Daily Orders | `/api/daily-orders` | `routers/daily_orders.py` | ~350 |
| Smart Agent | `/api/agent` | `routers/agent.py` | ~200 |
| Marketing Query | `/api/query` | `routers/query.py` | ~600 |
| Lifecycle | `/api/lifecycle` | `routers/lifecycle.py` | ~50 |
| Campaigns | `/api/campaigns` | `routers/campaigns.py` | ~80 |
| Opportunities | `/api/opportunities` | `routers/opportunities.py` | ~300 |
| SMS | `/api/sms` | `routers/sms.py` | ~80 |
| Telnyx | `/api/telnyx` | `routers/telnyx.py` | ~150 |
| Delivery | `/api/delivery` | `routers/delivery.py` | ~100 |
| Playbook | `/api/playbook` | `routers/playbook.py` | ~200 |
| Team Content | `/api/team-content` | `routers/team_content.py` | ~200 |
| Reports | `/api/reports` | `routers/reports.py` | ~80 |
| Events | `/api/events` | `routers/events.py` | ~100 |

### 4.2 Admin Endpoints

| Endpoint | Purpose | Auth |
|----------|---------|------|
| `GET /health` | Health check (DB connectivity) | None |
| `POST /admin/migrate/{num}` | Run specific migration | `ADMIN_SECRET` |
| `POST /admin/query` | Read-only SQL execution | `ADMIN_SECRET` |
| `POST /admin/exec` | Write SQL execution | `ADMIN_SECRET` |

### 4.3 Database Connection

`app/db.py` provides:
- `SimpleConnectionPool` (1-10 connections)
- `get_connection()` — acquire from pool, set `search_path` to `dabbahwala`
- `get_cursor()` — context manager yielding `RealDictCursor` (rows as dicts)
- Automatic transaction commit/rollback

---

## 5. Claude AI Agent Pipeline

### 5.1 Overview

The agent stack (`routers/agents.py`, ~1000 lines) implements a 4-layer pipeline where each layer's output feeds the next. All agent calls use `claude-sonnet-4-5-20250929` with forced tool use for structured output.

### 5.2 Layer 1 — Inference (3 parallel agents)

Each agent receives: contact profile, 30-day events, full communication history, active goal.

**Sentiment Agent:**
- System prompt: "You are a customer sentiment analyst for a food delivery service"
- Tool: `record_sentiment` with fields: sentiment, confidence, summary
- Output: positive/neutral/negative classification with explanation

**Intent Agent:**
- System prompt: "You are a customer intent classifier for a food delivery service"
- Tool: `record_intent` with fields: intent, signals[], confidence
- Output: ready_to_order/needs_info/price_sensitive/not_interested/unknown

**Engagement Agent:**
- System prompt: "You are a customer engagement scorer for a food delivery service"
- Tool: `record_engagement` with fields: engagement_score, trend, last_touch_hours_ago
- Output: 0-1 score with rising/flat/falling trend

### 5.3 Layer 2 — Decision (4 parallel agents)

Each agent receives: contact profile + full Layer 1 inference bundle.

**Stage Agent:** Recommends lifecycle segment transition
**Channel Agent:** Recommends outreach channel + timing
**Offer Agent:** Recommends offer type + suggested copy
**Escalation Agent:** Recommends whether to escalate to human field agent

### 5.4 Layer 3 — Orchestrator (single agent)

Receives: all Layer 2 recommendations + latest delivery event.

**Key feature: Delivery-aware guardrails**
- `delivered` -> thank-you SMS with reorder nudge (24h cooldown)
- `delivery_failed` / `delivery_returned` -> high-urgency Airtable escalation
- `out_for_delivery` / `driver_assigned` -> no action (order in flight)

**General guardrails:**
- Max 1 contact per 24h on same channel
- Max 3 SMS per week per contact
- Escalation always beats automation
- `not_interested` -> always none

**Output:** Exactly one action inserted into `action_queue`.

### 5.5 Layer 4 — Report Agents (daily)

**Activity Report** (8:00 AM):
- Queries agent runs, actions taken, escalations in last 24h
- Claude generates HTML summary
- Sent via SMTP with CSV attachment

**Outcome Report** (8:30 AM):
- Queries orders placed, email opens, goal achievements in last 24h
- Claude generates HTML summary
- Sent via SMTP with CSV attachment

### 5.6 Playbook Integration

The agent playbook (`agent_playbook` table, synced from Airtable) injects user-configured rules into Claude system prompts. Categories:
- **exclusion** — "Never contact X" / "Skip contacts in cooling"
- **priority** — "Prioritize contacts with 3+ orders"
- **inference** — "If SMS response mentions 'price', classify as price_sensitive"
- **decision** — "Always use SMS for reactivation, never email"
- **messaging** — "Include delivery slot info in thank-you messages"
- **general** — Open-ended instructions

---

## 6. Intelligence Cycle

### 6.1 5-Phase Hourly Cycle (`routers/intelligence.py`)

**Phase 1 — INTAKE:**
- Poll Instantly for new email events (opens, clicks, replies)
- Count recent Telnyx SMS/calls
- Return event counts

**Phase 2 — EVIDENCE:**
- Refresh engagement rollups (7-day rolling metrics)
- Calculate lifecycle distribution snapshot

**Phase 3 — INFERENCE (7 signal types):**

| Signal | Detection Logic |
|--------|----------------|
| `engaged_no_order` | 3+ opens or clicks in 7 days, no order in 7 days |
| `new_customer_no_repeat` | Exactly 1 order, 5+ days since first order, no repeat |
| `lapsed_reengaged` | Lapsed segment + recent SMS reply or email click |
| `reorder_intent` | Call transcript contains reorder keywords |
| `app_customers_for_conversion` | Orders via app, never ordered direct |
| `subscription_candidates` | 3+ one-time orders in 30 days, regular cadence |
| `high_value_at_risk` | 5+ total orders, no order in 14+ days |

**Phase 4 — DECISION:**
- Create opportunities from high-confidence signals
- Queue campaign moves (e.g., APP_TO_DIRECT for app customers)
- Queue SMS for key segments

**Phase 5 — EXECUTION:**
- Run lifecycle rule engine (`run_lifecycle_cycle()`)
- Prepare dispatch batches
- Return pending actions for n8n

---

## 7. n8n Workflow Automation

### 7.1 Workflow Inventory (15 total)

All workflows connect to the FastAPI backend at `https://dabbahwala-latest.onrender.com`.

#### Data Collection Workflows

| Workflow | Schedule | What It Does |
|----------|----------|-------------|
| **Telnyx Inbound Collector** | Every 30 min | Polls Telnyx API for inbound SMS/calls. For each message: `POST /api/telnyx/message`. Triggers real-time agent cycle via `POST /api/agents/cycle/run-for-contact`. |
| **Shipday Delivery Collector** | Every 30 min | Polls Shipday API for delivery status updates. Maps Shipday statuses to DabbahWala delivery events. `POST /api/delivery/status`. |
| **Daily Order Upload** | Daily 1 PM EST (Mon-Sat) | Fetches daily order CSV. Uploads via `POST /api/daily-orders/process`. Triggers full agent cycle via `POST /api/agents/cycle/run-all`. |
| **Google Docs Sync** | Every 30 min | Polls Google Drive folder for new/updated docs. Classifies as ground_note or ad_copy. Syncs via `POST /api/team-content/sync`. |

#### Processing Workflows

| Workflow | Schedule | What It Does |
|----------|----------|-------------|
| **Agent Orchestration Cron** | Every 3 h | Runs `POST /api/agents/cycle/run-all` — processes all contacts with active goals or high engagement through the full 3-layer agent pipeline. |
| **Hourly Intelligence Cycle** | Hourly | Runs `POST /api/intelligence/run-cycle` — the 5-phase INTAKE->EVIDENCE->INFERENCE->DECISION->EXECUTION cycle. |
| **Lifecycle Cycle Runner** | Hourly | Runs `POST /api/lifecycle/run` — the SQL rule engine for segment transitions and campaign queuing. |
| **Airtable Playbook Sync** | Every 15 min | Syncs playbook rules from Airtable to `agent_playbook` table via `POST /api/playbook/sync-from-airtable`. |

#### Execution Workflows

| Workflow | Schedule | What It Does |
|----------|----------|-------------|
| **SMS Dispatch** | Every 10 min | Polls `GET /api/agents/action-queue/pending` for send_sms actions. Sends via Telnyx API. Marks done via `POST /api/agents/action-queue/{id}/done`. |
| **Action Queue Executor** | Every 30 min | Polls action_queue for non-SMS actions (move_campaign, escalate_airtable). Executes via Instantly/Airtable APIs. |
| **Airtable Outcome Sync** | Every 15 min | Polls Airtable "Field Sales Tasks" for updated outcomes. Updates opportunities via `POST /api/opportunities/{id}/outcome`. |

#### Reporting Workflows

| Workflow | Schedule | What It Does |
|----------|----------|-------------|
| **Daily Activity Report** | Daily 8:00 AM | Triggers `POST /api/agents/report/activity`. Claude generates HTML summary + CSV of agent runs, actions, and escalations. Emails to `core@dabbahwala.com`. |
| **Daily Outcome Report** | Daily 8:30 AM | Triggers `POST /api/agents/report/outcome`. Claude generates HTML summary + CSV of orders, conversions, and goal achievements. |
| **Daily Report Generator** | Daily 11 PM | Legacy report. Runs `POST /api/reports/daily/{date}`. |

#### Interactive Workflows

| Workflow | Trigger | What It Does |
|----------|---------|-------------|
| **Marketing Query Form** | On-demand (form submission) | Provides a web form at `https://digitalworker.dataskate.io/form/marketing-query-form`. Routes queries to the API's 10 Tier-1 SQL categories + 1 Tier-2 Claude category. |

### 7.2 Version Control

13 of 15 workflows are version-controlled as JSON files in `n8n/`. The GitHub Action `.github/workflows/sync_n8n.yml` auto-syncs these files to the n8n instance on every push to `main`.

---

## 8. External Service Integration

### 8.1 Telnyx (SMS + Voice)

- **Outbound SMS:** Sent via Telnyx API from `+18444322224`
- **Inbound SMS:** Polled by Telnyx Inbound Collector workflow
- **Call transcripts:** Stored with duration, transcript text, and AI-generated summary
- **Field agent logging:** `POST /api/telnyx/field-agent-message` logs SMS sent by field agents from personal phones

### 8.2 Instantly (Email Campaigns)

5 lifecycle-mapped campaigns:

| Campaign ID | Name | Target Segment |
|-------------|------|---------------|
| `90ecd160-...` | DW-NurtureSlow-ColdContacts | cold |
| `30292b3d-...` | DW-PromoStandard-ActiveEngaged | engaged, active_customer |
| `c9af877a-...` | DW-PromoAggressive-LapsedCustomers | lapsed_customer |
| `c4c42e73-...` | DW-NewCustomerOnboarding | new_customer |
| `0c760ec8-...` | DW-Reactivation-LongDormant | reactivation_candidate |

Campaign routing is defined in `campaign_routing` table and updated by migrations 014, 023, 026, 031.

### 8.3 Airtable (CRM + Playbook)

- **Field Sales Tasks:** Opportunities are dispatched as Airtable records. Field agents update outcomes (ordered, not_interested, follow_up, etc.)
- **Playbook Rules:** User-configurable rules synced every 15 min. Categories: exclusion, priority, inference, decision, messaging, general.
- **Team Content:** Airtable can also store team observations (synced via form submissions)

### 8.4 Shipday (Delivery Tracking)

- Polled every 30 min by Shipday Delivery Collector
- Statuses mapped: `delivered`, `delivery_failed`, `delivery_returned`, `out_for_delivery`, `driver_assigned`
- Delivery events feed the orchestrator agent's delivery-aware guardrails

### 8.5 Google Docs (Team Content)

- Google Drive folder `1O0ES9uiDL6AWf9QMMYiyRUWGtymDjPF5` is polled every 30 min
- Documents classified by title keywords:
  - "ad copy", "social media", "facebook", "instagram" -> `ad_copy`
  - Everything else -> `ground_note`
- Content stored in `team_content` table with `google_doc_id` dedup

---

## 9. Daily Order Processing

### 9.1 CSV Upload Pipeline (`routers/daily_orders.py`)

**Endpoint:** `POST /api/daily-orders/process`

**Input:** CSV file with columns: Order Number, Date, Customer Name, Phone, Address, Dish Name, Quantity, Unit Price, Delivery Slot, Order Type

**Processing steps:**

1. **Parse CSV** — Read all rows, group by order number
2. **Resolve contacts** — Match by phone number, then name, then fuzzy match. Create new contact if no match.
3. **Create orders** — Insert into `orders` table with total amount, delivery slot, order type
4. **Resolve menu items** (5-step pipeline):
   - Exact match against `menu_items`
   - Alias lookup in `menu_item_aliases`
   - Normalized match (case-insensitive)
   - Fuzzy match (SequenceMatcher, 85% threshold)
   - Create new item with price from CSV
5. **Create order items** — Insert into `order_items`
6. **Fire events** — `order_placed` event for each order
7. **Detect opportunities:**
   - Lapsed customer returning (was lapsed, placed order)
   - First-time customer (new contact)
   - App customer converting to direct
8. **Run lifecycle cycle** — Update segments based on new orders

**Output:** Summary with counts (orders processed, items created, revenue, contacts matched/created, opportunities detected, menu items matched/created)

---

## 10. Marketing Query Interface

### 10.1 Self-Service Intelligence (`routers/query.py`)

Two tiers of query handling:

**Tier 1 — Direct SQL (fast, free):**

| Category | What It Returns |
|----------|----------------|
| `customer_lookup` | Full customer profile by email |
| `pipeline_snapshot` | Lifecycle segment distribution |
| `campaign_performance` | Campaign stats (opens, clicks, orders) for last 30 days |
| `who_to_contact` | Pending opportunities + reactivation targets |
| `daily_summary` | Today's orders, events, transitions |
| `order_analytics` | Top dishes, daily order volume, repeat rate |
| `communication_history` | SMS + calls for a specific customer |
| `ground_team_notes` | Browse/search field notes |
| `ad_copies` | Browse social media ad copies |
| `submit_input` | Store team observations/questions |

**Tier 2 — Claude with Real Data Context (~$0.02/query):**

| Category | What It Does |
|----------|-------------|
| `free_form` | Any marketing question. Claude receives: lifecycle distribution, order stats, top dishes, recent transitions, playbook rules, team content. Responds with actionable insights. |

### 10.2 n8n Form Integration

The Marketing Query Form workflow provides a web UI at `https://digitalworker.dataskate.io/form/marketing-query-form`. Submissions are routed to `POST /api/query` and results stored in Airtable.

---

## 11. MCP Server (Claude Desktop)

### 11.1 Architecture

`mcp_server/server.py` registers tools from 6 modules using the FastMCP framework. Connects directly to PostgreSQL.

### 11.2 Tool Groups

**Contacts** (`tools/contacts.py`):
- `get_contact_detail(email_or_id)` — Full profile with events, orders, communications
- `search_contacts(segment, flags, order_range, limit)` — Filtered search

**Analytics** (`tools/analytics.py`):
- `get_lifecycle_summary()` — Pipeline snapshot
- `get_campaign_performance(campaign, days)` — Campaign metrics
- `get_engagement_trends(days)` — Engagement over time

**Communications** (`tools/communications.py`):
- `get_communication_history(contact_id, days)` — SMS, calls, deliveries

**Recommendations** (`tools/recommendations.py`):
- `suggest_reactivation_targets(limit)` — Best reactivation candidates
- `recommend_content_strategy(contact_id)` — Full context for per-contact analysis

**Opportunities** (`tools/opportunities.py`):
- `detect_opportunities()` — Run all signal detectors
- `create_opportunity()` — Create from detection
- `get_high_intent_signals()` — Contacts showing purchase intent

**Agents** (`tools/agents.py`):
- `get_latest_inference(contact_id)` — Most recent Layer 1 results
- `get_latest_decision(contact_id)` — Most recent Layer 2 results
- `get_orchestrator_history(contact_id)` — Layer 3 audit trail
- `get_pending_actions(limit)` — Actions awaiting execution
- `get_agent_cycle_summary(days)` — Aggregated agent stats

---

## 12. Deployment & CI/CD

### 12.1 Render Deployment

**`render.yaml`** defines the infrastructure blueprint:

```yaml
services:
  - type: web
    name: dabbahwala-api
    runtime: python
    buildCommand: ./scripts/render_build.sh
    startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT
    healthCheckPath: /health

databases:
  - name: dabbahwala-db
    postgresMajorVersion: 16
```

**Build script** (`scripts/render_build.sh`):
1. Install Python dependencies
2. Run all migrations in order (`scripts/run_migrations.sh`)

### 12.2 GitHub Actions

**`.github/workflows/sync_n8n.yml`** — n8n Workflow Sync

Triggered on push to `main` when `n8n/**.json` files change:
1. Fetch existing workflows from n8n API
2. For each JSON file:
   - If workflow exists: PUT update + reactivate if previously active
   - If new: POST create
3. Uses `N8N_API_KEY` GitHub secret

### 12.3 Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `ANTHROPIC_API_KEY` | Yes | Claude agent pipeline |
| `TELNYX_API_KEY` | Yes | SMS/voice |
| `AIRTABLE_API_KEY` | Yes | CRM + playbook sync |
| `AIRTABLE_BASE_ID` | Yes | Airtable base identifier |
| `SHIPDAY_API_KEY` | Yes | Delivery tracking |
| `SMTP_HOST` | Yes | Report email relay |
| `SMTP_USER` | Yes | SMTP authentication |
| `SMTP_PASSWORD` | Yes | SMTP authentication |
| `REPORT_EMAIL_TO` | No | Report recipient (default: `core@dabbahwala.com`) |
| `ADMIN_SECRET` | Yes | Admin endpoint protection |
| `N8N_API_KEY` | No | n8n API access (for GitHub Action) |
| `INSTANTLY_API_KEY` | No | Instantly email campaigns |

---

## 13. Implementation Status

### Completed

| Component | Status | Details |
|-----------|--------|---------|
| PostgreSQL schema | Done | 33 migrations, all deployed |
| FastAPI application | Done | 15 routers, ~80+ endpoints |
| Contact management | Done | CRUD, lifecycle tracking, phone/email matching |
| Event ingestion | Done | All event types, stored procedures, audit trail |
| Lifecycle rule engine | Done | SQL-based, hourly execution via n8n |
| Campaign routing | Done | 5 Instantly campaigns mapped to lifecycle segments |
| SMS integration | Done | Telnyx send/receive, field agent logging |
| Delivery tracking | Done | Shipday polling, delivery-aware agent guardrails |
| Daily order processing | Done | CSV upload, 5-step menu resolution, opportunity detection |
| Intelligence cycle | Done | 5-phase hourly cycle with 7 signal types |
| Agent pipeline | Done | 4-layer Claude stack (inference, decision, orchestrator, reporting) |
| Action queue | Done | Pending -> executing -> done/failed lifecycle |
| Agent playbook | Done | Airtable-synced rules injected into Claude prompts |
| Marketing query form | Done | 10 Tier-1 SQL + 1 Tier-2 Claude categories |
| Team content | Done | Google Docs sync, ground notes, ad copies |
| MCP server | Done | 30+ tools across 6 groups |
| n8n workflows | Done | 15 workflows, 13 version-controlled |
| GitHub Actions CI/CD | Done | Auto-sync n8n on push to main |
| Daily reports | Done | Activity + Outcome reports via email (HTML + CSV) |
| Opportunity management | Done | Detection, dispatch to Airtable, outcome tracking |

### Operational Metrics

- **API endpoints:** ~80+
- **Database tables:** 20+
- **Stored functions:** 15+
- **n8n workflows:** 15
- **MCP tools:** 30+
- **Claude agent calls per contact cycle:** 8 (3 inference + 4 decision + 1 orchestrator)
- **Signal types detected:** 7
- **Lifecycle segments:** 8
- **Email campaigns:** 5
- **Migration files:** 33

---

*This document reflects the system as implemented. For architecture details, see [ARCHITECTURE.md](ARCHITECTURE.md). For quick reference, see [README.md](README.md).*
