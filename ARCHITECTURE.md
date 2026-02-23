# DabbahWala — System Architecture

DabbahWala is an automated customer marketing system built on a **4-layer Claude AI agent pipeline**. Events (orders, SMS replies, deliveries) trigger agents that analyse each contact, decide on actions, and enqueue them for execution by n8n automation workflows.

```
Events  ──→  Agent Pipeline (4 layers)  ──→  Action Queue  ──→  n8n Executors  ──→  Telnyx / Airtable / Instantly
```

---

## Agent Pipeline

### Layer 1 — Inference Agents (3 parallel Claude calls)

Three agents run concurrently per contact. Each reads the contact profile, events from the last 30 days, full communication history, and the contact's active goal.

| Agent | Output Fields |
|-------|--------------|
| **Sentiment** | `sentiment` (positive / neutral / negative), `confidence`, `summary` |
| **Intent** | `intent` (ready_to_order / needs_info / price_sensitive / not_interested / unknown), `signals[]`, `confidence` |
| **Engagement** | `engagement_score` (0-1), `trend` (rising / flat / falling), `last_touch_hours_ago` |

Persisted to: `inference_results`

---

### Layer 2 — Decision Agents (4 parallel Claude calls)

Four agents run concurrently, each receiving the full Layer 1 inference bundle.

| Agent | Output Fields |
|-------|--------------|
| **Stage** | `recommended_stage`, `confidence`, `reason` |
| **Channel** | `recommended_channel` (sms / email / call / none), `channel_timing` (immediate / tomorrow / 3days / none), `reason` |
| **Offer** | `offer_type` (discount / reminder / social_proof / none), `suggested_copy`, `reason` |
| **Escalation** | `should_escalate` (bool), `urgency` (high / medium / none), `reason` |

Persisted to: `decision_recommendations`

---

### Layer 3 — Orchestrator Agent (single final decision)

One Claude agent receives all Layer 2 recommendations plus the contact's latest delivery event. It applies guardrails and outputs exactly **one** action.

**Delivery overrides (highest priority):**
- `delivered` -> warm thank-you SMS with reorder nudge (skip if contacted in last 24 h)
- `delivery_failed` / `delivery_returned` -> escalate to Airtable as high urgency
- `out_for_delivery` / `driver_assigned` -> do nothing (order in flight)

**General guardrails:**
- Max 1 contact per 24 h on the same channel
- Max 3 SMS per week per contact
- Escalation always beats automation
- `not_interested` contacts -> always `none`

**Output:** `chosen_action` (send_sms / move_campaign / escalate_airtable / none), `chosen_channel`, `action_payload`, `reasoning`, `guardrails_applied[]`

Persisted to: `orchestrator_log` -> action inserted into `action_queue`

---

### Layer 4 — Report Agents (daily)

| Agent | Schedule | Output |
|-------|----------|--------|
| **Activity Report** | Daily 8:00 AM | Operational summary (runs, actions, escalations) — HTML email + CSV attachment |
| **Outcome Report** | Daily 8:30 AM | Results summary (orders, opens, conversions) — HTML email + CSV attachment |

Both reports are emailed to `core@dabbahwala.com`.

---

## Intelligence Cycle (5-phase, hourly)

A complementary rule-based system runs hourly alongside the agent pipeline:

```
INTAKE  ──→  EVIDENCE  ──→  INFERENCE  ──→  DECISION  ──→  EXECUTION
```

| Phase | What It Does |
|-------|-------------|
| **INTAKE** | Poll Instantly events, count recent Telnyx SMS/calls |
| **EVIDENCE** | Refresh 7-day engagement rollups, lifecycle distribution |
| **INFERENCE** | Detect 7 signal types (engaged_no_order, new_customer_no_repeat, lapsed_reengaged, reorder_intent, app_customers_for_conversion, subscription_candidates, high_value_at_risk) |
| **DECISION** | Create opportunities, queue campaign moves, trigger SMS |
| **EXECUTION** | Run lifecycle rules, prepare dispatch batches |

---

## Data Flow

```
1. Inbound event (Telnyx webhook / Shipday poll / Instantly email event / CSV upload)
   └─→ POST /api/events/ingest (or specialized endpoint)
       └─→ ingest_event() stored proc  ──→  events table

2. Event triggers agent cycle (real-time or scheduled)
   └─→ POST /api/agents/cycle/run-for-contact  (real-time, post-event)
       OR  Agent Orchestration Cron             (every 3 h, batch)
       ├─ Layer 1: 3 parallel Claude calls  ──→  inference_results
       ├─ Layer 2: 4 parallel Claude calls  ──→  decision_recommendations
       ├─ Layer 3: 1 Claude call            ──→  orchestrator_log
       └─ action_queue.insert(chosen_action, payload, status=pending)

3. n8n executor polls action_queue
   └─→ GET /api/agents/action-queue/pending
       ├─ send_sms              ──→  Telnyx API
       ├─ move_campaign         ──→  Instantly API
       ├─ escalate_airtable     ──→  Airtable API
       ├─ sync_airtable_task    ──→  Airtable API
       ├─ push_instantly_lead   ──→  Instantly API
       ├─ push_instantly_sequences ──→ Instantly API
       ├─ upload_google_drive   ──→  Google Drive API (OAuth2)
       └─ send_email_report     ──→  Gmail-SMTP (n8n credential Sk6XzPNPnJTXHEbr)
   └─→ POST /api/agents/action-queue/{id}/done
```

---

## n8n Workflow Layer

22 workflows on `digitalworker.dataskate.io`, all version-controlled in `n8n/`. All are active except `[Shipday — Evidence] Historical Import` (manual one-shot trigger).

Workflows follow the `[ExternalApp — FlowType] Name` taxonomy. All credential IDs are tracked in `n8n/config.json`.

| Group | Workflow | Schedule | Purpose |
|-------|----------|----------|---------|
| **Shipday** | Delivery Collector | Every 30 min | Fetches orders from Shipday, POSTs to `/api/shipday/ingest-orders` |
| **Shipday** | Feedback Sync | Hourly | Polls feedback, delivery instructions, proof-of-delivery |
| **Shipday** | Historical Import | Manual only | One-shot backfill of up to 1 year of order history |
| **Telnyx** | Inbound SMS Collector | Every 30 min | Ingests inbound SMS/calls, triggers real-time agent cycle |
| **Telnyx** | Broadcast Dispatch | Every 5 min | Dispatches queued broadcasts (SMS via Telnyx, email via server SMTP) |
| **Telnyx** | Broadcast Form | On form submit | n8n form UI for delay alerts and promo broadcasts |
| **Airtable** | Playbook Sync | Every 15 min | Syncs user-configured rules from Airtable |
| **Airtable** | Outcome Sync | Every 15 min | Pulls opportunity outcomes back from Airtable CRM |
| **Airtable** | Marketing Query Form | On-demand | Self-service query form → Claude inference → logs to Airtable |
| **Instantly** | Campaign Performance | Hourly | Fetches Instantly analytics per campaign, stores in DB |
| **Instantly** | Campaign Sync | Every 6 h | Fetches campaigns tagged `dabbahwala`, POSTs to `/api/webhooks/sync-campaigns` |
| **Instantly** | Campaign Setup | Daily midnight | Creates missing Instantly campaigns (no-op if all exist) |
| **Google** | Docs & Drive Sync | Every 30 min | Lists Drive folder, reads Google Docs, pushes to chatbot index via `/api/team-content/sync` |
| **Orders** | Daily CSV Upload | Daily 1 PM EST | Uploads daily CSV to `/api/daily-orders/process` |
| **Reporting** | Daily Field Brief | Daily 7:30 AM | Generates field sales call list via `/api/field-agent/daily-brief` |
| **Reporting** | Daily Activity Report | Daily 8:00 AM | Calls `/api/agents/report/activity` → Claude writes HTML + CSV → emails to `REPORT_EMAIL_TO` |
| **Reporting** | Daily Outcome Report | Daily 8:30 AM | Calls `/api/agents/report/outcome` → Claude writes HTML + CSV → emails to `REPORT_EMAIL_TO` |
| **Claude** | Agent Orchestration | Every 3 h | Batch agent cycle for all active contacts |
| **Claude** | Hourly Intelligence Cycle | Hourly | Full 5-phase: INTAKE → EVIDENCE → INFERENCE → DECISION → EXECUTION |
| **Claude** | Lifecycle Cycle Runner | Hourly | Runs `run_lifecycle_cycle()` SP — SQL rule-based stage transitions |
| **Claude** | Lapsed Customer Daily Cycle | Daily (random offset) | Persistent re-engagement for lapsed customers |
| **System** | Action Queue Executor | Every 30 min | Routes `action_queue` rows to: Telnyx (SMS), Instantly (leads/sequences), Airtable (tasks/escalations), Google Drive (CSV upload), Gmail-SMTP (email reports) |
| **System** | Chatbot Docs Reindex | Every Monday 2 AM | Housekeeping — refreshes chatbot document index |

---

## Database Schema

### Core Tables

| Table | Purpose |
|-------|---------|
| `contacts` | Master customer record (email, phone, lifecycle_segment, channel flags, order counts) |
| `events` | Raw event log (order_placed, email_open, sms_received, delivery_failed, etc.) |
| `orders` | Order records (order_ref, total_amount, delivery_slot, order_type) |
| `order_items` | Line items (menu_item_id, quantity, unit_price) |
| `menu_items` | Master menu (item_name, category, is_veg, avg_price) |
| `menu_item_aliases` | CSV dish name -> canonical menu item mapping |
| `telnyx_messages` | SMS tracking (direction, body, status, source, agent_name) |
| `telnyx_calls` | Call tracking (duration, transcript, summary) |
| `delivery_status` | Delivery updates (status, notes, location, updated_by) |
| `engagement_rollups` | 7-day/30-day rolling engagement metrics |

### Agent Pipeline Tables (migration 032)

| Table | Purpose |
|-------|---------|
| `customer_goals` | One active goal per contact (convert_to_order / retain / reactivate) |
| `inference_results` | Layer 1 outputs — sentiment, intent, engagement per cycle run |
| `decision_recommendations` | Layer 2 outputs — stage, channel, offer, escalation per run |
| `orchestrator_log` | Layer 3 chosen action, full reasoning text, guardrails applied |
| `action_queue` | Approved actions (pending -> executing -> done / failed) awaiting n8n |

### Configuration Tables

| Table | Purpose |
|-------|---------|
| `rules` | Lifecycle rule engine predicates + actions |
| `campaign_routing` | Lifecycle segment -> Instantly campaign mapping |
| `campaign_queue` | Pending campaign moves |
| `agent_playbook` | User-configured rules (synced from Airtable) |
| `sms_templates` | SMS A/B testing templates |
| `team_content` | Ground notes, ad copies, Google Docs sync |
| `decision_log` | Audit trail of lifecycle transitions |
| `daily_reports` | Aggregated daily metrics |

### Stored Functions

| Function | Purpose |
|----------|---------|
| `run_lifecycle_cycle()` | Main rule engine — evaluates predicates, transitions segments, queues campaigns |
| `refresh_engagement_rollups()` | Recalculate 7d/30d engagement metrics from events |
| `evaluate_rules()` | Core rule evaluation loop |
| `ingest_event()` | Event ingestion with audit trail |
| `store_telnyx_message()` | SMS storage with field agent support |
| `store_telnyx_call()` | Call record storage |
| `update_delivery_status()` | Delivery event processing |
| `create_opportunity()` | Opportunity creation with dedup |
| `get_contact_detail()` | Full contact profile with history |
| `get_communication_history()` | SMS + calls + deliveries for a contact |
| `suggest_reactivation_targets()` | Find contacts most likely to reactivate |

---

## API Surface

All routes served by the FastAPI app on Render.

| Router | Prefix | Key Endpoints |
|--------|--------|--------------|
| `agents.py` | `/api/agents` | `POST /cycle/run`, `/cycle/run-for-contact`, `/cycle/run-all`, `GET /action-queue/pending`, `POST /action-queue/{id}/done`, `POST /goals`, `POST /report/activity`, `POST /report/outcome` |
| `intelligence.py` | `/api/intelligence` | `POST /run-cycle`, `GET /pending-actions`, `POST /ingest-instantly-events` |
| `daily_orders.py` | `/api/daily-orders` | `POST /process`, `GET /summary/{date}` |
| `agent.py` | `/api/agent` | `POST /analyze-contacts`, `POST /analyze-single/{id}` |
| `query.py` | `/api/query` | `POST /` (10 Tier-1 SQL + 1 Tier-2 Claude categories), `GET /categories` |
| `lifecycle.py` | `/api/lifecycle` | `POST /run` — SQL rule engine |
| `opportunities.py` | `/api/opportunities` | `GET /detect`, `POST /`, `GET /pending`, `POST /{id}/dispatched`, `POST /{id}/outcome` |
| `campaigns.py` | `/api/campaigns` | `GET /pending`, `POST /{id}/executed` |
| `sms.py` | `/api/sms` | `GET /pending`, `POST /{id}/sent` |
| `telnyx.py` | `/api/telnyx` | `POST /message`, `POST /call`, `POST /field-agent-message` |
| `delivery.py` | `/api/delivery` | `POST /status` |
| `playbook.py` | `/api/playbook` | `GET /rules`, `POST /rules`, `POST /sync-from-airtable` |
| `team_content.py` | `/api/team-content` | `POST /sync`, `POST /submit`, `GET /browse`, `POST /search` |
| `reports.py` | `/api/reports` | `GET /daily/{date}`, `POST /daily/{date}` |
| `events.py` | `/api/events` | `POST /ingest` |

---

## MCP Server (Claude Desktop)

`mcp_server/` exposes PostgreSQL marketing data as Claude Desktop tools for monitoring and ad-hoc analysis.

| Tool Group | Tools |
|-----------|-------|
| **Contacts** | `get_contact_detail(email_or_id)`, `search_contacts(segment, flags, order_range)` |
| **Analytics** | `get_lifecycle_summary()`, `get_campaign_performance(campaign, days)`, `get_engagement_trends(days)` |
| **Communications** | `get_communication_history(contact_id, days)`, delivery tracking |
| **Recommendations** | `suggest_reactivation_targets(limit)`, `recommend_content_strategy(contact_id)` |
| **Opportunities** | `detect_opportunities()`, `create_opportunity()`, `get_high_intent_signals()` |
| **Agents** | `get_latest_inference(contact_id)`, `get_latest_decision(contact_id)`, `get_orchestrator_history(contact_id)`, `get_pending_actions(limit)`, `get_agent_cycle_summary(days)` |

---

## Component Map

```
┌───────────────────────────────────────────────────────────────────┐
│  INPUTS                                                           │
│  Telnyx (SMS/calls)  ·  Shipday (delivery)  ·  Instantly (email) │
│  Daily CSV orders    ·  Google Docs (team notes)                  │
└──────────────────────────────┬────────────────────────────────────┘
                               │ events
                               ▼
┌───────────────────────────────────────────────────────────────────┐
│  FastAPI  (Render — dabbahwala-latest.onrender.com)               │
│                                                                   │
│  /events/ingest  ──→  ingest_event() SP  ──→  events table       │
│                                                                   │
│  /agents/cycle/run-for-contact                                    │
│    ├─ Layer 1: Sentiment · Intent · Engagement  (3 parallel)     │
│    ├─ Layer 2: Stage · Channel · Offer · Escalation (4 parallel) │
│    ├─ Layer 3: Orchestrator  ──→  orchestrator_log               │
│    └────────────────────────→  action_queue (pending)            │
│                                                                   │
│  /intelligence/run-cycle                                          │
│    ├─ INTAKE  ──→  EVIDENCE  ──→  INFERENCE                      │
│    └─ DECISION  ──→  EXECUTION  ──→  opportunities               │
│                                                                   │
│  /query  ──→  Tier 1 (SQL) + Tier 2 (Claude) intelligence       │
└──────────────────────────────┬────────────────────────────────────┘
                               │ polls action_queue
                               ▼
┌───────────────────────────────────────────────────────────────────┐
│  n8n  (digitalworker.dataskate.io)                                │
│                                                                   │
│  Action Queue Executor  ──→  Telnyx / Instantly / Airtable        │
│  Action Queue Executor  ──→  Google Drive / Gmail-SMTP            │
│  Broadcast Dispatch     ──→  Telnyx (SMS) + server (email)        │
│  Airtable Outcome Sync  ──→  CRM feedback loop                   │
│  Lifecycle Cycle Runner ──→  SQL rule engine                      │
│  Report triggers  ──→  /report/activity · /report/outcome         │
│  Telnyx Collector  ──→  inbound SMS/calls                         │
│  Shipday Collector  ──→  delivery status                          │
│  Google Docs Sync  ──→  team content index                        │
└───────────────────────────────────────────────────────────────────┘
```

---

## External Service Integration

| Service | Purpose | Auth |
|---------|---------|------|
| **Anthropic Claude** | 4-layer agent pipeline (model: `claude-sonnet-4-5-20250929`) | `ANTHROPIC_API_KEY` |
| **Telnyx** | SMS sending/receiving, call transcripts, field agent logging | `TELNYX_API_KEY` |
| **Instantly** | Email campaigns (5 lifecycle-mapped campaigns) | `INSTANTLY_API_KEY` |
| **Airtable** | CRM, field sales tasks, playbook rules, outcome tracking | `AIRTABLE_API_KEY` |
| **Shipday** | Delivery tracking (order status, driver location) | `SHIPDAY_API_KEY` |
| **Google Drive** | CSV upload, chatbot doc sync | Google Drive OAuth2 (n8n cred `LUu1v42BgnEflv6f`) |
| **Google Docs** | Ground team notes, ad copies | Google Docs OAuth2 (n8n cred `FcNSuTgdmTt3M4D5`) |
| **Gmail / SMTP** | Daily report emails + broadcast emails | n8n cred `Sk6XzPNPnJTXHEbr` (`Gmail-SMTP`), reports to `REPORT_EMAIL_TO` |
| **n8n** | 22 automation workflows (all active) | `N8N_API_KEY` |

---

## Deployment

| Component | Platform | Region |
|-----------|----------|--------|
| FastAPI web service | Render (Starter plan) | Oregon |
| PostgreSQL 16 | Render (Starter plan) | Oregon |
| n8n workflows | Self-hosted (`digitalworker.dataskate.io`) | — |
| GitHub Actions | GitHub (auto-sync n8n on push) | — |

**Model:** All agent calls use `claude-sonnet-4-5-20250929`.
