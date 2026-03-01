# DabbahWala — System Reference

Complete technical reference for the DabbahWala automated marketing platform.

> **Navigation:** [README](README.md) · [Guide](GUIDE.md) · [Tests](TESTS.md) · [Claude Instructions](CLAUDE.md)

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

- **Rule-based automation** — Stage Engine (SQL rules) + Contact Sweep (hourly loop) + n8n workflows
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

### The Three Engines

DabbahWala uses three cooperating engines — all sharing the same PostgreSQL database, running independently on different schedules.

| Engine | What it does | Runs | Claude? |
|--------|-------------|------|---------|
| **Stage Engine** | Pure SQL rules — classifies every contact into a lifecycle segment and routes them to the correct Instantly email campaign | Hourly | No |
| **Contact Sweep** | Signal scanner — 5-phase SQL loop (COLLECT→PROFILE→SIGNAL→ROUTE→DISPATCH) that finds contacts ready to act and creates `opportunities` | Hourly | No |
| **AI Stack** | Per-contact Claude pipeline — 8 calls per contact (Observer→Advisor→Orchestrator) producing one concrete outreach action | Every 3 h + real-time on inbound SMS | Yes (8 calls) |

They are not redundant — Stage Engine keeps everyone in the right campaign, Contact Sweep spots who needs urgent action, AI Stack figures out exactly what to say to that specific person.

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
    ├─ Contact Sweep (hourly) ──→  /intelligence/run-cycle
    ├─ Stage Runner (hourly)  ──→  /lifecycle/run
    └─ Data collectors        ──→  Shipday / Telnyx / Google Docs / Airtable
```

### n8n → FastAPI → Outputs

```
Events  ──→  Agent Pipeline (4 layers)  ──→  Action Queue  ──→  n8n Executors  ──→  Telnyx / Airtable / Instantly
Airtable ──→  n8n Menu Catalog Sync (daily)  ──→  menu_catalog table (+ menu_catalog_history)
```

---

## 4. Database Schema

**PostgreSQL 16, schema: `dabbahwala`, 62+ migrations**

### Core Tables

| Table | Purpose |
|-------|---------|
| `contacts` | Master customer record — email, phone, lifecycle_segment, channel flags, order counts, `source` (origin tag e.g. `test_harness`, `shipday`, `import`); active campaign derived via JOIN to `campaign_routing` |
| `events` | Raw event log — order_placed, email_open, sms_received, delivery_failed, etc. |
| `orders` | Order records — order_ref, total_amount, delivery_slot, order_type |
| `order_items` | Line items — item_name, quantity, unit_price (menu_item_id retained as bare BIGINT for legacy rows) |
| `menu_catalog` | Per-item menu catalog (Airtable is source of truth) — item_name, category, is_veg, price, active, added_date, discarded_date, airtable_record_id |
| `menu_catalog_history` | Audit trail of every price/activation/discard change per item — change_type ('added','price_change','discarded','field_update'), old_value, new_value |

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
| `contact_observations` | Layer 1 outputs (Observer agents) — sentiment, intent, engagement per cycle run |
| `action_plans` | Layer 2 outputs (Advisor agents) — stage, channel, offer, escalation per run |
| `orchestrator_log` | Layer 3 chosen action, full reasoning text, guardrails applied |
| `action_queue` | Approved actions (pending → executing → done / failed) awaiting n8n |

### Configuration & Analytics Tables

| Table | Purpose |
|-------|---------|
| `rules` | Lifecycle rule predicates + actions (SQL-driven) |
| `campaign_routing` | **Single source of truth** for campaigns — lifecycle segment → Instantly campaign ID/name, email template file, performance stats (leads, opens, replies, etc.); `contacts.current_campaign` is always derived from this table via `lifecycle_segment` JOIN |
| `campaign_queue` | Pending campaign moves |
| `campaign_push_log` | Audit log of every push_instantly_lead attempt — queue_id, email, campaign, success/failure, status_code, response_body |
| `agent_playbook` | User-configured rules (synced from Airtable daily at 6 AM) |
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
| `config.py` | `/api/credentials` + `/api/internal` | `GET /` (return all API keys — requires `X-Admin-Secret`); `POST /send-email` (SMTP proxy); `POST /drive/upload`; `GET /drive/files`; `GET /docs/{doc_id}` |
| `test_harness.py` | `/api/test` | `POST /run` (full E2E suite), `GET /results`, `GET /results/{run_id}`, `GET /run/{group_id}` (run single test group, returns per-test pass/fail) |
| `agents.py` | `/api/agents` | `POST /cycle/run`, `/cycle/run-for-contact`, `/cycle/run-all`, `/cycle/run-daily-sweep`, `/cycle/run-all-lapsed`, `GET /action-queue/pending`, `POST /action-queue/{id}/done`, `POST /goals`, `POST /report/activity`, `POST /report/outcome` |
| `intelligence.py` | `/api/intelligence` | `POST /run-cycle`, `GET /pending-actions`, `POST /ingest-instantly-events` |
| `orders.py` | `/api/shipday` | `POST /ingest-orders`, `GET /sync-status`, `GET /top-calls`, `POST /run-migration`, `POST /import-all-and-run-agents`, `GET /import-pipeline-status`, `POST /sync-feedback`, `GET /feedback-stats` (merged from `shipday_sync.py` + `shipday_historical.py`) |
| `query.py` | `/api/query` | `POST /` (14 Tier-1 SQL + 1 Tier-2 Claude categories — includes `sms_performance`, `email_performance`, `activity_report`, `outcome_report` with date-range filtering), `GET /categories` |
| `lifecycle.py` | `/api/lifecycle` | `POST /run` — SQL rule engine |
| `prospects.py` | `/api/prospects` | `GET /template` (new-contact CSV template), `POST /upload-csv` (bulk add new contacts), `GET /update-template` (update CSV template + enqueues Drive upload), `POST /update-csv` (bulk update existing contacts — sets name, address, priority_override, sales_notes by email/phone match), `POST /add` (single manual entry) |
| `contacts.py` | `/api/contacts` | `PATCH /{id}/priority`, `PATCH /{id}/notes` |
| `opportunities.py` | `/api/opportunities` | `GET /detect`, `POST /`, `GET /pending`, `POST /{id}/dispatched`, `POST /{id}/outcome` |
| `campaigns.py` | `/api/campaigns` | `POST /push-lead` (enqueue push_instantly_lead into action_queue — the Postgres injection entry point), `GET /pending` (pending action_queue push_instantly_lead items with contact first/last name), `GET /active-contacts` (contacts with active campaign derived from lifecycle_segment via campaign_routing JOIN — for Instantly seed), `GET /active-contacts-stats` (diagnostic — filter exclusion counts + campaign distribution), `POST /log-push` (record Instantly push result in campaign_push_log), `GET /push-log` (read campaign_push_log — filter by success), `POST /{id}/executed` (mark single campaign_queue row executed), `POST /bulk-executed` (mark batch of action_queue push_instantly_lead rows as done — called by lifecycle_cycle_cron n8n workflow), `GET /analytics`, `GET /templates`, `GET /templates/{name}`, `PUT /templates/{name}`, `POST /templates/{name}/rewrite`, `POST /setup-instantly` |
| `sms.py` | `/api/telnyx` | `POST /message` (auto-creates contact + stores message for unknown inbound numbers so Observer has full context), `POST /call`, `POST /field-agent-message` (renamed from `telnyx.py`) |
| `webhooks.py` | `/api/webhooks` | `POST /instantly` (Instantly email events), `POST /telnyx` (Telnyx inbound SMS push webhook), `POST /shipday` / `GET /shipday` (Shipday delivery status), `POST /sync-campaigns`, `GET /campaigns`, `POST /campaign-stats` |
| `delivery.py` | `/api/delivery` | `POST /status` |
| `playbook.py` | `/api/playbook` | `GET /rules`, `POST /rules`, `POST /sync-from-airtable` |
| `team_content.py` | `/api/team-content` | `POST /sync`, `POST /submit`, `GET /browse`, `POST /search` |
| `reports.py` | `/api/reports` | `GET /daily/{date}`, `POST /daily/{date}` |
| `events.py` | `/api/events` | `POST /ingest` |
| `menu.py` | `/api/menu` | `GET /items`, `GET /items/inactive`, `GET /items/{id}/history`, `POST /sync` (Airtable → Postgres, two-phase: upsert + deletion detection; renamed from `airtable_menu.py`) |
| `growth_agent.py` | `/api/growth` | `POST /run-cycle` (Claude designs+dispatches experiment; agent chooses `measure_days` 7–56), `POST /measure` (adaptive: fires at `measure_at` OR after 30+ conversion events), `POST /baseline/update`; `GET /experiments`, `/insights` |
| `goal_agent.py` | `/api/goal-agent` | `POST /run`, `/hypothesize`, `/experiment`, `/measure`, `/harvest`; `GET /experiments`, `/signals`, `/runs` |
| `competitor_agent.py` | `/api/competitor-agent` | `POST /run` (full cycle: parse emails + scrape sites + generate + inject); `GET /runs`, `/experiments` |
| `chatbot.py` | `/api/chatbot` | `POST /ask`, `GET /suggest`, `GET /history`, `POST /reindex` — RAG Q&A over project docs |
| `auth.py` | _(root)_ | `GET /login`, `GET /auth/google`, `GET /auth/callback`, `GET /auth/me`, `GET /auth/logout` — Google OAuth2 for @dabbahwala.com accounts |
| `schedules.py` | `/api/admin` | `GET /schedules` (list all n8n workflow schedules as human-readable strings, sorted by name); `POST /schedules/{workflow_id}` (update a workflow's scheduleTrigger node and push to n8n) — both require active Google OAuth session |

### Admin Endpoints

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `GET /health` | None | DB connectivity check |
| `GET /dashboard` | Google OAuth (`@dabbahwala.com`) | Marketing intelligence dashboard — redirects to `/login` if unauthenticated |
| `POST /admin/migrate/{num}` | `ADMIN_SECRET` | Run a specific migration |
| `POST /admin/query` | `ADMIN_SECRET` | Read-only SQL |
| `POST /admin/exec` | `ADMIN_SECRET` | Write SQL |

### Database Connection (`app/db.py`)

- `SimpleConnectionPool` (1–10 connections)
- `get_cursor()` — context manager, `RealDictCursor`, auto-commit/rollback
- All queries set `search_path = dabbahwala`

---

## 6. AI Stack (Claude Agent Pipeline)

**Model routing:** Sonnet (`claude-sonnet-4-5-20250929`) for complex reasoning (Intent, Offer, Escalation, Orchestrator); Haiku (`claude-haiku-4-5-20251001`) for fast classification (Menu, Sentiment, Engagement, Stage, Channel).


**Prompt caching:** All system prompts are sent as cacheable content blocks (`cache_control: ephemeral`). The static prefix (role instructions + playbook) is identical across contacts, giving a 90% token discount from contact #2 onward in a batch.

**Playbook RAG:** Each agent layer receives only the relevant playbook categories (Observer agents: exclusion+priority+observer; Advisor agents: exclusion+priority+advisor+messaging; Orchestrator: exclusion+priority only).

**Playbook hash cache:** `_fetch_playbook_rules()` stores a SHA-256 hash of the formatted playbook. DB is only re-queried when the content actually changes — not on every contact.

### Layer 1 — Observer (Menu + 3 agents)

Input: contact profile + 30-day events + full communication history + active goal + this week's menu.

| Agent | Model | Tool | Output |
|-------|-------|------|--------|
| **Menu** | Haiku | `submit_menu_picks` | `top_picks[]` (favourites on menu this week), `bridge_item` (new intro), `avoid[]` |
| **Sentiment** | Haiku | `submit_sentiment` | `sentiment` (positive/neutral/negative), `confidence`, `summary` |
| **Intent** | Sonnet | `submit_intent` | `intent` (ready_to_order/needs_info/price_sensitive/not_interested/unknown), `signals[]`, `confidence` |
| **Engagement** | Haiku | `submit_engagement` | `engagement_score` (0–1), `trend` (rising/flat/falling), `last_touch_hours_ago` |

Menu picks feed into Intent (weights toward `ready_to_order` when favourites are available) and Offer (copy references specific items).
Stored in: `contact_observations`

### Layer 2 — Advisor (4 agents)

Input: contact profile + full Layer 1 output bundle.

| Agent | Model | Tool | Output |
|-------|-------|------|--------|
| **Stage** | Haiku | `submit_stage` | `recommended_stage`, `confidence`, `reason` |
| **Channel** | Haiku | `submit_channel` | `recommended_channel` (sms/email/call/none), `channel_timing` (immediate/tomorrow/3days/none), `reason` |
| **Offer** | Sonnet | `submit_offer` | `offer_type` (discount/reminder/social_proof/none), `suggested_copy` (references menu picks), `reason` |
| **Escalation** | Sonnet | `submit_escalation` | `should_escalate` (bool), `urgency` (high/medium/none), `reason` |

Stored in: `action_plans`

### Layer 3 — Orchestrator (1 Sonnet call)

Input: all four Layer 2 recommendations + latest delivery event + recent action history.

The Orchestrator is the final decision-maker. It reads everything and outputs **one action**. When the four Advisor agents disagree or a delivery event changes everything, the Orchestrator arbitrates.

**Delivery-aware guardrails (checked first, override everything):**

| Delivery event | Forced action |
|---------------|--------------|
| `delivered` | AI Stack fires after a **4-hour delay** (threading.Timer in webhooks.py) — gives the customer time to eat and leave feedback before any outreach decision is made |
| `delivery_failed` / `delivery_returned` | `escalate_airtable` with urgency=high — fires immediately, relationship recovery before any selling |
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

**Batch runner post-processing** (after all contacts in `run-all-contacts` are cycled):
- `move_campaign` contacts → also immediately pushed to Instantly via API
- `escalate_airtable` contacts → also immediately creates Airtable field-sales task
- Any `move_campaign` actions taken → one `send_email_report` action queued to `support@dabbahwala.com` at end of batch (digest, not per-contact)

### Layer 4 — Report Agents (2 Claude calls, daily, not per-contact)

| Agent | Schedule | Output |
|-------|----------|--------|
| **Activity Report** | Daily 8:00 AM | Claude-generated HTML summary of agent runs, actions queued, SMS/calls sent, field agent performance — emailed with CSV |
| **Outcome Report** | Daily 8:30 AM | Claude-generated HTML summary of orders, opens, conversions, menu trends, field agent scorecard — emailed with CSV |

### Daily Sweep Endpoint

`POST /api/agents/cycle/run-daily-sweep` — targets contacts not run in the last 72 hours (cap 200/day). Called by the daily agent orchestration cron at 9 AM. Complements the real-time per-contact triggers from `telnyx_inbound_collector` and `daily_order_upload`.

### Playbook Injection

The `agent_playbook` table (synced from Airtable daily at 6 AM) injects user-configured rules into the system prompt of **every** Layer 1, 2, and 3 agent. Users can change AI behaviour without any code changes.

| Category | Example effect |
|----------|---------------|
| `exclusion` | Overrides everything — "Never contact contacts tagged 'do_not_disturb'" |
| `priority` | Biases reasoning — "Prioritise contacts with 3+ orders over cold leads" |
| `observer` | Shapes classification — "If SMS mentions 'price', always classify as price_sensitive" |
| `advisor` | Directs actions — "Always use SMS for reactivation, never email" |
| `messaging` | Controls copy style — "Include delivery slot info in all thank-you messages" |
| `general` | Open-ended instructions |

---

## 7. Contact Sweep

**5-phase hourly loop (`/api/intelligence/run-cycle`) — zero Claude calls, pure SQL throughout.**

> **Terminology:** The Contact Sweep is entirely rule-based. The AI Stack (§6) is separate — it runs when the ROUTE phase decides a contact needs Claude analysis. See [LIFECYCLE.md](LIFECYCLE.md) for the full explanation.


### What it does

The Contact Sweep scans every contact in the database each hour to find behavioural patterns — contacts who are ready to act but haven't been reached yet. When it finds a match, it writes an **opportunity** record to the database. n8n then dispatches that opportunity to the right channel.

### The 5 Phases (COLLECT → PROFILE → SIGNAL → ROUTE → DISPATCH)

| Phase | What It Does | Claude? |
|-------|-------------|---------|
| **COLLECT** | Count all events in the last 2 hours across the system (email opens/clicks from Instantly, SMS/calls from Telnyx, orders). Snapshot only — no action taken. | No |
| **PROFILE** | Call `refresh_engagement_rollups()` to recalculate every contact's rolling 7-day and 30-day engagement metrics (`opens_7d`, `clicks_7d`, etc.). These metrics are what all signal detection queries read. | No |
| **SIGNAL** | Run 7 SQL functions that identify contacts matching specific behavioural patterns (SQL pattern matching only — not Claude). | No |
| **ROUTE** | For each contact found by SIGNAL, call `create_opportunity()` — a SQL stored function — to write a row to the `opportunities` table with the action, priority, and suggested message. | No |
| **DISPATCH** | Call `run_lifecycle_cycle()` (Stage Engine) to run SQL lifecycle rules and ensure stage transitions are up to date. Count pending opportunities and campaign moves for the cycle summary. | No |

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

**26 workflows on `digitalworker.dataskate.io` — all active-scheduled**

Workflow IDs tracked in `n8n/config.json`. All files version-controlled in `n8n/`.

### Workflow Inventory

| Group | Workflow | Schedule | Purpose |
|-------|----------|----------|---------|
| **[Menu]** | Catalog Sync | Weekly Mon 6:30 AM | Pull Airtable "Menu Catalog" → `POST /api/menu/sync` → upsert `menu_catalog`, detect deletions → mark discarded + record history |
| **[Agent Rules]** | Playbook Sync | Daily 6 AM | Sync rules from Airtable → `agent_playbook` table |
| **[Field Agent]** | Outcome Sync | Every 4 hours | Pull Airtable field sales outcomes → update opportunities |
| **[Order Intake]** | Order Collector | Every 30 min | Poll Shipday → `POST /api/shipday/ingest-orders` |
| **[Order Intake]** | Feedback Sync | Hourly | Poll delivery feedback, instructions, proof-of-delivery |
| **[Order Intake]** | Daily CSV Upload | Daily 1 PM EST | Upload daily CSV → `POST /api/daily-orders/process` |
| **[SMS]** | Inbound Collector | Every 30 min | Poll Telnyx MDR for inbound SMS + call recordings → `POST /api/telnyx/message` → trigger agent cycle |
| **[SMS]** | Dispatch Queue | Every 10 min | Poll action_queue for `send_sms` → Telnyx API → mark done |
| **[Broadcast]** | Dispatch | Every 1 hour | Dispatch queued broadcasts (SMS via Telnyx API, email via `/api/internal/send-email`); fixed email branch + messaging_profile_id |
| **[Email Campaigns]** | Performance Tracker | Hourly | Fetch Instantly analytics → `POST /api/webhooks/campaign-stats` |
| **[Email Campaigns]** | Campaign Setup | Daily midnight | Create missing Instantly campaigns (no-op if all exist) |
| **[Chatbot]** | Docs Sync | Every 30 min | List Drive folder → read Google Docs via `/api/internal/docs/{id}` → `POST /api/team-content/sync` |
| **[Chatbot]** | Docs Reindex | Weekly Mon 2 AM | Refresh chatbot document index |
| **[Field Agent]** | Daily Brief | Daily 7:30 AM | `POST /api/field-agent/daily-brief` → write top-10 contacts to Airtable Field Sales Tasks → email summary to core@dabbahwala.com |
| **[Reports]** | Daily Activity Report | Daily 8:00 AM | `POST /api/agents/report/activity` → Claude HTML + CSV → enqueued to `action_queue` as `send_email_report` → SMTP |
| **[Reports]** | Daily Outcome Report | Daily 8:30 AM | `POST /api/agents/report/outcome` → Claude HTML + CSV → enqueued to `action_queue` as `send_email_report` → SMTP |
| **[Intelligence]** | AI Stack | Every 3 hours | `POST /api/agents/cycle/run-daily-sweep` — dormant contacts (cap 200, 72 h cooldown); 4-layer Claude pipeline (Observer→Advisor→Orchestrator→Reports) |
| **[Intelligence]** | Contact Sweep | Hourly | `POST /api/intelligence/run-cycle` — full 5-phase sweep (COLLECT→PROFILE→SIGNAL→ROUTE→DISPATCH) |
| **[Intelligence]** | Stage Runner | Hourly | `POST /api/lifecycle/run` — Stage Engine: pure SQL rules that move contacts between lifecycle stages |
| **[Intelligence]** | Lapsed Re-engagement | Daily (random offset) | Persistent re-engagement for lapsed contacts |
| **[Growth]** | Weekly Growth Agent | Every Mon 7:30 AM | Fetch credentials → refresh baseline → measure due experiments (adaptive early cutoff at 30 events) → Claude designs+launches new experiment (agent picks `measure_days`) → build HTML report → `POST /api/internal/send-email` |
| **[Growth]** | Goal Agent | Daily 9:00 AM | `POST /api/goal-agent/run` — 4-phase proactive loop: HYPOTHESIZE → EXPERIMENT → MEASURE → HARVEST |
| **[Growth]** | Competitor Research | Every Mon 6:30 AM | `POST /api/competitor-agent/run` — parse .eml + scrape competitor sites + generate hypotheses → inject into `goal_experiments` |
| **[System]** | Action Queue | Every 30 min | Route action_queue rows to Telnyx / Instantly / Airtable / Drive / `/api/internal/send-email` |
| **[System]** | Feature Tests | Daily 5 AM | Sequential chain G1→G14, each node green/red independently; Summarize + email report to core@dabbahwala.com |

### n8n API Notes

- Activate workflow: `POST /api/v1/workflows/{id}/activate`
- Deactivate workflow: `POST /api/v1/workflows/{id}/deactivate`
- When pushing via API only send `{name, nodes, connections, settings}` — n8n rejects `staticData`, `pinData`, `tags`, `meta`
- Credentials resolve by **name** on first push — exact credential name required
- All credentials fetched at runtime via `GET /api/credentials` (single "DW Admin Secret" bootstrap credential in n8n)
- No hardcoded phone numbers, profile IDs, or API keys in workflow JSONs — all sourced from `/api/credentials` response

---

## 9. External Service Integrations

### Telnyx (SMS + Voice)

- Outbound SMS from `+18444322224`
- Inbound SMS collected via two mechanisms:
  - **Real-time**: `POST /api/webhooks/telnyx` — configure in Telnyx → Messaging Profiles → Webhooks → Inbound URL: `https://dabbahwala-latest.onrender.com/api/webhooks/telnyx`
  - **Polling fallback**: Telnyx Inbound Collector n8n workflow polls MDR every 30 min (`GET /v2/reports/messaging/message_detail_records`)
- **Unknown inbound contacts**: `POST /api/telnyx/message` auto-creates the contact (phone, lifecycle=cold, source=Inbound) and stores the message body before firing the agent cycle — so Observer reads the actual SMS text and Advisor/Orchestrator can respond in context immediately
- Historic SMS backfill: `[Telnyx — Evidence] SMS Historical Import` workflow (manual, one-shot; MDR retains ~90 days)
- Call recordings polled every 30 min via `GET /v2/recordings`
- Call transcripts stored with duration, transcript, AI summary
- Field agent logging: `POST /api/telnyx/field-agent-message` for SMS from personal phones

### Instantly (Email Campaigns)

6 lifecycle-mapped campaigns — all stored exclusively in `campaign_routing` (single source of truth):

| Campaign | Target Segment |
|----------|---------------|
| DW-NurtureSlow-ColdContacts | cold |
| DW-PromoStandard-ActiveEngaged | engaged |
| DW-ActiveCustomer | active_customer |
| DW-PromoAggressive-LapsedCustomers | lapsed_customer |
| DW-NewCustomerOnboarding | new_customer |
| DW-Reactivation-LongDormant | reactivation_candidate |

`campaign_routing` holds: `lifecycle_segment` (PK), `default_campaign`, `instantly_campaign_id`, `instantly_campaign_name`, `template_file`, and performance stats (leads, opens, replies, etc.). The `instantly_campaigns` table was dropped in migration 062 — all campaign data now lives in `campaign_routing`.

A contact's current campaign is always derived via `JOIN campaign_routing ON lifecycle_segment` — it is not stored on the `contacts` row.

### Airtable

- **Base ID:** `appuy2VTIao6XVpIW`
- **Menu Catalog** table (`tblmZBNdQvmFcvVai`): staff manage active items here → daily 6:30 AM n8n sync → `menu_catalog` (deleting a row marks it discarded in Postgres)
  - Fields: Item Name, Category, Is Veg, Description, Image URL, Price, Added Date
- **Field Sales Tasks:** escalated opportunities appear as Airtable records; agents update outcomes
- **Playbook Rules:** user-configurable; synced daily at 6 AM to `agent_playbook`

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
| `agents.py` | `get_latest_observations(contact_id)`, `get_latest_action_plan(contact_id)`, `get_orchestrator_history(contact_id)`, `get_pending_actions(limit)`, `get_ai_stack_summary(days)` |
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
| Database migrations | 65 |
| Database tables | 22+ |
| Stored functions | 15+ |
| n8n workflows | 30 |
| MCP tools | 35+ |
| Claude calls per contact cycle | 8 (3 + 4 + 1) |
| Signal types detected | 7 |
| Lifecycle segments | 8 |
| Email campaigns | 5 |
| E2E test cases | 55+ |

---

## 13. Feature Cross-Reference

Quick map of each feature group to its n8n workflows, Python routers, key DB tables, and test group.

| Feature | n8n Workflows | Python Routers | Key DB Tables | Test Group |
|---------|--------------|----------------|---------------|------------|
| **[Order Intake]** | Order Collector, Feedback Sync, Daily CSV Upload | `orders.py`, `daily_orders.py` | `contacts`, `orders`, `order_items` | G11 |
| **[SMS]** | Inbound Collector, Dispatch Queue | `sms.py`, `webhooks.py` | `telnyx_messages` | G5 |
| **[Broadcast]** | Dispatch | `broadcasts.py` | `broadcasts`, `broadcast_recipients` | G10 |
| **[Email Campaigns]** | Performance Tracker, Campaign Sync, Campaign Setup | `campaigns.py`, `prospects.py` | `campaign_routing`, `instantly_analytics` | G8 |
| **[Intelligence]** | Stage Runner, Contact Sweep, AI Stack, Lapsed Re-engagement | `lifecycle.py`, `intelligence.py`, `agents.py` | `rules`, `opportunities`, `contact_observations`, `action_plans`, `orchestrator_log`, `action_queue` | G6, G7 |
| **[Field Agent]** | Outcome Sync, Daily Brief | `field_agent.py` | `opportunities` (field_sales_call) | G9 |
| **[Agent Rules]** | Playbook Sync | `playbook.py` | `agent_playbook` | G9 |
| **[Menu]** | Catalog Sync | `menu.py` | `menu_catalog`, `menu_catalog_history` | G9 |
| **[Growth]** | Competitor Research, Goal Agent, Weekly Growth Agent | `competitor_agent.py`, `goal_agent.py`, `growth_agent.py` | `goal_experiments`, `experiments`, `competitor_analyses`, `discovered_signals` | G15 |
| **[Reports]** | Daily Activity Report, Daily Outcome Report | `reports.py` | `daily_reports` | G12 |
| **[Chatbot]** | Docs Sync, Docs Reindex | `chatbot.py`, `query.py`, `team_content.py` | `team_content` | G13 |
| **[System]** | Action Queue, Feature Tests, Connectivity Check | `test_harness.py`, `schedules.py` | `action_queue`, `test_runs` | G1, G2, G10 |
