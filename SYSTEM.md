# DabbahWala — System Reference

Complete technical reference for the DabbahWala automated marketing platform.

> **Navigation:** [README](README.md) · [Features](FEATURES.md) · [Claude Instructions](CLAUDE.md)
>
> **Deep-dive reading:** [LIFECYCLE.md](LIFECYCLE.md) — plain-language explanation of how the lifecycle engine, intelligence engine, and AI pipeline work together to convert customers, including why all three are necessary and not redundant.

---

## Table of Contents

1. [What Is DabbahWala](#1-what-is-dabbahwala)
2. [Infrastructure & Stack](#2-infrastructure--stack)
3. [System Architecture](#3-system-architecture)
4. [Database Schema](#4-database-schema)
5. [API Layer](#5-api-layer)
6. [Claude AI Agent Pipeline](#6-claude-ai-agent-pipeline)
7. [Intelligence Cycle](#7-intelligence-cycle)
8. [n8n Workflow Layer](#8-n8n-workflow-layer)
9. [External Service Integrations](#9-external-service-integrations)
10. [MCP Server (Claude Desktop)](#10-mcp-server-claude-desktop)
11. [Deployment & CI/CD](#11-deployment--cicd)
12. [Operational Metrics](#12-operational-metrics)

---

## 1. What Is DabbahWala

DabbahWala is a fresh Indian food delivery service in Atlanta. This backend system automates the entire customer lifecycle — from cold lead nurture through active customer engagement to lapsed customer reactivation — using:

- **Rule-based automation** — SQL lifecycle engine + n8n workflows
- **AI-powered reasoning** — 4-layer Claude agent pipeline (8 Claude calls per contact per cycle)
- **Multi-channel outreach** — SMS (Telnyx), email (Instantly), field sales (Airtable)
- **Self-service intelligence** — Marketing query form + Claude Desktop MCP tools

---

## 2. Infrastructure & Stack

### Application

| Component | Technology |
|-----------|-----------|
| Web framework | FastAPI (Python 3.11) |
| AI SDK | anthropic (v0.49) — all agents use `claude-sonnet-4-5-20250929` |
| MCP protocol | mcp (v1.3) — Claude Desktop tool integration |
| HTTP client | httpx (async external calls) |
| Data validation | Pydantic v2.10 |
| Environment | python-dotenv |

### Infrastructure

| Component | Platform | Notes |
|-----------|----------|-------|
| Web service | Render (Starter) — Oregon | Auto-deploys on push to `main` |
| PostgreSQL 16 (Supabase) | pooler.supabase.com — transaction mode (port 6543) | Schema: `dabbahwala`; search_path set via connection startup option |
| n8n automation | Self-hosted `digitalworker.dataskate.io` | 25 workflows |
| CI/CD | GitHub Actions | — |

### External Services

| Service | Purpose | Auth env var |
|---------|---------|-------------|
| Anthropic Claude | Agent pipeline | `ANTHROPIC_API_KEY` |
| Telnyx | SMS/voice | `TELNYX_API_KEY` |
| Instantly | Email campaigns | `INSTANTLY_API_KEY` |
| Airtable | CRM, playbook, outcomes | `AIRTABLE_API_KEY` |
| Shipday | Delivery tracking | `SHIPDAY_API_KEY` |
| Google Drive/Docs | Team content sync | OAuth2 (n8n creds) |
| Gmail SMTP | Report delivery | n8n cred `Sk6XzPNPnJTXHEbr` |

### Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `ANTHROPIC_API_KEY` | Yes | Claude agent calls |
| `TELNYX_API_KEY` | Yes | SMS/voice |
| `AIRTABLE_API_KEY` | Yes | CRM + playbook sync |
| `AIRTABLE_BASE_ID` | Yes | `appuy2VTIao6XVpIW` |
| `SHIPDAY_API_KEY` | Yes | Delivery tracking |
| `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD` | Yes | Report emails |
| `ADMIN_SECRET` | Yes | Admin endpoint protection |
| `REPORT_EMAIL_TO` | No | Report recipient (default: `core@dabbahwala.com`) |
| `INSTANTLY_API_KEY` | No | Instantly campaigns |
| `N8N_API_KEY` | No | n8n API (GitHub Action sync) |

---

## 3. System Architecture

### High-Level Data Flow

```
INPUTS
  Telnyx (SMS/calls)  ·  Shipday (delivery)  ·  Instantly (email events)
  Daily CSV orders    ·  Google Docs (team notes)  ·  Airtable (menu/playbook)
          │
          ▼
  FastAPI  (dabbahwala-latest.onrender.com)
          │
  /events/ingest  ──→  ingest_event() SP  ──→  events table
          │
  /agents/cycle/run-for-contact  (real-time, post-event)
  Agent Orchestration Cron       (every 3 h, batch)
    ├─ Layer 1: Sentiment · Intent · Engagement      (3 parallel Claude calls)
    ├─ Layer 2: Stage · Channel · Offer · Escalation (4 parallel Claude calls)
    ├─ Layer 3: Orchestrator                          (1 Claude call)
    └──────────────────────────────────────→  action_queue (pending)
          │
  n8n  (digitalworker.dataskate.io)
    ├─ Action Queue Executor  ──→  Telnyx / Instantly / Airtable
    ├─ SMS Dispatch           ──→  Telnyx
    ├─ Broadcast Dispatch     ──→  Telnyx + SMTP
    ├─ Hourly Intelligence    ──→  /intelligence/run-cycle
    ├─ Lifecycle Runner       ──→  /lifecycle/run
    └─ Data collectors        ──→  Shipday / Telnyx / Google Docs / Airtable
```

### n8n → FastAPI → Outputs

```
Events  ──→  Agent Pipeline (4 layers)  ──→  Action Queue  ──→  n8n Executors  ──→  Telnyx / Airtable / Instantly
Airtable ──→  n8n Menu Sync (hourly)  ──→  weekly_menu_schedule table  ──→  /menu-dashboard
```

---

## 4. Database Schema

**PostgreSQL 16, schema: `dabbahwala`, 59+ migrations**

### Core Tables

| Table | Purpose |
|-------|---------|
| `contacts` | Master customer record — email, phone, lifecycle_segment, channel flags, order counts, current campaign, `source` (origin tag e.g. `test_harness`, `shipday`, `import`) |
| `events` | Raw event log — order_placed, email_open, sms_received, delivery_failed, etc. |
| `orders` | Order records — order_ref, total_amount, delivery_slot, order_type |
| `order_items` | Line items — menu_item_id, quantity, unit_price |
| `menu_items` | Master menu catalog — item_name, category, is_veg, avg_price |
| `menu_item_aliases` | CSV dish name → canonical menu item mapping |
| `weekly_menu` | Weekly menu snapshot keyed by `(week_start, item_name)` — `is_featured`, `display_order`, `price`, FK to `menu_items` |
| `weekly_menu_schedule` | Airtable-driven weekly menu, keyed by `(week_start, item_name)`, includes `airtable_record_id`, `active`, `price` |
| `menu_sync_log` | Audit log of each menu scrape/sync attempt — source, items found/upserted, status, error |

### Communication Tables

| Table | Purpose |
|-------|---------|
| `telnyx_messages` | SMS tracking — direction, body, status, source, agent_name |
| `telnyx_calls` | Call tracking — duration, transcript, summary |
| `delivery_status` | Delivery updates — status, notes, location, updated_by |
| `engagement_rollups` | Materialised 7d/30d rolling engagement metrics |

### Agent Pipeline Tables (migration 032)

| Table | Purpose |
|-------|---------|
| `customer_goals` | One active goal per contact — convert_to_order / retain / reactivate |
| `inference_results` | Layer 1 outputs — sentiment, intent, engagement per cycle run |
| `decision_recommendations` | Layer 2 outputs — stage, channel, offer, escalation per run |
| `orchestrator_log` | Layer 3 chosen action, full reasoning text, guardrails applied |
| `action_queue` | Approved actions (pending → executing → done / failed) awaiting n8n |

### Configuration & Analytics Tables

| Table | Purpose |
|-------|---------|
| `rules` | Lifecycle rule predicates + actions (SQL-driven) |
| `campaign_routing` | Lifecycle segment → Instantly campaign mapping |
| `campaign_queue` | Pending campaign moves |
| `agent_playbook` | User-configured rules (synced from Airtable every 15 min) |
| `sms_templates` | SMS A/B testing variants |
| `team_content` | Ground notes, ad copies, Google Docs content |
| `opportunities` | Conversion opportunities with signal type, confidence, status |
| `decision_log` | Lifecycle transition audit trail |
| `daily_reports` | Aggregated daily metrics |
| `test_runs` | E2E test suite run records — JSONB results, pass/fail counts, triggered_by (migration 056) |

### Growth Hacker Agent Tables (migration 055)

| Table | Purpose |
|-------|---------|
| `experiments` | One row per growth experiment — type (timing/offer/message_angle/channel_sequence), cohort size, results |
| `experiment_contacts` | Contacts enrolled in each growth experiment + order outcome |
| `growth_baseline` | Historical 7-day baseline conversion rates (updated by growth agent for comparison) |

### Goal & Competitor Agent Tables (migrations 050, 055, 057, 058)

| Table | Purpose |
|-------|---------|
| `goal_experiments` | One row per experiment hypothesis — `hypothesis_hash VARCHAR(64) UNIQUE` prevents re-inserting the same idea; `source` tracks whether it came from `goal_agent` or `competitor_agent` |
| `goal_experiment_contacts` | Enrolled contacts per experiment; tracks `converted`, `conversion_at` |
| `goal_agent_runs` | Audit log of every goal agent run (all four phases) |
| `discovered_signals` | Reusable SQL-based signals harvested from proven experiments |
| `competitor_agent_runs` | Audit log for weekly competitor research runs — emails parsed, sites scraped, hypotheses queued |

### Lifecycle Segments (enum)

`cold` · `engaged` · `active_customer` · `new_customer` · `lapsed_customer` · `reactivation_candidate` · `cooling` · `optout`

### Stored Functions

| Function | Purpose |
|----------|---------|
| `run_lifecycle_cycle()` | Rule engine — evaluates predicates, transitions segments, queues campaigns |
| `refresh_engagement_rollups()` | Recalculate 7d/30d engagement metrics from events |
| `ingest_event()` | Event ingestion with audit trail and type validation |
| `get_contact_detail()` | Full contact profile with all history |
| `get_communication_history()` | SMS + calls + deliveries for a contact |
| `suggest_reactivation_targets()` | Find contacts most likely to reactivate |
| `get_lifecycle_summary()` | Pipeline snapshot (contacts per segment) |
| `get_campaign_performance()` | Campaign stats (opens, clicks, orders) |
| `generate_daily_report()` | Aggregate metrics for a date |
| `create_opportunity()` | Opportunity creation with deduplication |
| `get_pending_campaign_moves()` | Returns pending campaign_queue rows joined with contacts — includes `contact_first_name`, `contact_last_name` for Instantly lead creation |

---

## 5. API Layer

**FastAPI app served at `https://dabbahwala-latest.onrender.com`**

### Router Map

| Router | Prefix | Key Endpoints |
|--------|--------|--------------|
| `test_harness.py` | `/api/test` | `POST /run` (full E2E suite), `GET /results`, `GET /results/{run_id}` |
| `agents.py` | `/api/agents` | `POST /cycle/run`, `/cycle/run-for-contact`, `/cycle/run-all`, `/cycle/run-daily-sweep`, `/cycle/run-all-lapsed`, `GET /action-queue/pending`, `POST /action-queue/{id}/done`, `POST /goals`, `POST /report/activity`, `POST /report/outcome` |
| `intelligence.py` | `/api/intelligence` | `POST /run-cycle`, `GET /pending-actions`, `POST /ingest-instantly-events` |
| `daily_orders.py` | `/api/daily-orders` | `POST /process`, `GET /summary/{date}` |
| `query.py` | `/api/query` | `POST /` (14 Tier-1 SQL + 1 Tier-2 Claude categories — includes `sms_performance`, `email_performance`, `activity_report`, `outcome_report` with date-range filtering), `GET /categories` |
| `lifecycle.py` | `/api/lifecycle` | `POST /run` — SQL rule engine |
| `opportunities.py` | `/api/opportunities` | `GET /detect`, `POST /`, `GET /pending`, `POST /{id}/dispatched`, `POST /{id}/outcome` |
| `campaigns.py` | `/api/campaigns` | `GET /pending`, `GET /active-contacts` (all contacts with active campaign — for Instantly seed), `POST /bulk-executed` (batch mark), `POST /{id}/executed` |
| `telnyx.py` | `/api/telnyx` | `POST /message`, `POST /call`, `POST /field-agent-message` |
| `delivery.py` | `/api/delivery` | `POST /status` |
| `playbook.py` | `/api/playbook` | `GET /rules`, `POST /rules`, `POST /sync-from-airtable` |
| `team_content.py` | `/api/team-content` | `POST /sync`, `POST /submit`, `GET /browse`, `POST /search` |
| `reports.py` | `/api/reports` | `GET /daily/{date}`, `POST /daily/{date}` |
| `events.py` | `/api/events` | `POST /ingest` |
| `airtable_menu.py` | `/api/menu` | `GET /items`, `POST /sync` (Airtable → Postgres) |
| `menu_sync.py` | `/api/menu-sync` | Menu suggestion agent endpoints |
| `growth_agent.py` | `/api/growth` | Growth hacker agent endpoints |
| `goal_agent.py` | `/api/goal-agent` | `POST /run`, `/hypothesize`, `/experiment`, `/measure`, `/harvest`; `GET /experiments`, `/signals`, `/runs` |
| `competitor_agent.py` | `/api/competitor-agent` | `POST /run` (full cycle: parse emails + scrape sites + generate + inject); `GET /runs`, `/experiments` |
| `chatbot.py` | `/api/chatbot` | `POST /ask`, `GET /suggest`, `GET /history`, `POST /reindex` — RAG Q&A over project docs |
| `auth.py` | _(root)_ | `GET /login`, `GET /auth/google`, `GET /auth/callback`, `GET /auth/me`, `GET /auth/logout` — Google OAuth2 for @dabbahwala.com accounts |

### Admin Endpoints

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `GET /health` | None | DB connectivity check |
| `GET /dashboard` | Google OAuth (`@dabbahwala.com`) | Marketing intelligence dashboard — redirects to `/login` if unauthenticated |
| `GET /menu-dashboard` | Google OAuth (`@dabbahwala.com`) | Menu management dashboard — redirects to `/login` if unauthenticated |
| `POST /admin/migrate/{num}` | `ADMIN_SECRET` | Run a specific migration |
| `POST /admin/query` | `ADMIN_SECRET` | Read-only SQL |
| `POST /admin/exec` | `ADMIN_SECRET` | Write SQL |

### Database Connection (`app/db.py`)

- `SimpleConnectionPool` (1–10 connections)
- `get_cursor()` — context manager, `RealDictCursor`, auto-commit/rollback
- All queries set `search_path = dabbahwala`

---

## 6. Claude AI Agent Pipeline

**Model routing:** Sonnet (`claude-sonnet-4-5-20250929`) for complex reasoning (Intent, Offer, Escalation, Orchestrator); Haiku (`claude-haiku-4-5-20251001`) for fast classification (Menu, Sentiment, Engagement, Stage, Channel).

> ⚠️ **Naming note:** The Intelligence Cycle (§7) also uses the words "Inference" and "Decision" as phase names, but those phases contain **zero Claude calls** — they are pure SQL. The layers described here are the only place in the system where Claude is actually called. See [LIFECYCLE.md §4](LIFECYCLE.md#4-important-the-naming-collision) for the full disambiguation.

**Prompt caching:** All system prompts are sent as cacheable content blocks (`cache_control: ephemeral`). The static prefix (role instructions + playbook) is identical across contacts, giving a 90% token discount from contact #2 onward in a batch.

**Playbook RAG:** Each agent layer receives only the relevant playbook categories (inference agents: exclusion+priority+inference; decision agents: exclusion+priority+decision+messaging; orchestrator: exclusion+priority only).

**Playbook hash cache:** `_fetch_playbook_rules()` stores a SHA-256 hash of the formatted playbook. DB is only re-queried when the content actually changes — not on every contact.

### Layer 1 — Inference (Menu + 3 agents)

Input: contact profile + 30-day events + full communication history + active goal + this week's menu.

| Agent | Model | Tool | Output |
|-------|-------|------|--------|
| **Menu** | Haiku | `submit_menu_picks` | `top_picks[]` (favourites on menu this week), `bridge_item` (new intro), `avoid[]` |
| **Sentiment** | Haiku | `submit_sentiment` | `sentiment` (positive/neutral/negative), `confidence`, `summary` |
| **Intent** | Sonnet | `submit_intent` | `intent` (ready_to_order/needs_info/price_sensitive/not_interested/unknown), `signals[]`, `confidence` |
| **Engagement** | Haiku | `submit_engagement` | `engagement_score` (0–1), `trend` (rising/flat/falling), `last_touch_hours_ago` |

Menu picks feed into Intent (weights toward `ready_to_order` when favourites are available) and Offer (copy references specific items).
Stored in: `inference_results`

### Layer 2 — Decision (4 agents)

Input: contact profile + full Layer 1 output bundle.

| Agent | Model | Tool | Output |
|-------|-------|------|--------|
| **Stage** | Haiku | `submit_stage` | `recommended_stage`, `confidence`, `reason` |
| **Channel** | Haiku | `submit_channel` | `recommended_channel` (sms/email/call/none), `channel_timing` (immediate/tomorrow/3days/none), `reason` |
| **Offer** | Sonnet | `submit_offer` | `offer_type` (discount/reminder/social_proof/none), `suggested_copy` (references menu picks), `reason` |
| **Escalation** | Sonnet | `submit_escalation` | `should_escalate` (bool), `urgency` (high/medium/none), `reason` |

Stored in: `decision_recommendations`

### Layer 3 — Orchestrator (1 Sonnet call)

Input: all four Layer 2 recommendations + latest delivery event + recent action history.

The Orchestrator is the final decision-maker. It reads everything and outputs **one action**. When the four decision agents disagree or a delivery event changes everything, the Orchestrator arbitrates.

**Delivery-aware guardrails (checked first, override everything):**

| Delivery event | Forced action |
|---------------|--------------|
| `delivered` | Warm thank-you SMS with reorder nudge (skip if already contacted in last 24 h) |
| `delivery_failed` / `delivery_returned` | `escalate_airtable` with urgency=high — relationship recovery before any selling |
| `out_for_delivery` / `driver_assigned` | `none` — never interrupt an order in progress |

**General guardrails:**
- Max 1 contact per 24 h on same channel
- Max 3 SMS per week per contact
- Escalation always beats automation
- `intent=not_interested` → `none` unless escalation urgency is high
- `priority_override=do_not_contact` → `none`, no exceptions

**Persistence:** No-response is never a reason to stop. The Orchestrator keeps rotating channels indefinitely. The only permanent reason to output `none` is explicit optout or do-not-contact override.

Output: one `chosen_action` (`send_sms` / `move_campaign` / `escalate_airtable` / `none`) inserted into `action_queue`.
Stored in: `orchestrator_log`

### Layer 4 — Report Agents (2 Claude calls, daily, not per-contact)

| Agent | Schedule | Output |
|-------|----------|--------|
| **Activity Report** | Daily 8:00 AM | Claude-generated HTML summary of agent runs, actions queued, SMS/calls sent, field agent performance — emailed with CSV |
| **Outcome Report** | Daily 8:30 AM | Claude-generated HTML summary of orders, opens, conversions, menu trends, field agent scorecard — emailed with CSV |

### Daily Sweep Endpoint

`POST /api/agents/cycle/run-daily-sweep` — targets contacts not run in the last 72 hours (cap 200/day). Called by the daily agent orchestration cron at 9 AM. Complements the real-time per-contact triggers from `telnyx_inbound_collector` and `daily_order_upload`.

### Playbook Injection

The `agent_playbook` table (synced from Airtable every 15 min) injects user-configured rules into the system prompt of **every** Layer 1, 2, and 3 agent. Users can change AI behaviour without any code changes.

| Category | Example effect |
|----------|---------------|
| `exclusion` | Overrides everything — "Never contact contacts tagged 'do_not_disturb'" |
| `priority` | Biases reasoning — "Prioritise contacts with 3+ orders over cold leads" |
| `inference` | Shapes classification — "If SMS mentions 'price', always classify as price_sensitive" |
| `decision` | Directs actions — "Always use SMS for reactivation, never email" |
| `messaging` | Controls copy style — "Include delivery slot info in all thank-you messages" |
| `general` | Open-ended instructions |

---

## 7. Intelligence Cycle

**5-phase daily cycle (`/api/intelligence/run-cycle`) — runs at 7:00 AM — zero Claude calls, pure SQL throughout.**

> ⚠️ **Naming note:** Two of the five phases are called "INFERENCE" and "DECISION." These names are misleading because they share labels with layers in the Claude Agent Pipeline (§6). They are completely different — the Intelligence Cycle phases are pure SQL functions that scan all contacts at once; the Agent Pipeline layers are per-contact Claude AI calls. See [LIFECYCLE.md §4](LIFECYCLE.md#4-important-the-naming-collision) for the full disambiguation.

### What it does

The Intelligence Cycle scans every contact in the database each hour to find behavioural patterns — contacts who are ready to act but haven't been reached yet. When it finds a match, it writes an **opportunity** record to the database. n8n then dispatches that opportunity to the right channel.

### The 5 Phases

| Phase | What It Does | Claude? |
|-------|-------------|---------|
| **INTAKE** | Count all events in the last 2 hours across the system (email opens/clicks from Instantly, SMS/calls from Telnyx, orders). Snapshot only — no action taken. | No |
| **EVIDENCE** | Call `refresh_engagement_rollups()` to recalculate every contact's rolling 7-day and 30-day engagement metrics (`opens_7d`, `clicks_7d`, etc.). These metrics are what all signal detection queries read. | No |
| **INFERENCE** (SQL signal detection) | Run 7 SQL functions that identify contacts matching specific behavioural patterns. The name "Inference" here means pattern matching in SQL, not Claude reasoning. | No |
| **DECISION** (opportunity creation) | For each contact found by INFERENCE, call `create_opportunity()` — a SQL stored function — to write a row to the `opportunities` table with the action, priority, and suggested message. | No |
| **EXECUTION** | Call `run_lifecycle_cycle()` to run the lifecycle rule engine and ensure stage transitions are up to date. Count pending opportunities and campaign moves for the cycle summary. | No |

### The 7 SQL Signal Types

| Signal | SQL Detection Logic | Action created |
|--------|--------------------|-|
| `engaged_no_order` | `opens_7d >= 3` AND no order in 7 days | Email (warm, confidence 0.75) |
| `new_customer_no_repeat` | Exactly 1 total order, first order 5+ days ago | SMS (warm, confidence 0.80) |
| `lapsed_reengaged` | In `lapsed_customer` segment AND recent SMS reply or email click | Field sales call (hot, confidence 0.90) |
| `reorder_intent` | Call transcript contains reorder keywords | SMS (hot, confidence 0.92) |
| `app_customers_for_conversion` | `primary_source` is a food delivery app AND no direct order in 30 days | SMS + campaign move (warm, confidence 0.82) |
| `subscription_candidates` | 3+ total orders AND no subscription type | SMS subscription pitch (warm, confidence 0.78) |
| `high_value_at_risk` | 5+ total orders AND no order in 14+ days AND not already lapsed/optout | Field sales call (hot, confidence 0.88) |

---

## 8. n8n Workflow Layer

**30 workflows on `digitalworker.dataskate.io` — all active except `[Shipday — Evidence] Historical Import` (manual one-shot)**

Workflow IDs tracked in `n8n/config.json`. All files version-controlled in `n8n/`.

### Workflow Inventory

| Group | Workflow | Schedule | Purpose |
|-------|----------|----------|---------|
| **Airtable** | Menu Sync | Hourly | Pull Airtable "Weekly Menu" → `POST /api/menu/sync` → `weekly_menu_schedule` |
| **Airtable** | Playbook Sync | Every 15 min | Sync rules from Airtable → `agent_playbook` table |
| **Airtable** | Outcome Sync | Every 15 min | Pull Airtable field sales outcomes → update opportunities |
| **Airtable** | Marketing Query Form | On-demand (form submit) | n8n form → `POST /api/query` → Claude inference → logs to Airtable |
| **Shipday** | Delivery Collector | Every 30 min | Poll Shipday → `POST /api/delivery/status` |
| **Shipday** | Feedback Sync | Hourly | Poll delivery feedback, instructions, proof-of-delivery |
| **Shipday** | Historical Import | Manual only | One-shot backfill of up to 1 year of order history |
| **Telnyx** | Inbound Collector | Every 30 min | Ingest inbound SMS/calls → `POST /api/telnyx/message` → trigger agent cycle |
| **Telnyx** | SMS Dispatch | Every 10 min | Poll action_queue for `send_sms` → Telnyx API → mark done |
| **Telnyx** | Broadcast Dispatch | Every 5 min | Dispatch queued broadcasts (SMS via Telnyx, email via SMTP) |
| **Telnyx** | Broadcast Form | On form submit | n8n form UI for delay alerts and promo broadcasts |
| **Instantly** | Campaign Performance | Hourly | Fetch Instantly analytics → DB |
| **Instantly** | Campaign Sync | Every 6 h | Sync campaigns tagged `dabbahwala` → `POST /api/webhooks/sync-campaigns` |
| **Instantly** | Campaign Setup | Daily midnight | Create missing Instantly campaigns (no-op if all exist) |
| **Instantly** | Bulk Lead Seeder | Manual only | One-shot: seed all active contacts into their Instantly campaign; skips missing `first_name`; `skip_if_in_workspace=true` (idempotent) |
| **Google** | Docs & Drive Sync | Every 30 min | List Drive folder → read Google Docs → `POST /api/team-content/sync` |
| **Orders** | Daily CSV Upload | Daily 1 PM EST | Upload daily CSV → `POST /api/daily-orders/process` |
| **Reporting** | Daily Field Brief | Daily 7:30 AM | `POST /api/field-agent/daily-brief` |
| **Reporting** | Daily Activity Report | Daily 8:00 AM | `POST /api/agents/report/activity` → Claude HTML + CSV → email |
| **Reporting** | Daily Outcome Report | Daily 8:30 AM | `POST /api/agents/report/outcome` → Claude HTML + CSV → email |
| **Claude** | Agent Orchestration | Daily 9:00 AM | `POST /api/agents/cycle/run-daily-sweep` — dormant contacts (cap 200, 72 h cooldown) |
| **Claude** | Daily Intelligence Cycle | Daily 7:00 AM | `POST /api/intelligence/run-cycle` — full 5-phase cycle (24 h poll window) |
| **Claude** | Lifecycle Cycle Runner | Daily 6:00 AM | `POST /api/lifecycle/run` — SQL rule engine; pending contacts pushed to Instantly via bulk `POST /api/campaigns/bulk-executed` (single DB call) |
| **Claude** | Lapsed Customer Daily | Daily (random offset) | Persistent re-engagement for lapsed customers |
| **Claude** | Menu Sync Weekly | Weekly | Menu suggestion agent cycle |
| **Claude** | Growth Agent Cycle | Every Monday 7:30 AM | Growth hacker 4-phase experiment loop: refresh baseline → measure → design+launch → email report |
| **Claude** | Goal-Oriented Agent Cycle | Daily 9:00 AM | `POST /api/goal-agent/run` — 4-phase proactive loop: HYPOTHESIZE → EXPERIMENT → MEASURE → HARVEST; proven experiments become `discovered_signals` |
| **Claude** | Competitor Research Agent | Every Monday 6:30 AM | `POST /api/competitor-agent/run` — parse .eml samples + scrape 5 competitor sites + Claude generates 8 hypotheses covering all 4 retention segments → auto-inject into `goal_experiments` |
| **System** | Action Queue Executor | Every 30 min | Route action_queue rows to Telnyx / Instantly / Airtable / Drive / SMTP |
| **System** | Chatbot Docs Reindex | Every Monday 2 AM | Refresh chatbot document index |
| **System** | Daily E2E Test Suite | Daily 5:00 AM ET | Run 55+ end-to-end tests across 14 groups → email results to vivek@dabbahwala.com |

### n8n API Notes

- Activate workflow: `POST /api/v1/workflows/{id}/activate`
- Deactivate workflow: `POST /api/v1/workflows/{id}/deactivate`
- When pushing via API only send `{name, nodes, connections, settings}` — n8n rejects `staticData`, `pinData`, `tags`, `meta`
- Credentials resolve by **name** on first push — exact credential name required
- Telnyx from number hardcoded as `+18444322224` (n8n Variables not available on this instance)

---

## 9. External Service Integrations

### Telnyx (SMS + Voice)

- Outbound SMS from `+18444322224`
- Inbound SMS polled every 30 min by Telnyx Inbound Collector
- Call transcripts stored with duration, transcript, AI summary
- Field agent logging: `POST /api/telnyx/field-agent-message` for SMS from personal phones

### Instantly (Email Campaigns)

5 lifecycle-mapped campaigns:

| Campaign | Target Segment |
|----------|---------------|
| DW-NurtureSlow-ColdContacts | cold |
| DW-PromoStandard-ActiveEngaged | engaged, active_customer |
| DW-PromoAggressive-LapsedCustomers | lapsed_customer |
| DW-NewCustomerOnboarding | new_customer |
| DW-Reactivation-LongDormant | reactivation_candidate |

Campaign routing defined in `campaign_routing` table. Updated by migrations 014, 023, 026, 031, 042–045.

### Airtable

- **Base ID:** `appuy2VTIao6XVpIW`
- **Weekly Menu** table: staff edit menu items here → hourly n8n sync → `weekly_menu_schedule`
  - Fields: Name, Category, Is Veg, Description, Image URL, Week Start, Active, Price
- **Field Sales Tasks:** escalated opportunities appear as Airtable records; agents update outcomes
- **Playbook Rules:** user-configurable; synced every 15 min to `agent_playbook`

### Shipday

Polled every 30 min. Status mapping:

| Shipday Status | DabbahWala Event |
|---------------|-----------------|
| COMPLETED | `delivered` |
| FAILED | `delivery_failed` |
| RETURNED | `delivery_returned` |
| PICKED_UP | `out_for_delivery` |
| ASSIGNED | `driver_assigned` |
| ACCEPTED | `order_accepted` |

### Google Docs / Drive

- Drive folder: `1O0ES9uiDL6AWf9QMMYiyRUWGtymDjPF5`
- Polled every 30 min; docs classified by title keyword:
  - "ad copy" / "social media" / "facebook" / "instagram" → `ad_copy`
  - Everything else → `ground_note`
- Stored in `team_content` with `google_doc_id` dedup

### Gmail SMTP

- n8n credential: `Gmail-SMTP` (ID: `Sk6XzPNPnJTXHEbr`)
- Port 465, SSL — used by Action Queue Executor and Broadcast Dispatch
- Reports sent to `REPORT_EMAIL_TO` env var (default `core@dabbahwala.com`)

---

## 10. MCP Server (Claude Desktop)

`mcp_server/server.py` — FastMCP server connecting directly to PostgreSQL.

### Tool Groups

| Module | Tools |
|--------|-------|
| `contacts.py` | `get_contact_detail(email_or_id)`, `search_contacts(segment, flags, order_range, limit)` |
| `analytics.py` | `get_lifecycle_summary()`, `get_campaign_performance(campaign, days)`, `get_engagement_trends(days)` |
| `communications.py` | `get_communication_history(contact_id, days)` |
| `recommendations.py` | `suggest_reactivation_targets(limit)`, `recommend_content_strategy(contact_id)` |
| `opportunities.py` | `detect_opportunities()`, `create_opportunity()`, `get_high_intent_signals()` |
| `agents.py` | `get_latest_inference(contact_id)`, `get_latest_decision(contact_id)`, `get_orchestrator_history(contact_id)`, `get_pending_actions(limit)`, `get_agent_cycle_summary(days)` |
| `shipday.py` | `get_shipday_order(order_number)`, `list_shipday_orders(from_date, to_date)`, `get_shipday_carriers()`, `get_shipday_order_tracking(order_number)` |
| `instantly.py` | `instantly_list_campaigns()`, `instantly_get_campaign_analytics(campaign_id)`, `instantly_list_leads(campaign_id)`, `instantly_get_email_events(campaign_id)` |

### Claude Desktop Config

```json
{
  "mcpServers": {
    "dabbahwala": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/path/to/DabbahWala",
      "env": {
        "DATABASE_URL": "<postgres-url>",
        "SHIPDAY_API_KEY": "<key>",
        "INSTANTLY_API_KEY": "<key>"
      }
    }
  }
}
```

---

## 11. Deployment & CI/CD

### Render Auto-Deploy

On every merge to `main`:
1. `scripts/render_build.sh` — installs Python deps, runs all pending `migrations/*.sql`
2. `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

**Migration rules:**
- Files in `migrations/` numbered sequentially (next: **060**)
- Always use `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`
- Never modify existing migrations — always create a new one
- Schema: `dabbahwala`


### Environments

| Environment | URL |
|-------------|-----|
| Production | `https://dabbahwala.com` |
| Staging | `https://staging.dabbahwala.com` |
| Pre-prod | `https://preprod.dabbahwala.com` |
| API | `https://dabbahwala-latest.onrender.com` |

---

## 12. Operational Metrics

| Metric | Count |
|--------|-------|
| API endpoints | ~88+ |
| Database migrations | 59 |
| Database tables | 22+ |
| Stored functions | 15+ |
| n8n workflows | 30 |
| MCP tools | 35+ |
| Claude calls per contact cycle | 8 (3 + 4 + 1) |
| Signal types detected | 7 |
| Lifecycle segments | 8 |
| Email campaigns | 5 |
| E2E test cases | 55+ |
