# DabbahWala Marketing System

Automated, AI-driven marketing orchestration for DabbahWala — a fresh Indian food delivery service in Atlanta. The system combines a **4-layer Claude AI agent pipeline** with rule-based lifecycle automation, multi-channel outreach (SMS, email, field sales), and a self-service intelligence interface for the marketing team.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | FastAPI (Python 3.11) on Render |
| **Database** | PostgreSQL 16, `dabbahwala` schema, pgvector for semantic search |
| **AI** | Claude Sonnet 4.5 — 4-layer agent pipeline (inference, decision, orchestrator, reporting) + menu suggestion agent + growth hacker agent |
| **Automation** | n8n (25 workflows, all active) on `digitalworker.dataskate.io` |
| **SMS/Voice** | Telnyx — outbound SMS, inbound message/call ingestion, OTP reading, field agent logging |
| **Email** | Instantly — 5 lifecycle-mapped campaigns |
| **CRM** | Airtable — field sales tasks, playbook rules, outcome tracking |
| **Delivery** | Shipday — real-time delivery status polling |
| **Content** | Google Docs — ground team notes, ad copies |
| **Menu Management** | Airtable-driven — staff edit in Airtable, hourly n8n sync → Postgres, `/menu-dashboard` CRUD UI |
| **MCP** | Claude Desktop integration for ad-hoc marketing analysis |

## System Architecture

```
  ┌──────────────────────────────────────────────────────────┐
  │  INPUTS                                                   │
  │  Telnyx (SMS/calls)  ·  Shipday (delivery)  ·  Instantly │
  │  Daily CSV orders    ·  Google Docs (team notes)          │
  └────────────────────────────┬─────────────────────────────┘
                               │ events
                               ▼
  ┌──────────────────────────────────────────────────────────┐
  │  FastAPI  (Render)                                        │
  │                                                           │
  │  /events/ingest ──→ ingest_event() ──→ events table      │
  │                                                           │
  │  /agents/cycle/run-for-contact                            │
  │    ├─ Layer 1: Sentiment · Intent · Engagement (3x)      │
  │    ├─ Layer 2: Stage · Channel · Offer · Escalation (4x) │
  │    ├─ Layer 3: Orchestrator (1 final decision)           │
  │    └──→ action_queue (pending)                            │
  │                                                           │
  │  /intelligence/run-cycle                                  │
  │    ├─ INTAKE → EVIDENCE → INFERENCE → DECISION → EXEC   │
  │    └──→ opportunities + campaign moves                    │
  └────────────────────────────┬─────────────────────────────┘
                               │ polls action_queue
                               ▼
  ┌──────────────────────────────────────────────────────────┐
  │  n8n  (digitalworker.dataskate.io) — 22 workflows          │
  │  Broadcast Dispatch ──→ Telnyx (SMS) / Gmail-SMTP (email) │
  │  Action Queue Executor ──→ Instantly / Airtable / Drive   │
  │  Action Queue Executor ──→ Gmail-SMTP (report emails)     │
  │  Google Docs Sync ──→ chatbot index                       │
  └──────────────────────────────────────────────────────────┘
```

## API Endpoints

### Agent Pipeline (`/api/agents`)
| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/cycle/run` | Run full 3-layer cycle for a list of contact IDs |
| POST | `/cycle/run-for-contact` | Run cycle for single contact (by phone/email) |
| POST | `/cycle/run-all` | Run cycle for all active-goal/high-engagement contacts |
| GET | `/action-queue/pending` | Pending actions for n8n executors |
| POST | `/action-queue/{id}/done` | Mark action executed |
| POST | `/action-queue/{id}/failed` | Mark action failed |
| POST | `/goals` | Create customer goal (convert/retain/reactivate) |
| POST | `/goals/{id}/achieved` | Mark goal achieved |
| POST | `/report/activity` | Generate + email daily activity report |
| POST | `/report/outcome` | Generate + email daily outcome report |

### Intelligence Cycle (`/api/intelligence`)
| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/run-cycle` | Full 5-phase intelligence cycle (hourly) |
| GET | `/pending-actions` | Actions for n8n execution |
| POST | `/ingest-instantly-events` | Poll Instantly campaign events |

### Daily Orders (`/api/daily-orders`)
| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/process` | Upload CSV, create contacts/orders/items, detect opportunities |
| GET | `/summary/{date}` | Order summary for a date |

### Smart Agent (`/api/agent`)
| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/analyze-contacts` | Claude-powered batch opportunity detection (menu-aware) |
| POST | `/analyze-single/{id}` | Single-contact deep analysis |

Each contact profile now includes `current_menu` (this week's menu) and `new_to_customer_this_week` (items the customer has never ordered). Claude uses these to craft hyper-personalised, menu-specific messages.

### Weekly Menu Sync (`/api/menu`)
| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/sync` | Accept scraped menu payload and store in `weekly_menu` table |
| GET | `/current` | Return this week's active menu (used by Claude agent) |
| POST | `/scrape-trigger` | Launch Playwright scraper (OTP via Telnyx) as subprocess |
| GET | `/history` | Audit log of past syncs |

The scraper (`scripts/scrape_menu.py`) runs every Monday at 6 AM, navigates to the subscription builder, enters the Telnyx business number, reads the OTP from `telnyx_messages`, and extracts menu items from the rendered page.

### Growth Hacker Agent (`/api/growth`)
| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/run-cycle` | Claude designs + launches a new marketing experiment |
| POST | `/measure` | Score experiments whose 7-day window has elapsed |
| POST | `/baseline/update` | Recalculate the baseline 7-day conversion rate |
| GET | `/experiments` | List all experiments with win/loss results |
| GET | `/insights` | Claude-synthesised learnings across all completed experiments |

Experiments run every Monday. Claude invents novel hypotheses across four types: **timing** (unusual send windows), **offer** (free add-ons, credits), **message_angle** (scarcity, nostalgia, social proof), and **channel_sequence** (SMS → email follow-up). Results are measured after 7 days, learnings feed future hypotheses.

### Marketing Query (`/api/query`)
| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/` | Self-service intelligence — 10 Tier-1 (SQL) + 1 Tier-2 (Claude) categories |
| GET | `/categories` | List available query categories |

### Lifecycle & Campaigns
| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/lifecycle/run` | Execute SQL rule engine |
| GET | `/api/campaigns/pending` | Pending campaign moves |
| POST | `/api/campaigns/{id}/executed` | Mark campaign executed |

### Opportunities (`/api/opportunities`)
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/detect` | Detect engaged-no-order opportunities |
| GET | `/detect/new-customer-no-repeat` | New customers not reordering |
| GET | `/detect/lapsed-reengaged` | Lapsed customers re-engaging |
| GET | `/detect/reorder-intent` | Reorder intent from transcripts |
| POST | `/` | Create opportunity |
| GET | `/pending` | Pending opportunities for dispatch |
| POST | `/{id}/dispatched` | Mark dispatched to Airtable |
| POST | `/{id}/outcome` | Record outcome |

### Communications
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/sms/pending` | Pending SMS queue |
| POST | `/api/sms/{id}/sent` | Mark SMS sent |
| POST | `/api/telnyx/message` | Ingest Telnyx SMS |
| POST | `/api/telnyx/call` | Ingest Telnyx call transcript |
| POST | `/api/telnyx/field-agent-message` | Log field agent SMS |
| POST | `/api/delivery/status` | Update delivery status |

### Playbook & Content
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/playbook/rules` | Active playbook rules |
| GET | `/api/playbook/rules/for-prompt` | Rules formatted for Claude system prompt |
| POST | `/api/playbook/rules` | Create rule |
| PUT | `/api/playbook/rules/{id}` | Update rule |
| DELETE | `/api/playbook/rules/{id}` | Deactivate rule |
| POST | `/api/playbook/sync-from-airtable` | Sync rules from Airtable |
| POST | `/api/team-content/sync` | Ingest from Google Docs |
| POST | `/api/team-content/submit` | Form submission |
| GET | `/api/team-content/browse` | Browse recent content |
| POST | `/api/team-content/search` | Full-text search |

### Menu Management (`/api/menu`)
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/items` | List menu items from Postgres (filter by week, category, active) |
| POST | `/items` | Create item in Airtable + Postgres |
| PUT | `/items/{id}` | Update item in Airtable + Postgres |
| DELETE | `/items/{id}` | Delete item from Airtable + Postgres |
| POST | `/sync` | Pull all Airtable "Weekly Menu" records → upsert Postgres (called by n8n hourly) |

### Reports & Admin
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/reports/daily/{date}` | Fetch daily report |
| POST | `/api/reports/daily/{date}` | Generate daily report |
| GET | `/health` | Health check (DB connectivity) |
| POST | `/admin/migrate/{num}` | Run specific migration (requires ADMIN_SECRET) |
| POST | `/admin/query` | Read-only SQL (requires ADMIN_SECRET) |
| POST | `/admin/exec` | Write SQL (requires ADMIN_SECRET) |

## Database Schema (54 migrations)

| # | Migration | Purpose |
|---|-----------|---------|
| 001 | `enums` | lifecycle_segment, campaign_name, event_type, delivery_status, opportunity_action |
| 002 | `contacts` | Master table — email, phone, lifecycle, channel flags, order counts |
| 003 | `events` | Raw event intake (order_placed, email_open, sms_received, etc.) |
| 004 | `engagement_rollups` | 7-day/30-day rolling engagement metrics |
| 005 | `rules` | Lifecycle rule engine predicates + actions |
| 006 | `decision_log` | Audit trail of lifecycle transitions |
| 007 | `campaign_queue` | Pending campaign moves |
| 008 | `telnyx_tracking` | `telnyx_messages` + `telnyx_calls` (SMS/call history) |
| 009 | `fn_refresh_rollups` | `refresh_engagement_rollups()` function |
| 010 | `fn_rule_engine` | `evaluate_rules()` core function |
| 011 | `fn_lifecycle_api` | `run_lifecycle_cycle()` main function |
| 012 | `triggers` | Auto-update triggers for `updated_at` |
| 013 | `seed_rules` | Default lifecycle rules (first order, lapsed, reactivation) |
| 014 | `seed_campaign_routing` | Lifecycle segment -> Instantly campaign mapping |
| 015 | `daily_reports` | `daily_reports` table + `generate_daily_report()` |
| 016 | `opportunities` | Opportunity CRUD tables |
| 017 | `fn_delivery_telnyx` | `update_delivery_status()`, `store_telnyx_message()`, `store_telnyx_call()` |
| 018 | `fn_opportunities` | `create_opportunity()`, `mark_opportunity_dispatched()` |
| 019 | `fn_analytics_reports` | `get_lifecycle_summary()`, `get_campaign_performance()` |
| 020 | `fn_contacts_comms` | `get_contact_detail()`, `search_contacts()`, `get_communication_history()` |
| 021 | `fn_recommendations` | `suggest_reactivation_targets()`, signal detectors |
| 022 | `vector_search` | pgvector: `content_embeddings` (1536-dim), `intent_phrases` |
| 023 | `instantly_campaign_routing` | Initial Instantly campaign IDs |
| 024 | `menu_orders` | `menu_items`, `orders`, `order_items` for daily order ingestion |
| 025 | `sms_templates` | SMS A/B testing templates + `agent_playbook` |
| 026 | `campaign_routing_update` | Campaign routing refinements |
| 027 | `agent_playbook` | Agent playbook enhancements |
| 028 | `menu_item_aliases` | CSV dish name -> canonical menu item resolution |
| 029 | `team_content` | Google Docs sync, ground notes, ad copies |
| 030 | `fix_ingest_event_audit` | Event auditing and type expansion |
| 031 | `instantly_campaign_routing_v2` | Final Instantly campaign UUIDs per lifecycle segment |
| 032 | `agent_tables` | `inference_results`, `decision_recommendations`, `orchestrator_log`, `action_queue`, `customer_goals` |
| 033 | `field_agent_sms` | `source` + `agent_name` columns on `telnyx_messages` |
| 034–053 | *(various)* | Shipday historical, field agent reviews, broadcast jobs, chatbot RAG, chatbot doc meta, chatbot canned QA, orders delivery date, orders notes, Telnyx tracking view, team content types, Shipday communications, ground team evidence, active customer campaign, campaign routing fixes, engagement rollups 30d, Instantly campaigns & stats |
| 054 | `weekly_menu_schedule` | Weekly menu items table — keyed by `(week_start, item_name)` |
| 055 | `menu_airtable_id` | Adds `airtable_record_id`, `active`, `price` columns to `weekly_menu_schedule` for Airtable sync |

## n8n Workflows (23 active)

All workflows follow `[ExternalApp — FlowType] Name` taxonomy. Credential IDs are in `n8n/config.json`.

**Implementation notes:**
- **Telnyx from number:** Hardcoded as `+18444322224` in `broadcast_dispatch` / `sms_dispatch` (n8n Variables not available on this instance)
- **Slack nodes:** Replaced with NoOp placeholders in `airtable_playbook_sync`, `daily_order_upload`, `lapsed_customer_cycle` — not yet configured
- **sms_dispatch** was recreated (old workflow ID missing); new ID is in `n8n/config.json`

| Workflow | Schedule | Purpose |
|----------|----------|---------|
| [Claude] Agent Orchestration | Every 3 h | Batch agent cycle for all active contacts |
| [Claude] Hourly Intelligence Cycle | Hourly | 5-phase: INTAKE → EVIDENCE → INFERENCE → DECISION → EXECUTION |
| [Claude] Lifecycle Cycle Runner | Hourly | SQL rule engine — stage transitions, campaign queuing |
| [Claude] Lapsed Customer Cycle | Daily (random offset) | Persistent re-engagement for lapsed customers |
| [System] Action Queue Executor | Every 30 min | Routes action_queue: Telnyx / Instantly / Airtable / Google Drive / Gmail-SMTP |
| [System] Chatbot Docs Reindex | Every Monday 2 AM | Housekeeping — refreshes chatbot document index |
| [Airtable] Outcome Sync | Every 15 min | Pull opportunity outcomes from Airtable |
| [Airtable] Playbook Sync | Every 15 min | Sync user-configured rules from Airtable |
| [Airtable] Marketing Query Form | On-demand | Self-service query form → Claude inference |
| [Telnyx] Inbound SMS Collector | Every 30 min | Ingest inbound SMS/calls, trigger real-time agent cycle |
| [Telnyx] Broadcast Dispatch | Every 5 min | Dispatch broadcasts: SMS via Telnyx, email via server |
| [Telnyx] Broadcast Form | On form submit | n8n form UI for delay alerts + promo broadcasts |
| [Shipday] Delivery Collector | Every 30 min | Poll Shipday for delivery status updates |
| [Shipday] Feedback Sync | Hourly | Poll feedback, delivery instructions, proof-of-delivery |
| [Shipday] Historical Import | Manual only | One-shot backfill of up to 1 year of order history |
| [Instantly] Campaign Performance | Hourly | Fetch Instantly analytics per campaign |
| [Instantly] Campaign Sync | Every 6 h | Sync Instantly campaigns to DB |
| [Instantly] Campaign Setup | Daily midnight | Create missing Instantly campaigns |
| [Google] Docs & Drive Sync | Every 30 min | Read Google Docs, push content to chatbot index |
| [Orders] Daily CSV Upload | Daily 1 PM EST | Process daily order CSV via API |
| [Reporting] Daily Field Brief | Daily 7:30 AM | Generate field sales call list |
| [Reporting] Daily Activity Report | Daily 8:00 AM | Claude-written HTML + CSV activity summary → Gmail |
| [Reporting] Daily Outcome Report | Daily 8:30 AM | Claude-written HTML + CSV outcome summary → Gmail |
| [Airtable] Menu Sync | Hourly | Pulls Airtable "Weekly Menu" records → upserts `weekly_menu_schedule` in Postgres (`baZV5ViA5lXNCTWR`) |

## Lifecycle Segments

8 states: `cold` -> `engaged` -> `active_customer` -> `new_customer` -> `lapsed_customer` -> `reactivation_candidate` -> `cooling` -> `optout`

Rules define transitions based on order frequency, engagement signals (opens, clicks, SMS responses), cooling periods, and channel preferences.

## Menu Item Resolution (5-step pipeline)

1. **Exact match** against `menu_items` master table
2. **Alias lookup** in `menu_item_aliases` (handles variant names)
3. **Normalized match** (case-insensitive)
4. **Fuzzy match** (SequenceMatcher, 85% threshold)
5. **Create new item** with price from CSV if truly new

## MCP Server (Claude Desktop)

30+ tools across 6 groups:

- **Contacts:** `get_contact_detail()`, `search_contacts()`
- **Analytics:** `get_lifecycle_summary()`, `get_campaign_performance()`, `get_engagement_trends()`
- **Communications:** `get_communication_history()`, `get_delivery_tracking()`
- **Recommendations:** `suggest_reactivation_targets()`, `recommend_content_strategy()`
- **Opportunities:** `detect_opportunities()`, `create_opportunity()`, `get_high_intent_signals()`
- **Agents:** `get_latest_inference()`, `get_latest_decision()`, `get_orchestrator_history()`, `get_pending_actions()`, `get_agent_cycle_summary()`

## Campaign Definitions

5 Instantly email campaigns mapped to lifecycle segments:

| Campaign | Lifecycle Segment | Purpose |
|----------|------------------|---------|
| `DW-NurtureSlow-ColdContacts` | cold | Long-term nurture |
| `DW-PromoStandard-ActiveEngaged` | engaged, active_customer | Standard promotions |
| `DW-PromoAggressive-LapsedCustomers` | lapsed_customer | Aggressive win-back |
| `DW-NewCustomerOnboarding` | new_customer | First-time buyer onboarding |
| `DW-Reactivation-LongDormant` | reactivation_candidate | Dormant customer reactivation |

## Data Seed Files

Located in `data/sql/` (46 files):

| Prefix | Files | Content |
|--------|-------|---------|
| 01 | 1 | 137 master menu items with category/is_veg/avg_price |
| 02 | 4 | ~4,000 seed contacts |
| 03 | 19 | ~20,000 historical orders |
| 04 | 2 | Order line items |
| 05 | 19 | ~50,000 event records |
| 06 | 1 | Menu item duplicate cleanup |

## Deployment

### Render (Production)

One-click deploy via `render.yaml` blueprint. Auto-runs migrations on deploy.

- **Web service:** `dabbahwala-api` (Python 3.11, Starter plan, Oregon)
- **Database:** `dabbahwala-db` (PostgreSQL 16, Starter plan, Oregon)
- **n8n:** Self-hosted at `digitalworker.dataskate.io`
- **API URL:** `https://dabbahwala-latest.onrender.com`

Build uses `set -euo pipefail` — any failure aborts the deploy. No browser install step (Playwright removed).

### Local Development

```bash
cp .env.example .env
# Fill in DATABASE_URL, ANTHROPIC_API_KEY, etc.
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Environment Variables

See `.env.example` for full list. Key variables:

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | PostgreSQL connection |
| `ANTHROPIC_API_KEY` | Claude agent pipeline |
| `TELNYX_API_KEY` | SMS/voice |
| `AIRTABLE_API_KEY` + `AIRTABLE_BASE_ID` | CRM + playbook |
| `SHIPDAY_API_KEY` | Delivery tracking |
| `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD` | Report emails (n8n also uses Gmail-SMTP credential) |
| `REPORT_EMAIL_TO` | Report email recipient (default: `core@dabbahwala.com`) |
| `ADMIN_SECRET` | Admin endpoint protection |
| `N8N_API_KEY` | Workflow automation |

## CI/CD

- **GitHub Action** (`.github/workflows/sync_n8n.yml`): Auto-syncs n8n workflow JSON files to the n8n instance on push to `main`.
