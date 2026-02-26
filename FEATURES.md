# DabbahWala — Feature Reference

Organized by the 12 feature groups used in n8n workflow naming.

> **Navigation:** [README](README.md) · [System Reference](SYSTEM.md) · [Claude Instructions](CLAUDE.md) · [Tests](TESTS.md)

---

## [Order Intake]

**Purpose:** Collect, store, and process all customer orders from Shipday delivery platform and daily CSV uploads.

**n8n Workflows:**
| Workflow | Schedule | What it does |
|----------|----------|--------------|
| `[Order Intake] Order Collector` | Every 30 min | Fetches orders from Shipday API → `POST /api/shipday/ingest-orders` |
| `[Order Intake] Feedback Sync` | Every hour | Fetches delivery feedback, instructions, POD photos from Shipday |
| `[Order Intake] Daily CSV Upload` | Daily 1 PM | Reads today's CSV from Google Drive → `POST /api/daily-orders/process` |
| `[Order Intake] Historical Import` | Manual only | One-shot backfill of up to 1 year of Shipday order history |

**Python:** `app/routers/orders.py` (was shipday_historical + shipday_sync), `app/routers/daily_orders.py`

**Tests:** Group 11 (Orders)

**Flow:**
```
Shipday API → n8n → POST /api/shipday/ingest-orders
  → contacts table (upsert by phone/email)
  → orders table
  → shipday_communications (feedback + POD)
  → events.ingest (delivery_update event)
  → lifecycle engine picks up new order
```

---

## [SMS]

**Purpose:** Inbound SMS collection from Telnyx MDR + outbound SMS dispatch from the action queue.

**n8n Workflows:**
| Workflow | Schedule | What it does |
|----------|----------|--------------|
| `[SMS] Inbound Collector` | Every 30 min | Polls Telnyx MDR API for inbound SMS → `POST /api/telnyx/message` |
| `[SMS] Dispatch Queue` | Every 10 min | Reads pending `send_sms` actions → sends via Telnyx API → marks dispatched |
| `[SMS] Historical Import` | Manual only | One-shot backfill of inbound SMS from Telnyx MDR (90-day history) |

**Python:** `app/routers/sms.py` (was telnyx.py), `app/routers/webhooks.py` (real-time Telnyx webhook)

**DB:** `telnyx_messages`, `action_queue` (send_sms rows)

**Tests:** Group 5 (Telnyx SMS)

**Flow:**
```
Inbound: Telnyx MDR poll → telnyx_messages (direction=inbound)
       + Telnyx webhook → POST /api/webhooks/telnyx → real-time telnyx_messages write

Outbound: AI Stack/Intelligence → action_queue (send_sms)
        → [SMS] Dispatch Queue → Telnyx Messages API → telnyx_messages (direction=outbound)
```

---

## [Broadcast]

**Purpose:** Mass SMS and email campaigns dispatched to audience segments. Separate from the AI Stack's individual outreach.

**n8n Workflows:**
| Workflow | Schedule | What it does |
|----------|----------|--------------|
| `[Broadcast] Dispatch` | Every 5 min | Reads pending `broadcast_recipients` → sends SMS (Telnyx) or email (SMTP) |
| `[Broadcast] Broadcast Form` | On form submit | n8n form UI for creating delay alerts and promo broadcasts |

**Python:** `app/routers/broadcasts.py`

**DB:** `broadcasts`, `broadcast_recipients`

**Tests:** Covered in Group 10 (Action Queue)

**Flow:**
```
Admin submits Broadcast Form → POST /api/broadcasts
  → broadcast_recipients populated
  → [Broadcast] Dispatch polls every 5 min → sends SMS/email → marks sent
```

---

## [Email Campaigns]

**Purpose:** Manage Instantly email campaigns that deliver ongoing outreach to lifecycle-segmented contacts.

**n8n Workflows:**
| Workflow | Schedule | What it does |
|----------|----------|--------------|
| `[Email Campaigns] Performance Tracker` | Every hour | Fetches Instantly analytics per campaign → DB |
| `[Email Campaigns] Campaign Sync` | Every 6 hours | Syncs Instantly campaigns tagged `dabbahwala` → `campaign_routing` |
| `[Email Campaigns] Campaign Setup` | Daily midnight | Creates missing campaigns in Instantly |
| `[Email Campaigns] Bulk Seed` | Manual only | Seeds all active contacts into their assigned Instantly campaigns |

**Python:** `app/routers/campaigns.py`, `app/routers/prospects.py`

**DB:** `campaign_routing`, `instantly_analytics`, `contacts.current_campaign`

**Tests:** Group 8 (Instantly)

**Flow:**
```
Stage Engine runs → contact.lifecycle_segment changes
  → campaign_routing maps segment → Instantly campaign ID
  → contact.current_campaign updated
  → [Email Campaigns] Bulk Seed or event-driven push adds lead to campaign
  → Instantly sends emails on its own schedule
  → [Email Campaigns] Performance Tracker pulls stats back to DB
```

---

## [Intelligence]

**Purpose:** Three cooperating engines that detect who needs attention, classify contacts, and run Claude reasoning. The core of the DabbahWala system.

### Stage Runner (SQL rules)
Pure SQL stored procedure. Moves contacts between 8 lifecycle stages based on order frequency, recency, and engagement. No Claude.

### Contact Sweep (Rule-based loop)
Hourly 5-phase rule-based loop: `COLLECT → PROFILE → SIGNAL → ROUTE → DISPATCH`. Determines which contacts need outreach today, routes them to the AI Stack or action queue. No Claude.

### AI Stack (Claude pipeline)
4-layer Claude reasoning pipeline: `Observer → Advisor → Orchestrator → Reports`. Per-contact analysis using full history. Produces one concrete outreach action per contact.

### Lapsed Re-engagement
Daily cycle targeting contacts silent for 30+ days. Uses AI Stack with escalating urgency and creative copy variation.

**n8n Workflows:**
| Workflow | Schedule | What it does |
|----------|----------|--------------|
| `[Intelligence] Stage Runner` | Every hour | `POST /api/lifecycle/run` — Stage Engine SQL proc |
| `[Intelligence] Contact Sweep` | Every hour | `POST /api/intelligence/run-cycle` — 5-phase sweep |
| `[Intelligence] AI Stack` | Every 3 hours | `POST /api/agents/cycle` — Claude 4-layer pipeline |
| `[Intelligence] Lapsed Re-engagement` | Daily (random 0–8h offset) | `POST /api/agents/cycle/lapsed` — re-engagement sweep |

**Python:** `app/routers/lifecycle.py`, `app/routers/intelligence.py`, `app/routers/agents.py`

**DB:** `rules`, `decision_log`, `contacts.lifecycle_segment`, `contact_observations`, `action_plans`, `action_queue`, `agent_playbook`

**Tests:** Groups 6 (Agent Pipeline), 7 (Intelligence)

**Flow:**
```
Every hour:
  Stage Runner: evaluate_rules() SQL proc → contacts.lifecycle_segment updated
  Contact Sweep: COLLECT→PROFILE→SIGNAL→ROUTE→DISPATCH → action_queue rows created

Every 3 hours:
  AI Stack: Observer (9 Haiku/Sonnet calls) → contact_observations
           → Advisor (4 calls) → action_plans
           → Orchestrator (1 Sonnet call) → action_queue (concrete action)
           → Reports (1 Sonnet) → daily_activity_report
```

---

## [Field Agent]

**Purpose:** Support field sales reps with daily call briefs and sync outcomes back from Airtable.

**n8n Workflows:**
| Workflow | Schedule | What it does |
|----------|----------|--------------|
| `[Field Agent] Outcome Sync` | Every 15 min | Reads call outcomes from Airtable Field Sales Tasks → updates contacts |
| `[Field Agent] Daily Brief` | Daily 7:30 AM | Generates field sales brief → emails to team |

**Python:** `app/routers/field_agent.py`

**DB:** `contacts.priority_flag`, `contacts.notes`, Airtable `Field Sales Tasks` table

**Tests:** Covered in Group 9 (Airtable)

---

## [Agent Rules]

**Purpose:** Sync configurable agent playbook rules from Airtable into Postgres so Claude agents can load them at inference time.

**n8n Workflows:**
| Workflow | Schedule | What it does |
|----------|----------|--------------|
| `[Agent Rules] Playbook Sync` | Every 15 min | Reads Airtable `Agent Playbook` table → `POST /api/playbook/sync-from-airtable` |

**Python:** `app/routers/playbook.py`

**DB:** `agent_playbook` (rule_name, category, instruction, priority, is_active)

**Tests:** Group 9 (Airtable) — `airtable_playbook_rules_exist` test

**Categories:** `exclusion`, `priority`, `observer`, `advisor`, `messaging`, `general`

---

## [Menu]

**Purpose:** Keep the Postgres menu catalog in sync with Airtable as the single source of truth for the active menu.

**n8n Workflows:**
| Workflow | Schedule | What it does |
|----------|----------|--------------|
| `[Menu] Catalog Sync` | Daily 6:30 AM | Reads Airtable `Menu Catalog` → upsert into `menu_catalog`, detect deletions |

**Python:** `app/routers/menu.py` (was airtable_menu.py)

**DB:** `menu_catalog`, `menu_catalog_history`

**Airtable:** Base `appuy2VTIao6XVpIW`, Table `Menu Catalog` (ID: `tblmZBNdQvmFcvVai`)

**Tests:** Covered in Group 9 (Airtable)

---

## [Growth]

**Purpose:** Run automated growth experiments to improve repeat order rates. Competes with existing competitors. Sets and tracks experiment goals.

**n8n Workflows:**
| Workflow | Schedule | What it does |
|----------|----------|--------------|
| `[Growth] Competitor Research` | Monday 6:30 AM | Parses competitor samples, scrapes live sites, Claude generates 8 hypotheses → `goal_experiments` |
| `[Growth] Goal Agent` | Daily 9 AM | 4-phase experiment loop: HYPOTHESIZE → EXPERIMENT → MEASURE → HARVEST |
| `[Growth] Weekly Growth Agent` | Monday 7:30 AM | Measures experiments, designs new ones, emails growth report |

**Python:** `app/routers/competitor_agent.py`, `app/routers/goal_agent.py`, `app/routers/growth_agent.py`

**DB:** `goal_experiments`, `goal_experiment_contacts`, `goal_agent_runs`, `experiments`, `competitor_analyses`

**Tests:** Group 15 (Competitor Agent)

**Flow:**
```
Monday:
  [Growth] Competitor Research → 8 hypotheses injected into goal_experiments
  [Growth] Weekly Growth Agent → measure running experiments + design new ones

Daily 9 AM:
  [Growth] Goal Agent HYPOTHESIZE → new experiments from backlog
           EXPERIMENT → select test/control cohorts, queue SMS actions
           MEASURE → check 72h conversion rates
           HARVEST → proven experiments → discovered_signals table
```

---

## [Reports]

**Purpose:** Daily Claude-written HTML + CSV reports emailed to the team each morning.

**n8n Workflows:**
| Workflow | Schedule | What it does |
|----------|----------|--------------|
| `[Reports] Daily Activity Report` | Daily 8 AM | `POST /api/agents/report/activity` → Claude writes HTML + emails |
| `[Reports] Daily Outcome Report` | Daily 8:30 AM | `POST /api/agents/report/outcome` → Claude writes HTML + emails |
| `[Field Agent] Daily Brief` | Daily 7:30 AM | Field sales brief → email |

**Python:** `app/routers/reports.py`

**DB:** `agent_runs`, report data from contacts/orders/action_queue

**Tests:** Group 12 (Reports)

---

## [Chatbot]

**Purpose:** Index Google Docs knowledge base and answer natural language questions about the business, menu, and marketing system.

**n8n Workflows:**
| Workflow | Schedule | What it does |
|----------|----------|--------------|
| `[Chatbot] Docs Sync` | Every 30 min | Reads Google Docs from Drive folder → chatbot index |
| `[Chatbot] Docs Reindex` | Monday 2 AM | Full reindex of all docs |
| `[Chatbot] Query Form` | On form submit | n8n form → `POST /api/query` → Claude answer |

**Python:** `app/routers/chatbot.py`, `app/routers/query.py`, `app/routers/team_content.py`

**DB:** `chatbot_documents`, `chatbot_chunks` (or equivalent)

**Google Drive:** Folder `1O0ES9uiDL6AWf9QMMYiyRUWGtymDjPF5`

**Tests:** Group 13 (Query / Chatbot)

---

## [System]

**Purpose:** Infrastructure: action queue execution, system tests, and service connectivity checks.

**n8n Workflows:**
| Workflow | Schedule | What it does |
|----------|----------|--------------|
| `[System] Action Queue` | Every 30 min | Routes pending `action_queue` rows to Airtable, Instantly, Google Drive, SMTP, Telnyx |
| `[System] Feature Tests` | Daily 5 AM | 11 parallel nodes — one per test group (G1–G14). Each shows green/red in n8n. |
| `[System] Connectivity Check` | Manual | 6 parallel nodes — one per service (FastAPI, Telnyx, Airtable, Instantly, Shipday, Google). |

**Python:** `app/routers/test_harness.py`, `app/services/test_harness_service.py`

**DB:** `action_queue`, `test_runs`, `test_results`

**Tests:** Groups 1 (Connectivity), 2 (Schema), 10 (Action Queue)

**Action Types in Queue:**
| Action | Target |
|--------|--------|
| `send_sms` | Telnyx Messages API |
| `send_email_report` | Gmail SMTP → `/api/internal/send-email` |
| `upload_google_drive` | Google Drive → `/api/internal/drive/upload` |
| `sync_airtable_task` | Airtable Field Sales Tasks |
| `push_instantly_lead` | Instantly v2 Leads API |
| `push_instantly_sequences` | Instantly Sequences API |

---

## Credential Architecture

All API keys are stored as Render environment variables and fetched at runtime by n8n workflows via `GET /api/credentials` (protected by `X-Admin-Secret` header). n8n stores only one credential: `DW Admin Secret`.

| Service | Env Var | Used By |
|---------|---------|---------|
| Telnyx | `TELNYX_API_KEY` | [SMS], [Broadcast] |
| Airtable | `AIRTABLE_API_KEY` | [Agent Rules], [Field Agent], [Menu] |
| Instantly | `INSTANTLY_API_KEY` | [Email Campaigns] |
| Shipday | `SHIPDAY_API_KEY` | [Order Intake] |
| Gmail SMTP | `GMAIL_SMTP_USER` / `GMAIL_SMTP_PASSWORD` | [Reports], [Broadcast] |
| Google Drive | `GOOGLE_SERVICE_ACCOUNT_JSON` | [Chatbot] |
| Admin | `ADMIN_SECRET` | All workflows (bootstrap) |

---

## Test Groups Reference

| Group | Name | Coverage |
|-------|------|----------|
| G1 | Connectivity | DB, API health, Telnyx, Instantly, Airtable |
| G2 | Schema | All tables and columns present |
| G3 | Contact Setup | Create test contact + order |
| G4 | Events | Event ingestion |
| G5 | Telnyx SMS | Send + receive SMS via Telnyx |
| G6 | Agent Pipeline | Observer → Advisor → Orchestrator |
| G7 | Intelligence | Contact Sweep 5 phases |
| G8 | Instantly | Campaign operations |
| G9 | Airtable | Playbook rules, field sales tasks |
| G10 | Action Queue | Queue + dispatch actions |
| G11 | Orders | Shipday order ingestion |
| G12 | Reports | Daily activity + outcome reports |
| G13 | Chatbot | Query answering |
| G14 | Cleanup | Delete all test data |
| G15 | Competitor Agent | Competitor research cycle |
