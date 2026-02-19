# DabbahWala Marketing System

Lifecycle-driven marketing orchestration platform for DabbahWala, a fresh Indian food delivery service in Atlanta. Combines rule-based automation with AI-powered (Claude) reasoning to detect conversion opportunities, route contacts through lifecycle campaigns, and orchestrate multi-channel outreach.

## Tech Stack

- **Backend:** FastAPI (Python 3.11) on Render
- **Database:** PostgreSQL 16 with `dabbahwala` schema, pgvector for RAG
- **Orchestration:** n8n (10 workflows) on self-hosted instance
- **AI Agent:** Claude Sonnet 4.5 for intelligent opportunity detection
- **Channels:** Telnyx (SMS/calls), Instantly (email campaigns), Airtable (field sales)
- **MCP Server:** Model Context Protocol for Claude Desktop integration

## System Architecture

```
  CSV Orders ──→ /daily-orders/process ──→ contacts + orders + events
  Instantly  ──→ /intelligence/ingest  ──→ campaign events
  Telnyx     ──→ /telnyx/message|call  ──→ SMS/call transcripts
  Airtable   ──→ /playbook/sync        ──→ agent playbook rules
                          │
                          ▼
              ┌─────────────────────┐
              │  INTELLIGENCE CYCLE │  (hourly via n8n)
              │  1. INTAKE          │
              │  2. EVIDENCE        │
              │  3. INFERENCE       │
              │  4. DECISION        │
              │  5. EXECUTION       │
              └─────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
    Rule Engine      Claude Agent    Opportunity Queue
    (lifecycle)      (smart detect)    │
                                      ├──→ Telnyx SMS
                                      ├──→ Instantly Email
                                      └──→ Airtable Field Sales
```

## API Endpoints

### Core Processing
| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/daily-orders/process` | Upload CSV, create contacts/orders/items, detect opportunities |
| GET | `/api/daily-orders/summary/{date}` | Order summary for a date |
| POST | `/api/events/ingest` | Ingest raw events (opens, clicks, orders) |
| POST | `/api/lifecycle/run` | Execute lifecycle rule engine |

### Intelligence & Agent
| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/intelligence/run-cycle` | Full 5-phase intelligence cycle |
| GET | `/api/intelligence/pending-actions` | Pending actions from last cycle |
| POST | `/api/intelligence/ingest-instantly-events` | Poll Instantly campaign events |
| POST | `/api/agent/analyze-contacts` | Claude-powered opportunity detection (batch) |
| POST | `/api/agent/analyze-single/{id}` | Single-contact deep analysis |

### Opportunities & Campaigns
| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/opportunities` | Create opportunity |
| GET | `/api/opportunities/pending` | Pending opportunities for dispatch |
| POST | `/api/opportunities/{id}/dispatched` | Mark dispatched |
| POST | `/api/opportunities/{id}/outcome` | Record outcome |
| GET | `/api/campaigns/pending` | Pending campaign moves |
| POST | `/api/campaigns/{id}/executed` | Mark campaign executed |

### Communications & Delivery
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/sms/pending` | Pending SMS queue |
| POST | `/api/sms/{id}/sent` | Mark SMS sent |
| POST | `/api/telnyx/message` | Ingest Telnyx SMS |
| POST | `/api/telnyx/call` | Ingest Telnyx call transcript |
| POST | `/api/delivery/status` | Update delivery status |

### Playbook & Reports
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/playbook/rules` | Get active playbook rules |
| POST | `/api/playbook/rules` | Create/update playbook rule |
| POST | `/api/playbook/sync-from-airtable` | Sync rules from Airtable |
| GET | `/api/reports/daily/{date}` | Daily performance report |
| POST | `/api/reports/daily/{date}` | Generate daily report |

### Admin
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/health` | Health check |
| POST | `/admin/migrate/{num}` | Run specific migration |
| POST | `/admin/query` | Execute read-only SQL |
| POST | `/admin/exec` | Execute write SQL |

## Database Schema (28 migrations)

| Migration | Purpose |
|-----------|---------|
| 001 | Enums: lifecycle_segment, campaign_name, event_type, delivery_status, opportunity_action |
| 002 | `contacts` table (email, phone, lifecycle_segment, channel flags, order counts) |
| 003 | `events` table (raw event intake) |
| 004 | `engagement_rollups` materialized view (7-day rolling) |
| 005 | `rules` table (predicate SQL + actions) |
| 006 | `decision_log` (audit trail of lifecycle transitions) |
| 007 | `campaign_queue` (pending campaign moves) |
| 008 | `telnyx_messages` + `telnyx_calls` (SMS/call history) |
| 009 | `refresh_engagement_rollups()` function |
| 010 | `evaluate_rules()` core rule engine function |
| 011 | `run_lifecycle_cycle()` main lifecycle function |
| 012 | Auto-update triggers for `updated_at` |
| 013 | Seed rules (first order, lapsed, reactivation thresholds) |
| 014 | `campaign_routing` table (lifecycle segment -> Instantly campaign) |
| 015 | `daily_reports` table + `generate_daily_report()` |
| 016 | `opportunities` table (CRUD for conversion actions) |
| 017 | `update_delivery_status()`, `store_telnyx_message()`, `store_telnyx_call()` |
| 018 | `create_opportunity()`, `mark_opportunity_dispatched()`, `update_opportunity_outcome()` |
| 019 | Analytics: `get_lifecycle_summary()`, `get_campaign_performance()`, `get_engagement_trends()` |
| 020 | `get_contact_detail()`, `search_contacts()`, `get_communication_history()` |
| 021 | `suggest_reactivation_targets()`, `recommend_content_strategy()`, signal detectors |
| 022 | pgvector: `content_embeddings` (1536-dim), `intent_phrases` for semantic search |
| 023 | Instantly campaign routing with campaign IDs |
| 024 | `menu_items`, `orders`, `order_items` tables for daily order ingestion |
| 025 | `sms_templates` (A/B testing) + `agent_playbook` (user-configured rules) |
| 026 | Campaign routing update |
| 027 | Agent playbook enhancements |
| 028 | `menu_item_aliases` table for CSV name -> canonical menu item resolution |

## n8n Workflows (10 total)

| Workflow | Schedule | Purpose |
|----------|----------|---------|
| Lifecycle Cycle Runner | Hourly | Run rule engine, update segments, queue campaigns |
| SMS Dispatch | Every 10 min | Pull pending SMS, send via Telnyx |
| Opportunity Dispatcher | Every 5 min | Dispatch pending opportunities to Airtable |
| Airtable Outcome Sync | Every 15 min | Sync outcomes back from Airtable |
| Opportunity Detection | Every 2 hours | Signal detection (engaged-no-order, lapsed, etc.) |
| Daily Report Generator | Daily 11 PM | Aggregate metrics for the day |
| Airtable Playbook Sync | Every 15 min | Sync user-configured rules from Airtable |
| Daily Order Upload | Daily 1 PM EST | Fetch CSV from Airtable, process orders |
| Hourly Intelligence Cycle | Hourly | Full INTAKE->EVIDENCE->INFERENCE->DECISION->EXECUTION |
| SMS Templates | On-demand | A/B test variants from playbook |

## Menu Item Validation

The daily order processor resolves CSV dish names to canonical master menu items using a 5-step pipeline:

1. **Exact match** against `menu_items` master table
2. **Alias lookup** in `menu_item_aliases` (handles "Couples Thali - Veg" -> "Couple's - Veg Thali Box")
3. **Normalized match** (case-insensitive comparison)
4. **Fuzzy match** (SequenceMatcher, 85% threshold)
5. **Create new item** with price from CSV if truly new

The API response reports `menu_items_matched` vs `menu_items_created` for monitoring.

## MCP Server (Claude Desktop Integration)

25+ tools across 5 groups:

- **Contacts:** `get_contact_detail()`, `search_contacts()`
- **Analytics:** `get_lifecycle_summary()`, `get_campaign_performance()`, `get_engagement_trends()`, `get_order_attribution()`
- **Communications:** `get_communication_history()`, `get_delivery_tracking()`
- **Recommendations:** `suggest_reactivation_targets()`, `recommend_content_strategy()`
- **Opportunities:** `detect_opportunities()`, `create_opportunity()`, `get_high_intent_signals()`

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

## Lifecycle Segments

8 states: `cold` -> `engaged` -> `active_customer` -> `new_customer` -> `lapsed_customer` -> `reactivation_candidate` -> `cooling` -> `optout`

Rules define transitions based on:
- Order frequency (first order within 3 days, 5+ orders, no order in 14 days)
- Engagement signals (opens, clicks, SMS responses)
- Cooling periods (prevent over-messaging)
- Channel preferences (email/SMS opt-in flags)

## Intelligence Cycle (5 Phases)

1. **INTAKE:** Poll Instantly events, count Telnyx SMS/calls
2. **EVIDENCE:** Refresh 7-day engagement rollups
3. **INFERENCE:** Detect 7 signal types (engaged-no-order, lapsed-reengaged, reorder-intent, app-to-direct, subscription-candidate, high-value-at-risk, new-no-repeat)
4. **DECISION:** Create opportunities, queue campaign moves
5. **EXECUTION:** Run lifecycle rules, prepare dispatch batches

## Deployment

### Render (Production)
```bash
# One-click via render.yaml blueprint
# Auto-runs migrations on deploy
```

### Local Development
```bash
cp .env.example .env
# Fill in DATABASE_URL, ANTHROPIC_API_KEY, etc.
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Environment Variables
See `.env.example` for required configuration:
- `DATABASE_URL` - PostgreSQL connection
- `ANTHROPIC_API_KEY` - Claude agent
- `TELNYX_API_KEY` + `TELNYX_FROM_NUMBER` - SMS/calls
- `AIRTABLE_API_KEY` + `AIRTABLE_BASE_ID` - Field sales + playbook
- `N8N_INSTANCE_URL` + `N8N_API_KEY` - Workflow orchestration
