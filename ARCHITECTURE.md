# DabbahWala — Agentic Architecture

DabbahWala is an automated customer marketing system built on a **4-layer Claude AI agent pipeline**. Events (orders, SMS replies, deliveries) trigger agents that analyse each contact, decide on actions, and enqueue them for execution by n8n automation workflows.

```
Events → Agent Pipeline (4 layers) → Action Queue → n8n Executors → Telnyx / Airtable / CRM
```

---

## Agent Pipeline

### Layer 1 — Inference Agents (parallel)

Three Claude agents run concurrently per contact. Each reads the contact profile, events from the last 30 days, full communication history, and the contact's active goal.

| Agent | Output fields |
|---|---|
| **Sentiment** | `sentiment` (positive / neutral / negative), `confidence`, `summary` |
| **Intent** | `intent` (ready_to_order / needs_info / price_sensitive / not_interested / unknown), `signals[]`, `confidence` |
| **Engagement** | `engagement_score` (0–1), `trend` (rising / flat / falling), `last_touch_hours_ago` |

Persisted to: `inference_results`

---

### Layer 2 — Decision Agents (parallel)

Four Claude agents run concurrently, each receiving the full Layer 1 inference bundle.

| Agent | Output fields |
|---|---|
| **Stage** | `recommended_stage`, `confidence`, `reason` |
| **Channel** | `recommended_channel` (sms / email / call / none), `channel_timing` (immediate / tomorrow / 3days / none), `reason` |
| **Offer** | `offer_type` (discount / reminder / social_proof / none), `suggested_copy`, `reason` |
| **Escalation** | `should_escalate` (bool), `urgency` (high / medium / none), `reason` |

Persisted to: `decision_recommendations`

---

### Layer 3 — Orchestrator Agent (single final decision)

One Claude agent receives all Layer 2 recommendations plus the contact's latest delivery event. It applies guardrails and outputs exactly **one** action.

**Delivery overrides (highest priority):**
- `delivered` → warm thank-you SMS with reorder nudge (skip if contacted in last 24 h)
- `delivery_failed` / `delivery_returned` → escalate to Airtable as high urgency
- `out_for_delivery` / `driver_assigned` → do nothing (order in flight)

**General guardrails:**
- Max 1 contact per 24 h on the same channel
- Max 3 SMS per week per contact
- Escalation always beats automation
- `not_interested` contacts → always `none`

**Output:** `chosen_action` (send_sms / move_campaign / escalate_airtable / none), `chosen_channel`, `action_payload`, `reasoning`, `guardrails_applied[]`

Persisted to: `orchestrator_log` → action inserted into `action_queue`

---

### Layer 4 — Report Agents (daily)

| Agent | Schedule | Output |
|---|---|---|
| **Activity Report** | Daily 8:00 AM | Operational summary (runs, actions, escalations) — emailed as HTML |
| **Outcome Report** | Daily 8:30 AM | Results summary (orders, opens, conversions) — emailed as HTML |

---

## Data Flow

```
1. Inbound event (Telnyx webhook / Shipday poll / Instantly email event)
   └─→ POST /events/ingest
       └─→ ingest_event() stored proc → events table

2. Event triggers agent cycle
   └─→ POST /cycle/run-for-contact   (real-time, post-event)
       OR scheduled Agent Orchestration Cron (every 3 h, batch)
       ├─ Layer 1: 3 parallel Claude calls  → inference_results
       ├─ Layer 2: 4 parallel Claude calls  → decision_recommendations
       ├─ Layer 3: 1 Claude call            → orchestrator_log
       └─ action_queue.insert(chosen_action, payload, status=pending)

3. n8n executor polls action_queue
   └─→ GET /action-queue/pending
       ├─ send_sms          → POST /sms/send → Telnyx API
       ├─ move_campaign     → Instantly API
       └─ escalate_airtable → Airtable API
   └─→ POST /action-queue/{id}/done
```

---

## n8n Workflow Layer

11 live workflows on `digitalworker.dataskate.io`. 9 are version-controlled in `n8n/`.

| Workflow | Schedule | Purpose |
|---|---|---|
| **Agent Orchestration Cron** | Every 3 h | Batch-runs agent cycle for all active contacts |
| **Action Queue Executor** | Every 30 min | Executes non-SMS queued actions (campaign moves, etc.) |
| **SMS Dispatch via Telnyx** | Every 10 min | Polls `action_queue`, dispatches SMS via Telnyx |
| **Lifecycle Cycle Runner** | Every 1 h | Runs `run_lifecycle_cycle()` SP — SQL-rule-based stage transitions |
| **Airtable Outcome Sync** | Every 15 min | Pulls opportunity outcomes back from Airtable |
| **Telnyx Inbound Collector** | Every 30 min | Ingests inbound SMS/call events |
| **Shipday Delivery Collector** | Every 30 min | Ingests delivery status updates from Shipday |
| **Daily Activity Report** | Daily 8:00 AM | Triggers `POST /report/activity` |
| **Daily Outcome Report** | Daily 8:30 AM | Triggers `POST /report/outcome` |
| **Opportunity Detection Cron** | Every 2 h | Detects & creates opportunities *(managed in n8n only)* |
| **Opportunity Dispatcher** | Every 5 min | Dispatches pending opportunities to Airtable *(managed in n8n only)* |

---

## Database Schema (agent tables)

Defined in `migrations/025_agent_tables.sql`.

| Table | Purpose |
|---|---|
| `customer_goals` | One active goal per contact (convert_to_order / retain / reactivate) |
| `inference_results` | Layer 1 outputs — sentiment, intent, engagement per cycle run |
| `decision_recommendations` | Layer 2 outputs — stage, channel, offer, escalation per run |
| `orchestrator_log` | Layer 3 chosen action, full reasoning text, guardrails applied |
| `action_queue` | Approved actions (pending → executing → done / failed) awaiting n8n |

---

## API Surface

All routes served by the FastAPI app on Render (`app/`).

| Router | Key endpoints |
|---|---|
| `agents.py` | `POST /cycle/run` `POST /cycle/run-for-contact` `POST /cycle/run-all` `GET /action-queue/pending` `POST /action-queue/{id}/done` `POST /action-queue/{id}/failed` `POST /goals` `POST /goals/{id}/achieved` |
| `lifecycle.py` | `POST /run` — SQL rule engine |
| `opportunities.py` | `POST /` `GET /pending` `POST /{id}/dispatched` `POST /{id}/outcome` |
| `sms.py` | SMS send / receive |
| `telnyx.py` | Telnyx webhook handler |
| `delivery.py` | Shipday delivery event ingestion |
| `campaigns.py` | Instantly campaign management |
| `reports.py` | Report generation triggers |
| `events.py` | Generic event ingestion |

---

## MCP Server

`mcp_server/` exposes Postgres marketing data as Claude Desktop tools for monitoring and ad-hoc analysis.

| Tool | Purpose |
|---|---|
| `get_latest_inference(contact_id)` | Read most recent Layer 1 results |
| `get_latest_decision(contact_id)` | Read most recent Layer 2 results |
| `get_orchestrator_history(contact_id)` | Read Layer 3 audit trail (last N decisions) |
| `get_pending_actions(limit)` | Read action_queue items awaiting execution |
| `get_agent_cycle_summary(days)` | Aggregated stats: runs, escalations, goals achieved |
| `suggest_reactivation_targets(limit)` | Contacts most likely to reactivate |
| `recommend_content_strategy(contact_id)` | Full history bundle for per-contact agent analysis |

---

## Component Map

```
┌─────────────────────────────────────────────────────────────────┐
│  INPUTS                                                         │
│  Telnyx (SMS/calls)  ·  Shipday (delivery)  ·  Instantly (email)│
└────────────────────────────┬────────────────────────────────────┘
                             │ events
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  FastAPI  (Render)                                              │
│                                                                 │
│  /events/ingest ──→ ingest_event() SP ──→ events table         │
│                                                                 │
│  /cycle/run-for-contact                                         │
│    ├─ Layer 1  Sentiment · Intent · Engagement  (parallel)     │
│    ├─ Layer 2  Stage · Channel · Offer · Escalation (parallel) │
│    ├─ Layer 3  Orchestrator  ──→  orchestrator_log             │
│    └──────────────────────────→  action_queue (pending)        │
└────────────────────────────┬────────────────────────────────────┘
                             │ polls action_queue
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  n8n  (digitalworker.dataskate.io)                             │
│                                                                 │
│  SMS Dispatch ──→ Telnyx                                       │
│  Action Queue Executor ──→ Instantly / misc                    │
│  Airtable Outcome Sync ──→ Airtable (field sales)             │
│  Lifecycle Cycle Runner ──→ SQL rule engine                    │
│  Opportunity Dispatcher ──→ Airtable                           │
│  Report triggers ──→ /report/activity · /report/outcome        │
└─────────────────────────────────────────────────────────────────┘
```

---

## Model

All agent calls use `claude-sonnet-4-5-20250929`.
