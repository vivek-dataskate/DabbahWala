# DabbahWala — System Overview

DabbahWala is an automated AI-driven marketing orchestration system for a fresh Indian home-cooked food delivery service in Atlanta. It runs three independent intelligence engines, a 5-agent Claude AI pipeline, 26 active n8n workflows, and integrates with SMS (Telnyx), email campaigns (Instantly), field sales (Airtable), and order tracking (Shipday) — all coordinated through a FastAPI backend hosted on Render.

**Last updated: 2026-03-04**

---

## Table of Contents

1. [Architecture](#architecture)
2. [Three Engines](#three-engines)
3. [The Five Agents](#the-five-agents)
4. [n8n Workflow Layer](#n8n-workflow-layer)
5. [API Layer](#api-layer)
6. [Database](#database)
7. [External Services](#external-services)
8. [Customer Journeys](#customer-journeys)
9. [User Stories](#user-stories)
10. [Operations How-To](#operations-how-to)
11. [Deployment](#deployment)
12. [Troubleshooting](#troubleshooting)

---

## Architecture

```
External Events
  Telnyx (SMS/calls)  ─┐
  Shipday (orders)     ├──► FastAPI  (dabbahwala-latest.onrender.com)
  Instantly (email)    │         │
  CSV imports          ┘         ├─ Three Intelligence Engines
                                 │    ├─ Stage Engine   (SQL rules)
                                 │    ├─ Contact Sweep  (5-phase loop)
                                 │    └─ AI Stack       (Claude agents)
                                 │
                                 ├─ Five Agent Types
                                 │    ├─ Observer / Advisor / Orchestrator
                                 │    ├─ Goal Agent     (HYPOTHESIZE→EXPERIMENT→MEASURE→HARVEST)
                                 │    ├─ Growth Agent   (weekly experiments)
                                 │    ├─ Competitor Agent (weekly research)
                                 │    └─ Report Agents  (daily HTML emails)
                                 │
                                 └─ action_queue → n8n (digitalworker.dataskate.io)
                                                    ├─ SMS via Telnyx
                                                    ├─ Leads via Instantly
                                                    ├─ Tasks via Airtable
                                                    └─ Reports via Gmail SMTP
```

### Stack

| Component | Technology |
|-----------|-----------|
| Web framework | FastAPI (Python 3.11) |
| Database | PostgreSQL 16 (Supabase pooler, port 6543, transaction mode) |
| Automation | n8n self-hosted at `digitalworker.dataskate.io` |
| Hosting | Render (auto-deploy on merge to `main`) |
| AI | Claude Sonnet (`claude-sonnet-4-5-20250929`) + Haiku (`claude-haiku-4-5-20251001`) |
| Async HTTP | httpx |
| Validation | Pydantic v2.10 |

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | PostgreSQL connection string |
| `ANTHROPIC_API_KEY` | Claude agent calls |
| `TELNYX_API_KEY` | SMS + voice |
| `AIRTABLE_API_KEY` | CRM + playbook + menu sync |
| `AIRTABLE_BASE_ID` | `appuy2VTIao6XVpIW` |
| `SHIPDAY_API_KEY` | Delivery order tracking |
| `INSTANTLY_API_KEY` | Email campaign management |
| `ADMIN_SECRET` | Admin endpoint protection |
| `REPORT_EMAIL_TO` | Report recipient (default: `core@dabbahwala.com`) |

---

## Three Engines

The system runs three independent intelligence engines in parallel. No engine depends on another to run.

### Engine 1 — Stage Engine (Pure SQL)

**When:** Every hour via n8n `[Intelligence] Stage Runner`
**Claude calls:** 0

Evaluates SQL predicates against the `contacts` table and moves contacts between lifecycle segments. When a segment change warrants outreach, it writes a `push_instantly_lead` action to `action_queue`.

**8 lifecycle segments:**
```
cold → engaged → active_customer
new_customer (1 order) → active_customer (2+ orders, recent)
active_customer → lapsed_customer (14–29 days silent)
lapsed_customer → reactivation_candidate (30+ days silent)
any → optout | cooling (manual override)
```

**6 Instantly campaigns (one per segment):**
| Segment | Campaign |
|---------|---------|
| cold | DW-NurtureSlow-ColdContacts |
| engaged | DW-PromoStandard-ActiveEngaged |
| new_customer | DW-NewCustomerOnboarding |
| active_customer | DW-ActiveCustomer |
| lapsed_customer | DW-PromoAggressive-LapsedCustomers |
| reactivation_candidate | DW-Reactivation-LongDormant |

Routing is managed by the `campaign_routing` table (PK: `lifecycle_segment`) — the single source of truth for which campaign each segment maps to.

---

### Engine 2 — Contact Sweep (5-Phase Intelligence Loop)

**When:** Daily 7 AM via n8n `[Intelligence] Contact Sweep`; also triggered in real-time on inbound SMS
**Claude calls:** 0

Runs a 5-phase SQL loop over all contacts:

1. **COLLECT** — count recent events per contact
2. **PROFILE** — refresh 7d/30d engagement rollups
3. **SIGNAL** — detect 7 SQL patterns (see below)
4. **ROUTE** — create `opportunities` for flagged contacts
5. **DISPATCH** — run Stage Engine to sync lifecycle segments

**The 7 signals:**

| Signal | Condition | Channel | Confidence |
|--------|-----------|---------|-----------|
| `engaged_no_order` | 3+ email opens in 7d, no order | email | 0.75 |
| `new_customer_no_repeat` | 1 order, 5+ days ago, no second | SMS | 0.80 |
| `lapsed_reengaged` | lapsed segment + recent SMS reply or email click | field call | 0.90 |
| `reorder_intent` | call transcript contains reorder keywords | SMS | 0.92 |
| `app_customers_for_conversion` | primary source = delivery app, no direct order in 30d | SMS + campaign move | 0.82 |
| `subscription_candidates` | 3+ orders, no subscription type set | SMS pitch | 0.78 |
| `high_value_at_risk` | 5+ total orders, silent 14+ days, not lapsed/optout | field call | 0.88 |

---

### Engine 3 — AI Stack (4-Layer Claude Pipeline)

**When:** Daily 9 AM batch via n8n `[Intelligence] AI Stack` (cap 200 contacts, 72h cooldown per contact); real-time on inbound SMS
**Claude calls per contact:** 8 (3 Observer + 4 Advisor + 1 Orchestrator)
**Prompt caching:** System prompts cached (ephemeral) — 90% token discount from contact #2 onward in batch

The AI Stack runs a 4-layer pipeline per contact. Results are stored in separate DB tables and fed forward to the next layer.

#### Layer 1 — Observer (4 agents)

Input: Full contact profile + 30-day event log + communication history + active goal + current menu + playbook rules

| Agent | Model | Output stored in |
|-------|-------|-----------------|
| **Menu** | Haiku | `contact_observations.top_picks`, `.bridge_item`, `.avoid` |
| **Sentiment** | Haiku | `contact_observations.sentiment`, `.sentiment_confidence` |
| **Intent** | Sonnet | `contact_observations.intent`, `.intent_signals`, `.intent_confidence` |
| **Engagement** | Haiku | `contact_observations.engagement_score`, `.engagement_trend`, `.last_touch_hours_ago` |

#### Layer 2 — Advisor (4 agents)

Input: Layer 1 outputs + same contact context

| Agent | Model | Output stored in |
|-------|-------|-----------------|
| **Stage** | Haiku | `action_plans.recommended_stage`, `.stage_confidence` |
| **Channel** | Haiku | `action_plans.recommended_channel`, `.channel_timing` |
| **Offer** | Sonnet | `action_plans.offer_type`, `.suggested_copy` |
| **Escalation** | Sonnet | `action_plans.should_escalate`, `.urgency` |

#### Layer 3 — Orchestrator (1 agent)

Input: All Layer 1 + Layer 2 outputs + playbook rules

Applies guardrails in this order (highest precedence first):
1. `priority_override = do_not_contact` → always `none`, no exceptions
2. Delivery status overrides:
   - `out_for_delivery` / `driver_assigned` → `none` (never interrupt)
   - `delivery_failed` / `delivery_returned` → `escalate_airtable` (urgency = high)
   - `delivered` → delay 4h, then fire AI Stack (let customer eat first)
3. `intent = not_interested` → `none` unless escalation urgency is high
4. Contact frequency limits: max 1 per 24h same channel, max 3 SMS/week
5. Escalation beats automation — if `should_escalate = true`, skip automation

Output: one `chosen_action` written to `action_queue`:
- `send_sms` — queued for Telnyx dispatch
- `move_campaign` — queued for Instantly lead push
- `escalate_airtable` — queued for field sales task creation
- `none` — no action this cycle

Stored in `orchestrator_log` with full reasoning text.

#### Layer 4 — Report Agents (2 daily Claude calls)

| Agent | Time | Output |
|-------|------|--------|
| **Activity Report** | 8:00 AM | Claude-generated HTML summary of cycles, actions, SMS/calls sent |
| **Outcome Report** | 8:30 AM | Claude-generated HTML summary of orders, conversions, field agent scorecard |

Both reports are emailed to `REPORT_EMAIL_TO` via Gmail SMTP.

---

## The Five Agents

Beyond the AI Stack, three specialised Claude agents run independently to drive proactive growth.

---

### Agent 1 — Goal-Oriented Agent

**Route:** `POST /api/goal-agent/run`
**Schedule:** Daily 9 AM via n8n `[Growth] Goal Agent`
**Model:** Sonnet
**Purpose:** Sets a goal (e.g., "get more repeat orders"), generates experiment hypotheses, tests them against real contact cohorts, measures results, and promotes proven experiments into permanent `discovered_signals`.

**4-phase loop:**

```
HYPOTHESIZE → Claude generates 3-5 new experiment ideas from DB patterns
     ↓
EXPERIMENT → Select cohorts (up to 20 contacts), craft messages, queue via action_queue
     ↓
MEASURE (after 72h) → Count actual conversions in the cohort, compute conversion rate
     ↓
HARVEST → Winning experiments become new entries in discovered_signals
```

**Key tables:** `goal_experiments`, `goal_experiment_contacts`, `goal_agent_runs`, `discovered_signals`

**Measurement:** 72-hour window. An experiment "wins" if its cohort conversion rate beats the system baseline. Winners are promoted to `discovered_signals` — permanent reusable signals the rest of the system can act on.

**Endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/goal-agent/run` | Run full 4-phase cycle |
| `POST` | `/api/goal-agent/hypothesize` | Generate hypotheses only |
| `POST` | `/api/goal-agent/experiment` | Launch specific experiment |
| `POST` | `/api/goal-agent/measure` | Score completed experiments |
| `POST` | `/api/goal-agent/harvest` | Promote winners to discovered_signals |

---

### Agent 2 — Weekly Growth Agent

**Route:** `POST /api/growth/run-cycle`
**Schedule:** Weekly Monday via n8n `[Growth] Weekly Growth Agent`
**Model:** Sonnet
**Purpose:** Invents novel marketing experiments (timing tricks, offer hooks, message angles, channel sequences) that haven't been tried. Distinct from the Goal Agent: growth agent focuses on creative experimentation with larger cohorts (15–60 contacts) over longer measurement windows (14–28 days).

**5-phase loop:**

```
DESIGN → Claude generates experiment (type: timing / offer / message_angle / channel_sequence)
     ↓
COHORT → Select 15-60 eligible contacts (not opted out, not in another running experiment)
     ↓
DISPATCH → Create opportunities via action_queue for each cohort member
     ↓
MEASURE (14-28 days) → Check which cohort members ordered; compare to baseline conversion rate
     ↓
LEARN → Claude analyses results, generates next_hypothesis for follow-up
```

**Adaptive early cutoff:** If a cohort has already generated 30+ conversion events before the measurement deadline, scoring runs early.

**Measurement windows by type:**
- `offer` / pricing: 28 days (needs multiple ordering cycles)
- `channel_sequence`: 21 days
- `timing` / `message_angle`: 14 days minimum

**Key tables:** `experiments`, `experiment_contacts`, `growth_baseline`

**Endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/growth/run-cycle` | Design + launch new experiment (weekly) |
| `POST` | `/api/growth/measure` | Score experiments whose window has passed |
| `GET` | `/api/growth/experiments` | List all experiments with results |
| `GET` | `/api/growth/insights` | Claude-synthesised learnings across all experiments |
| `POST` | `/api/growth/baseline/update` | Recalculate 7-day baseline conversion rate |

---

### Agent 3 — Competitor Research Agent

**Route:** `POST /api/competitor-agent/run`
**Schedule:** Weekly Monday 6:30 AM via n8n `[Growth] Competitor Research` (before Goal Agent at 9 AM)
**Model:** Sonnet
**Purpose:** Researches competitor marketing tactics and auto-injects new experiment hypotheses into `goal_experiments` for the Goal Agent to pick up.

**3-phase cycle:**

```
RESEARCH → Parse .eml samples from data/cookunitysamples/ + scrape 5 competitor sites live
              (cookunity.com, hellofresh.com, factor75.com, freshly.com, sunbasket.com)
     ↓
GAP ANALYSIS → Compare competitor tactics to DabbahWala's already-tested experiments
               and proven_signals
     ↓
INJECT → Generate 8 novel hypotheses covering all 4 customer segments →
         INSERT into goal_experiments (status=pending, source=competitor_agent)
```

**4 mandatory customer segments per run:**
| Segment | Definition |
|---------|-----------|
| `never_ordered` | Signed up, no order placed |
| `one_and_done` | Exactly 1 order, 14+ days silent |
| `lapsing_regular` | 2–5 orders, 21+ days silent |
| `high_value_at_risk` | 5+ orders, 30+ days silent |

**Key tables:** `goal_experiments` (source=competitor_agent), `competitor_agent_runs`, `discovered_signals`

**Endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/competitor-agent/run` | Run full 3-phase research + inject cycle |
| `GET` | `/api/competitor-agent/runs` | List recent run logs |
| `GET` | `/api/competitor-agent/experiments` | List competitor-sourced experiments |

---

### Agents 4 & 5 — Report Agents

**Daily Activity Report** (`POST /api/agents/report/activity`) — 8:00 AM
**Daily Outcome Report** (`POST /api/agents/report/outcome`) — 8:30 AM

Claude generates structured HTML + CSV reports and emails them via Gmail SMTP to `REPORT_EMAIL_TO`.

- **Activity Report**: AI cycles run, actions queued, SMS/calls sent, contact sweep results
- **Outcome Report**: Orders placed, email open/reply rates, conversion attribution, field agent scorecard

---

### Playbook Injection (All Agents)

All agents receive only relevant playbook rules injected into their system prompt at runtime (RAG without vector store). Rules are managed in Airtable `Agent Playbook` table and synced daily at 6 AM.

| Category | Injected into |
|----------|--------------|
| `exclusion` | All agents (highest precedence — never contact) |
| `priority` | All agents (contact prioritisation) |
| `observer` | Layer 1 agents only |
| `advisor` | Layer 2 agents only |
| `messaging` | Layer 2 + Orchestrator |
| `general` | All agents |

---

## n8n Workflow Layer

27 total workflows: **26 active-scheduled** + **1 manual-only** (`[System] Connectivity Check`).

All workflows use a single `DW Admin Secret` HTTP Header Auth credential. All integration API keys are fetched at runtime via `GET /api/credentials` — no keys are stored in n8n.

### Complete Workflow List

#### [Order Intake]
| Workflow | ID | Schedule |
|----------|----|----|
| Order Collector | `AePBXRdPKkUQpHIT` | Every 30 min |
| Feedback Sync | `0pQY0otcvnGj8WBH` | Every hour |
| Daily CSV Upload | `6ZYQwdkmS5Nni05u` | Daily 1 PM EST |

#### [SMS]
| Workflow | ID | Schedule |
|----------|----|----|
| Inbound Collector | `xcNObK3qdU1wdf3f` | Every 30 min |
| Dispatch Queue | `w2bVQQ4hy33OdY1R` | Every 10 min |

#### [Broadcast]
| Workflow | ID | Schedule |
|----------|----|----|
| Dispatch | `oDEse7EvWHj6UVM4` | Every 1 hour |

#### [Email Campaigns]
| Workflow | ID | Schedule |
|----------|----|----|
| Performance Tracker | `ctCLyHDQc1VckMqL` | Every hour |
| Campaign Sync | `nCcBt9USIYxlOaJT` | Every 6 hours |
| Campaign Setup | `NbnkM3nTFKSgtcfb` | Daily midnight |

#### [Intelligence]
| Workflow | ID | Schedule |
|----------|----|----|
| Contact Sweep | `FcbBt0AIlkYoa01X` | Daily 7 AM |
| Stage Runner | `h80nX24myWwsbxuB` | Every hour |
| Lapsed Re-engagement | `S3jSnWb3UTv9HmJL` | Daily (random offset) |
| AI Stack | `VreWonSUTk4VCXPF` | Daily 9 AM |

#### [Field Agent]
| Workflow | ID | Schedule |
|----------|----|----|
| Outcome Sync | `chfGgYIjyTw6QP5m` | Every 4 hours |
| Daily Brief | `kOI33cFH4bM8OCaf` | Daily 7:30 AM |

#### [Agent Rules]
| Workflow | ID | Schedule |
|----------|----|----|
| Playbook Sync | `FXuYcwQeBQ72Xxyu` | Daily 6 AM |

#### [Menu]
| Workflow | ID | Schedule |
|----------|----|----|
| Catalog Sync | `baZV5ViA5lXNCTWR` | Weekly Mon 6:30 AM |

#### [Growth]
| Workflow | ID | Schedule |
|----------|----|----|
| Competitor Research | `GozoSXHiazEdhpni` | Weekly Mon 6:30 AM |
| Goal Agent | `w5kYj5vNsNW53W4n` | Daily 9 AM |
| Weekly Growth Agent | `Nbut2tjjksGvQYzH` | Weekly Mon 7:30 AM |

#### [Reports]
| Workflow | ID | Schedule |
|----------|----|----|
| Daily Activity Report | `91bMjrZxiCPTglEI` | Daily 8 AM |
| Daily Outcome Report | `fONTnqi4l9DT3aCo` | Daily 8:30 AM |

#### [Chatbot]
| Workflow | ID | Schedule |
|----------|----|----|
| Docs Sync | `oHtGvkCLTWYkxNZ0` | Every 30 min |
| Docs Reindex | `7mn3Ys0xMmZnZQIC` | Weekly Mon 2 AM |

#### [System]
| Workflow | ID | Schedule |
|----------|----|----|
| Action Queue | `RzR3ZNYlty7cuTDY` | Every 30 min |
| Feature Tests | `zlKQKfJ18QGIwogq` | Daily 5 AM |
| Connectivity Check | `ipSHdFUZMj2D0r0t` | **Manual only** |

---

## API Layer

**Base URL:** `https://dabbahwala-latest.onrender.com`

### Core Routers

| Router | Prefix | Key Endpoints |
|--------|--------|--------------|
| `agents.py` | `/api/agents` | `POST /cycle/run-for-contact`, `POST /cycle/run-all`, `POST /cycle/run-daily-sweep`, `GET /action-queue/pending`, `POST /action-queue/{id}/done`, `POST /report/{activity,outcome}` |
| `intelligence.py` | `/api/intelligence` | `POST /run-cycle`, `GET /pending-actions`, `POST /ingest-instantly-events` |
| `lifecycle.py` | `/api/lifecycle` | `POST /run` |
| `goal_agent.py` | `/api/goal-agent` | `POST /run`, `/hypothesize`, `/experiment`, `/measure`, `/harvest` |
| `growth_agent.py` | `/api/growth` | `POST /run-cycle`, `/measure`, `/baseline/update`, `GET /experiments`, `/insights` |
| `competitor_agent.py` | `/api/competitor-agent` | `POST /run`, `GET /runs`, `/experiments` |
| `campaigns.py` | `/api/campaigns` | `POST /push-lead`, `GET /pending`, `GET /active-contacts`, `POST /log-push`, `GET /analytics` |
| `orders.py` | `/api/shipday` | `POST /ingest-orders`, `GET /sync-status`, `POST /sync-feedback` |
| `sms.py` | `/api/telnyx` | `POST /message`, `/call`, `/field-agent-message` |
| `menu.py` | `/api/menu` | `GET /items`, `POST /sync` |
| `query.py` | `/api/query` | `POST /` (14 SQL + 1 Claude categories), `GET /categories` |
| `chatbot.py` | `/api/chatbot` | `POST /ask`, `GET /suggest`, `POST /reindex` |
| `playbook.py` | `/api/playbook` | `GET /rules`, `POST /sync-from-airtable` |
| `prospects.py` | `/api/prospects` | `POST /upload-csv`, `/add`, `/update-csv`, `GET /template` |
| `reports.py` | `/api/reports` | `GET /daily/{date}`, `POST /daily/{date}` |
| `webhooks.py` | `/api/webhooks` | `POST /instantly`, `/telnyx`, `/shipday`, `/sync-campaigns` |
| `test_harness.py` | `/api/test` | `POST /run`, `GET /results`, `GET /results/{run_id}` |
| `config.py` | `/api/credentials`, `/api/internal` | `GET /` (all API keys), `POST /send-email`, drive/docs helpers |
| `schedules.py` | `/api/admin` | `GET /schedules`, `POST /schedules/{workflow_id}` |

### Query Categories (`POST /api/query`)

14 Tier-1 SQL categories + 1 Tier-2 Claude free-form:

`lifecycle_overview` · `sms_performance` · `email_performance` · `activity_report` · `outcome_report` · `top_contacts` · `at_risk_contacts` · `reactivation_targets` · `campaign_performance` · `order_trends` · `field_agent_scorecard` · `signal_summary` · `ai_stack_summary` · `menu_performance` · `free_form` (Claude)

### Admin / Utility

- `GET /health` — DB connectivity check
- `GET /dashboard` — marketing intelligence dashboard (requires @dabbahwala.com OAuth)
- `POST /admin/migrate/{num}` — run specific migration (requires `ADMIN_SECRET`)
- `POST /admin/query` — read-only SQL (requires `ADMIN_SECRET`)
- `POST /admin/exec` — write SQL (requires `ADMIN_SECRET`)

---

## Database

### Core Tables

| Category | Tables |
|----------|--------|
| Contacts & Orders | `contacts`, `events`, `orders`, `order_items` |
| Menu | `menu_catalog`, `menu_catalog_history` |
| Communication | `telnyx_messages`, `telnyx_calls`, `delivery_status`, `engagement_rollups` |
| AI Stack | `contact_observations`, `action_plans`, `orchestrator_log`, `action_queue` |
| Experiments | `experiments`, `experiment_contacts`, `growth_baseline` |
| Goal Agent | `goal_experiments`, `goal_experiment_contacts`, `goal_agent_runs`, `discovered_signals` |
| Competitor | `competitor_agent_runs` |
| Configuration | `campaign_routing`, `campaign_push_log`, `agent_playbook`, `sms_templates`, `team_content`, `rules` |
| Intelligence | `opportunities`, `decision_log`, `daily_reports`, `test_runs` |
| Customer Goals | `customer_goals` |

### Migrations

Migrations live in `migrations/` and run idempotently on every deploy.

**5 consolidated baseline files** (edit these for most changes):

| File | Contents |
|------|---------|
| `001_schema_types.sql` | Enum types |
| `002_tables.sql` | All core tables + indexes |
| `003_functions.sql` | Stored procedures |
| `004_triggers_views.sql` | Triggers + views |
| `005_seed.sql` | Reference/seed data |

**Additive one-time files** (for destructive changes, backfills, or one-time runs):
- `006_add_missing_columns.sql`, `006_drop_campaign_queue.sql`
- `007_campaign_routing_stats_columns.sql`, `007_create_missing_tables.sql`
- `008_drop_campaign_queue.sql`

**Next available migration number: 009**

### Key Stored Functions

| Function | Purpose |
|----------|---------|
| `ingest_event()` | Event logging with audit trail |
| `run_lifecycle_cycle()` | Stage Engine: SQL rules → action_queue |
| `refresh_engagement_rollups()` | 7d/30d rolling metrics per contact |
| `create_opportunity()` | Opportunity deduplication + creation |
| `get_contact_detail()` | Full contact profile for AI context |
| `get_communication_history()` | Last N days of messages/calls |
| `get_lifecycle_summary()` | Segment distribution snapshot |
| `get_campaign_performance()` | Instantly campaign analytics |
| `suggest_reactivation_targets()` | Ranked lapsed contacts |
| `generate_daily_report()` | Raw data for report agents |

---

## External Services

### Telnyx (SMS + Voice)
- **From number:** `+18444322224`
- **Inbound:** Real-time webhook `POST /api/webhooks/telnyx` + 30-min polling fallback
- **Outbound:** n8n `[SMS] Dispatch Queue` (every 10 min) routes `send_sms` from action_queue
- **Unknown inbound:** Auto-creates contact (phone, source=Inbound, lifecycle=cold)

### Instantly (Email Campaigns)
- **Auth:** Bearer token (base64 `workspace_id:secret`) — never use `X-API-Key` header
- **6 campaigns** map to lifecycle segments via `campaign_routing` table
- **Lead push:** action_queue `push_instantly_lead` → n8n `[System] Action Queue` → Instantly API
- **Analytics:** n8n `[Email Campaigns] Performance Tracker` (hourly) updates `campaign_routing` stats

### Airtable (CRM + Configuration)
- **Base ID:** `appuy2VTIao6XVpIW`
- **Menu Catalog** (`tblmZBNdQvmFcvVai`) — staff manage active menu items; weekly sync to Postgres
- **Agent Playbook** (`tbljWs6hKWbYFufnM`) — user-configurable AI rules; daily 6 AM sync
- **Field Sales Tasks** — escalated opportunities appear here; field agents record outcomes via Airtable

### Shipday (Delivery Tracking)
- **Polled every 30 min** by n8n `[Order Intake] Order Collector`
- **Status mapping:** COMPLETED→`delivered`, FAILED→`delivery_failed`, RETURNED→`delivery_returned`, PICKED_UP→`out_for_delivery`, ASSIGNED→`driver_assigned`, ACCEPTED→`order_accepted`
- **Post-delivery:** 4-hour delay → AI Stack fires (let customer eat, then nudge for feedback/reorder)

### Google Docs / Drive
- **Drive folder:** `1O0ES9uiDL6AWf9QMMYiyRUWGtymDjPF5`
- **Polled every 30 min** by n8n `[Chatbot] Docs Sync`
- **Classification:** "ad copy", "social media" → `ad_copy`; rest → `ground_note`
- **Stored in:** `team_content` table with `google_doc_id` dedup

### Gmail SMTP (Reporting)
- **n8n credential:** `Gmail-SMTP` (ID: `Sk6XzPNPnJTXHEbr`)
- **Port:** 465 (SSL)
- **Used by:** Action Queue (`send_email_report` actions), Broadcast Dispatch (email channel), daily reports

---

## Customer Journeys

### Journey 1: Cold Lead → First Order
1. New contact imported → `lifecycle = cold` → enrolled in `DW-NurtureSlow-ColdContacts`
2. First email open → Stage Engine: `cold` → `engaged` → move to `DW-PromoStandard-ActiveEngaged`
3. After 3+ opens, no order → Signal `engaged_no_order` fires → SMS with menu highlight queued
4. Any SMS reply → real-time AI Stack fires → follow-up personalised by Observer/Advisor
5. First order → `new_customer` segment, goal `convert_to_order` marked achieved

### Journey 2: First Order → Repeat Buyer
1. Order confirmed → `lifecycle = new_customer` → `DW-NewCustomerOnboarding`
2. Delivery confirmed → 4-hour delay → thank-you SMS + reorder nudge
3. Day 5, no second order → Signal `new_customer_no_repeat` → subscription pitch SMS
4. Channel rotates (email → SMS → field call) if no response after each touch

### Journey 3: Active Customer Retention
1. Every delivery → 4-hour delay → thank-you SMS + reorder nudge
2. 3+ orders → Signal `subscription_candidates` → subscription pitch SMS
3. 14+ days silent (5+ total orders) → Signal `high_value_at_risk` (confidence 0.88) → hot field call created
4. AI Stack cycles every 72h — loyalty-focused messaging

### Journey 4: Lapsed Customer Re-engagement (14–29 Days Silent)
1. → Stage Engine: `lapsed_customer` → `DW-PromoAggressive-LapsedCustomers`
2. AI Stack escalates offer: discount → urgency → personal reference
3. Any engagement signal → `lapsed_reengaged` fires → hot field call (confidence 0.90)
4. 3+ no-answers → Escalation Agent recommends field sales intervention

### Journey 5: Dormant Win-Back (30+ Days Silent)
1. → Stage Engine: `reactivation_candidate` → `DW-Reactivation-LongDormant`
2. n8n `[Intelligence] Lapsed Re-engagement` runs daily (random offset)
3. AI Stack rotates channels indefinitely: SMS → email → field call → SMS (never stops unless optout)
4. Call transcript with reorder keywords → Signal `reorder_intent` → immediate hot SMS

### Journey 6: Delivery Failure → Relationship Recovery
1. Shipday reports FAILED/RETURNED → `delivery_failed` event logged within 30 min
2. AI Stack Orchestrator fires **immediately** — guardrail forces `escalate_airtable` (urgency = high)
3. Airtable task appears in minutes → field agent calls same day (apology + credit/refund offer)
4. Outcome recorded in Airtable → `[Field Agent] Outcome Sync` updates contact + unblocks outreach

---

## User Stories

### Operator (System Admin)

1. As an operator, I can upload a CSV of new prospects so they are immediately enrolled in outreach.
2. As an operator, I can check `GET /health` to confirm the DB is reachable before diagnosing failures.
3. As an operator, I can run the full E2E test suite via `POST /api/test/run` to verify system integrity.
4. As an operator, I can trigger a manual AI cycle for a specific contact to test pipeline behaviour.
5. As an operator, I can see the lifecycle distribution of all contacts at a glance via `lifecycle_overview` query.
6. As an operator, I can adjust workflow schedules via the dashboard or `/api/admin/schedules` without touching n8n.
7. As an operator, I can add a contact to a do-not-contact list by setting `priority_override = do_not_contact`.
8. As an operator, I can view the full action_queue and see what's pending, sent, or failed.
9. As an operator, I can re-run a failed migration by calling `POST /admin/migrate/{num}`.
10. As an operator, I can see which experiment is currently running and how it's performing via `/api/growth/experiments`.

### Field Sales Agent

1. As a field agent, I receive a daily brief at 7:30 AM with the top 10 contacts to call today.
2. As a field agent, I see each contact's order history, last interaction, and AI-suggested talk track.
3. As a field agent, I record outcomes in Airtable and the system updates the contact profile within 4 hours.
4. As a field agent, I am notified immediately when a high-value customer's delivery fails.
5. As a field agent, I can escalate a contact to "do not contact" and the AI will respect it within minutes.

### Marketing Manager

1. As a marketing manager, I can configure AI rules in Airtable without touching code and they take effect by 6 AM the next day.
2. As a marketing manager, I receive daily HTML + CSV reports by email summarising outreach and conversions.
3. As a marketing manager, I can see email campaign open/reply rates per lifecycle segment via `campaign_performance` query.
4. As a marketing manager, I can send a broadcast SMS or email to a specific lifecycle segment on demand.
5. As a marketing manager, I can view the weekly growth experiment and its hypothesis before it launches.
6. As a marketing manager, I can see competitor intelligence summaries generated weekly by the Competitor Agent.
7. As a marketing manager, I can update the menu in Airtable and it syncs to the AI context by Monday 6:30 AM.
8. As a marketing manager, I can query the system with plain English via the Tier-2 Claude query interface.

### Customer (Automated Journey)

1. As a new customer, I receive a thank-you SMS within 4 hours of my first delivery.
2. As a repeat customer, I receive personalised reorder nudges based on my actual order history.
3. As a lapsed customer, I receive progressively stronger win-back offers (discount → urgency → personal).
4. As a customer who hasn't ordered, I receive menu highlights tailored to my past email engagement.
5. As a customer whose delivery failed, I am called by a real person the same day with an apology and fix.
6. As a customer who opted out, I never receive another automated message — zero exceptions.

### Claude Desktop / MCP User

1. As an MCP user, I can ask "show me top reactivation targets" and get a ranked list with order history.
2. As an MCP user, I can ask "what's the open rate for the lapsed campaign" and get live Instantly stats.
3. As an MCP user, I can look up any contact by email or phone and see their full AI observation history.
4. As an MCP user, I can ask "which contacts have a hot priority opportunity" without writing SQL.
5. As an MCP user, I can check pending action_queue items and manually mark them done.

---

## Operations How-To

### Adding Contacts
```bash
# Bulk CSV
GET /api/prospects/template    # download template
POST /api/prospects/upload-csv # upload filled CSV

# Single contact
POST /api/prospects/add        # { name, email, phone, source }

# Bulk update existing
POST /api/prospects/update-csv # match by email/phone, update name/address/priority/notes
```

### Sending a Broadcast
```bash
POST /api/broadcasts
{
  "message": "Fresh Biryani special today only...",
  "channel": "sms|email",
  "audience_segment": "active_customer|lapsed_customer|all",
  "scheduled_at": null
}
```
Dispatched by n8n `[Broadcast] Dispatch` (every hour).

### Configuring Agent Behaviour (Playbook)
1. Open Airtable → `Agent Playbook` table
2. Add/edit rules with appropriate `Category` (exclusion, priority, observer, advisor, messaging, general)
3. Wait for daily 6 AM sync, or trigger immediately: `POST /api/playbook/sync-from-airtable`

**Category precedence:** exclusion > priority > messaging > observer/advisor > general

### Running AI Agent Cycles Manually
```bash
POST /api/agents/cycle/run-for-contact   # single contact
POST /api/agents/cycle/run-all           # all eligible (cap 200)
POST /api/agents/cycle/run-daily-sweep   # lapsed contacts (72h cooldown)
```

### Triggering Intelligence Sweep Manually
```bash
POST /api/intelligence/run-cycle   # full 5-phase sweep
POST /api/lifecycle/run            # Stage Engine only (faster)
GET  /api/opportunities/detect     # detect signals only, no rows created
```

### Querying the System
```bash
POST /api/query
{
  "category": "lifecycle_overview",   # or any of the 14 SQL categories or "free_form"
  "date_from": "2026-01-01",
  "date_to": "2026-03-04",
  "limit": 50
}
```

### Adjusting Workflow Schedules
- **Dashboard:** `https://dabbahwala-latest.onrender.com/dashboard` → Admin → schedule table
- **API:** `POST /api/admin/schedules/{workflow_id}` with new cron schedule

### Running Tests
```bash
POST /api/test/run              # full E2E suite (14 groups)
GET  /api/test/results/{run_id} # poll for results
```
Automated daily at 5 AM by n8n `[System] Feature Tests` → email report.

### Checking System Health
```bash
GET /health                     # DB check
```
Or in n8n: run `[System] Connectivity Check` manually → green/red per service (DB, Telnyx, Instantly, Airtable, Shipday, Anthropic).

### Adding Competitor Email Samples
Drop `.eml` files into `data/cookunitysamples/`. They are auto-detected on the next weekly Monday run of `[Growth] Competitor Research`.

---

## Deployment

```
git push to main
      ↓
GitHub Actions trigger
      ↓
Render auto-deploy (scripts/render_build.sh):
  1. pip install -r requirements.txt
  2. Run all migrations/*.sql (idempotent)
  3. uvicorn app.main:app --host 0.0.0.0 --port $PORT

n8n auto-sync (.github/workflows/sync_n8n.yml):
  - Pushes all n8n/*.json workflow files to digitalworker.dataskate.io
```

**Production URL:** `https://dabbahwala-latest.onrender.com`

### MCP Server (Claude Desktop)
Configure `~/.claude/claude_desktop_config.json` to connect to the MCP server at `mcp_server/server.py`. Tools cover: contacts, analytics, communications, recommendations, opportunities, agents, shipday, instantly.

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| AI cycles not running | `ANTHROPIC_API_KEY` expired | Update in Render env vars |
| SMS not sending | `[SMS] Dispatch Queue` paused or Telnyx key invalid | Check n8n workflow; run connectivity check |
| Emails not arriving | Gmail SMTP credential expired | Re-authorise n8n credential `Sk6XzPNPnJTXHEbr` |
| Contacts in wrong campaign | Lifecycle segment mismatch | `POST /api/lifecycle/run` to re-evaluate |
| Airtable tasks missing | `action_queue` stuck | Run `[System] Action Queue` manually in n8n |
| Playbook rules not applying | Sync hasn't run | `POST /api/playbook/sync-from-airtable` |
| Menu items stale | Airtable sync failed | `POST /api/menu/sync`; check `AIRTABLE_API_KEY` |
| Daily reports not arriving | SMTP failure or action_queue stuck | Check `action_queue` for pending `send_email_report`; verify SMTP creds |
| Lifecycle segments not moving | Stage Engine not running | `POST /api/lifecycle/run`; check n8n `[Intelligence] Stage Runner` |
| No orders in system | Shipday sync failed | `POST /api/shipday/ingest-orders`; verify `SHIPDAY_API_KEY` |
| Growth experiment not launching | Not enough eligible contacts | Check `/api/growth/experiments` — need 5+ non-opted-out contacts not in another experiment |
| Goal Agent not harvesting | No experiments hit success threshold | Check `/api/goal-agent/run` response; lower `success_threshold` in goal_experiments table |
| Competitor Agent not injecting | Sample dir missing or Claude error | Check `data/cookunitysamples/` exists; check `/api/competitor-agent/runs` for error_detail |

---

*System auto-deploys on merge to `main`. For Claude Code sessions, see `CLAUDE.md` for git workflow, credentials, and migration instructions.*
