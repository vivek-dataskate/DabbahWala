# DabbahWala — Business Features

Each section describes a business-critical capability and the technical assets that deliver it.

> **Navigation:** [README](README.md) · [System Reference](SYSTEM.md) · [Claude Instructions](CLAUDE.md)

---

## 1. Customer Lifecycle Management

Contacts are automatically classified into 8 stages and moved between them based on order frequency, engagement signals, and time-based rules — without manual intervention.

**Stages:** `cold` → `engaged` → `new_customer` → `active_customer` → `cooling` → `lapsed_customer` → `reactivation_candidate` → `optout`

**Assets**

| Asset | Role |
|-------|------|
| `contacts.lifecycle_segment` | Current stage stored on every contact row |
| `rules` table | Predicate SQL + action pairs defining when to transition |
| `campaign_routing` table | Maps each lifecycle stage to an Instantly email campaign |
| `decision_log` table | Audit trail of every stage transition |
| `run_lifecycle_cycle()` stored proc | Main rule engine — evaluates predicates, transitions segments, queues campaigns |
| `evaluate_rules()` stored proc | Core rule evaluation loop called by the above |
| `routers/lifecycle.py` | `POST /api/lifecycle/run` endpoint |
| `[Claude] Lifecycle Cycle Runner` n8n | Fires `POST /api/lifecycle/run` every hour |
| `[Airtable] Playbook Sync` n8n | Syncs user-configured rules into `agent_playbook` every 15 min |

---

## 2. AI Agent Pipeline

Per-contact AI reasoning using 9 sequential Claude calls (Menu + 3 Inference + 4 Decision + Orchestrator) that produce one concrete outreach action. Runs in real-time after inbound events and in a daily dormant sweep at 9 AM.

**Flow:** Menu (Haiku) → Inference ×3 (Haiku/Sonnet) → Decision ×4 (Haiku/Sonnet) → Orchestrator (Sonnet) → `action_queue`

**Model routing:** Haiku for fast classification (Menu, Sentiment, Engagement, Stage, Channel); Sonnet for complex reasoning (Intent, Offer, Escalation, Orchestrator). Prompt caching (`cache_control: ephemeral`) on all system prompts gives a 90% token discount from contact #2 onward.

**Playbook RAG:** Category filtering routes only relevant rule categories to each agent layer (inference/decision/messaging/exclusion). Hash-based in-memory cache avoids DB round-trips when playbook is unchanged.

**Assets**

| Asset | Role |
|-------|------|
| `routers/agents.py` | Full 4-layer pipeline implementation |
| `customer_goals` table | One active goal per contact (`convert_to_order` / `retain` / `reactivate`) |
| `inference_results` table | Layer 1 outputs — sentiment, intent, engagement |
| `decision_recommendations` table | Layer 2 outputs — stage, channel, offer, escalation |
| `orchestrator_log` table | Layer 3 chosen action, full reasoning, guardrails applied |
| `action_queue` table | Pending → executing → done/failed lifecycle for each action |
| `agent_playbook` table | User-configured rules injected into Claude system prompts (synced from Airtable) |
| `[Claude] Agent Orchestration` n8n | Daily sweep at 9 AM — dormant contacts not run in 72 h (cap 200) |
| `[Telnyx] Inbound SMS Collector` n8n | Triggers real-time cycle per contact after inbound SMS/call |

**Delivery guardrails (Layer 3 overrides):**
- `delivered` → thank-you SMS with reorder nudge (24 h cooldown)
- `delivery_failed` / `delivery_returned` → high-urgency Airtable escalation
- `out_for_delivery` / `driver_assigned` → no action (order in flight)

---

## 3. Multi-Channel Marketing Execution

Actions decided by the agent pipeline are dispatched to the correct channel (SMS, email, or field sales) via the `action_queue` mechanism.

**Assets**

| Asset | Role |
|-------|------|
| `action_queue` table | Staging table for all approved outreach actions |
| `[System] Action Queue Executor` n8n | Polls queue every 30 min; routes to Telnyx / Instantly / Airtable / Google Drive / Gmail-SMTP |
| `[Telnyx] Broadcast Dispatch` n8n | Dispatches broadcasts every 5 min — SMS via Telnyx, email via Gmail-SMTP |
| `[Telnyx] Broadcast Form` n8n | Web form for the team to send manual delay alerts or promo blasts |
| Telnyx API | SMS sending from `+18444322224` |
| Instantly API | Campaign-based email delivery (5 lifecycle-mapped campaigns) |
| Airtable API | Field sales task creation for human escalations |
| `routers/sms.py` | `GET /api/sms/pending`, `POST /api/sms/{id}/sent` |
| `routers/campaigns.py` | `GET /api/campaigns/pending`, `POST /api/campaigns/{id}/executed` |

**5 Instantly campaigns:**

| Campaign | Target Segment |
|----------|---------------|
| `DW-NurtureSlow-ColdContacts` | cold |
| `DW-PromoStandard-ActiveEngaged` | engaged, active_customer |
| `DW-NewCustomerOnboarding` | new_customer |
| `DW-PromoAggressive-LapsedCustomers` | lapsed_customer |
| `DW-Reactivation-LongDormant` | reactivation_candidate |

---

## 4. Daily Order Processing

A CSV file of the day's orders is uploaded every afternoon and automatically creates/updates contacts, records orders and menu items, detects opportunities, and triggers agent cycles.

**Assets**

| Asset | Role |
|-------|------|
| `routers/daily_orders.py` | `POST /api/daily-orders/process` — 5-step CSV pipeline |
| `orders` table | Order records (ref, date, amount, slot, type) |
| `order_items` table | Line items per order |
| `menu_items` table | Master menu catalog (137 items) |
| `menu_item_aliases` table | CSV dish name → canonical menu item resolution |
| `[Orders] Daily CSV Upload` n8n | Uploads daily CSV at 1 PM EST (Mon–Sat) |

**5-step menu item resolution:** exact match → alias lookup → normalized match → fuzzy match (85% threshold) → create new item

**Post-upload triggers:** lifecycle run → opportunity detection → agent cycle for new/returning contacts

---

## 5. Delivery-Aware Intelligence

Real-time delivery status from Shipday is ingested and used by the AI orchestrator to override standard outreach logic — e.g., thank customers immediately after delivery, escalate instantly on failure.

**Assets**

| Asset | Role |
|-------|------|
| Shipday API | Source of delivery status, driver location, ETA |
| `delivery_status` table | Delivery event log per contact and order |
| `update_delivery_status()` stored proc | Delivery event processing with contact linkage |
| `routers/delivery.py` | `POST /api/delivery/status` |
| `[Shipday] Delivery Collector` n8n | Polls Shipday every 30 min |
| `[Shipday] Feedback Sync` n8n | Polls feedback, delivery instructions, proof-of-delivery hourly |
| `[Shipday] Historical Import` n8n | Manual one-shot backfill (intentionally inactive) |
| Layer 3 orchestrator guardrails | Reads latest delivery event; overrides standard action logic |

---

## 6. Marketing Intelligence Cycle

A 5-phase rule-based engine runs once daily (7:00 AM) to detect signals across all contacts and generate opportunities for the action queue — without waiting for a specific inbound event. Poll window is 24 hours to match the daily cadence.

**7 signal types detected:**

| Signal | Detection Logic |
|--------|----------------|
| `engaged_no_order` | 3+ opens/clicks in 7 days, no order in 7 days |
| `new_customer_no_repeat` | Exactly 1 order, 5+ days since first, no repeat |
| `lapsed_reengaged` | Lapsed segment + recent SMS reply or email click |
| `reorder_intent` | Call transcript contains reorder keywords |
| `app_customers_for_conversion` | Orders via app, never ordered direct |
| `subscription_candidates` | 3+ one-time orders in 30 days, regular cadence |
| `high_value_at_risk` | 5+ total orders, no order in 14+ days |

**Assets**

| Asset | Role |
|-------|------|
| `routers/intelligence.py` | 5-phase cycle: INTAKE → EVIDENCE → INFERENCE → DECISION → EXECUTION |
| `opportunities` table | One row per detected opportunity (`pending` → `dispatched` → `outcome_recorded`) |
| `engagement_rollups` table | 7-day/30-day rolling metrics per contact |
| `refresh_engagement_rollups()` stored proc | Recalculates rollups from raw events |
| `create_opportunity()` stored proc | Creates opportunity with deduplication |
| `routers/opportunities.py` | CRUD + detection endpoints |
| `[Claude] Daily Intelligence Cycle` n8n | Fires `POST /api/intelligence/run-cycle` daily at 7:00 AM |
| `[Instantly] Campaign Performance` n8n | Ingests Instantly email events into DB hourly |

---

## 7. Menu Management

The weekly menu is maintained by staff in Airtable and automatically synced to Postgres hourly. Claude uses the live menu to personalise every outreach message — anchoring to favourites, bridging to items the customer has never tried.

**Assets**

| Asset | Role |
|-------|------|
| Airtable "Weekly Menu" table | Staff-editable source of truth (Name, Category, Is Veg, Price, Active) |
| `weekly_menu_schedule` table | Postgres mirror, keyed by `(week_start, item_name)` |
| `routers/airtable_menu.py` | CRUD endpoints — `GET/POST/PUT/DELETE /api/menu/items`, `POST /api/menu/sync` |
| `routers/menu_sync.py` | `GET /api/menu/current` used by the agent pipeline to load this week's menu |
| `[Airtable] Menu Sync` n8n | Pulls all Airtable records → upserts Postgres daily at 6:30 AM (ID: `baZV5ViA5lXNCTWR`) |
| `current_menu` field | Injected into every Claude contact profile — full this-week menu |
| `new_to_customer_this_week` field | Items from `current_menu` that the customer has never ordered; used to spark curiosity |

---

## 8. Self-Service Marketing Queries

The marketing team can ask instant data questions via a web form without writing SQL. Queries route to either fast SQL (free) or a Claude-powered free-form analysis.

**Assets**

| Asset | Role |
|-------|------|
| `routers/query.py` | `POST /api/query` — routes to 10 SQL categories or 1 Claude category |
| `[Airtable] Marketing Query Form` n8n | Web form at `digitalworker.dataskate.io/form/marketing-query-form`; logs results to Airtable |

**10 Tier-1 SQL categories** (instant, free): `customer_lookup`, `pipeline_snapshot`, `campaign_performance`, `who_to_contact`, `daily_summary`, `order_analytics`, `communication_history`, `ground_team_notes`, `ad_copies`, `submit_input`

**1 Tier-2 Claude category** (`free_form`, ~$0.02/query): Claude receives lifecycle distribution, order stats, top dishes, recent transitions, playbook rules, and team content — responds with actionable insights.

### AskMe Dashboard (RAG Chatbot)

A documentation-grounded Q&A assistant embedded in the dashboard. Answers questions about system design, business processes, agent pipeline, and n8n workflows — not live data lookups (those go to the Query tab).

**Answer flow:**
1. **Fast path 1 — exact chip cache** (`chatbot_canned_qa`): 25 pre-generated answers for common questions. Returned instantly, zero API cost.
2. **Fast path 2 — semantic similarity cache** (`chatbot_interactions` + pg_trgm GiST index): If a past answered question has trigram similarity ≥ 0.72, return its cached answer. Zero API cost.
3. **RAG → Haiku** (cache miss only): Retrieve top-8 relevant chunks from `chatbot_doc_chunks` via PostgreSQL FTS + up to 3 keyword-matched past Q&A pairs → call `claude-haiku-4-5-20251001` (max 1500 tokens) → save to `chatbot_interactions`.

**Autocomplete:** `GET /api/chatbot/suggest?q=...` searches past interactions + chip questions with `ILIKE` as the user types.

**Doc indexing:** All project text files are chunked (900 chars, 120 overlap) into `chatbot_doc_chunks`. A SHA-256 hash of all file contents is stored in `chatbot_doc_meta`; chips are only re-generated when the hash changes (saves API cost on quiet weeks).

**Cost:** ~$0.0011/question (Haiku). ~$0.028 per chip rebuild (25 chips × Haiku). ~98% of questions served from cache once the system warms up.

**Assets**

| Asset | Role |
|-------|------|
| `routers/chatbot.py` | `POST /api/chatbot/ask`, `GET /suggest`, `GET /history`, `POST /reindex` |
| `chatbot_doc_chunks` table | FTS-indexed project file chunks (900-char, overlap 120) |
| `chatbot_interactions` table | Every answered Q&A pair; GiST trigram index for similarity cache |
| `chatbot_canned_qa` table | Pre-generated answers for 25 chip questions |
| `chatbot_doc_meta` table | Key/value store: `last_indexed_at`, `docs_hash` |
| `[System] Chatbot Docs Reindex` n8n | Weekly Monday 2 AM — triggers `POST /api/chatbot/reindex` |

---

## 9. Growth Hacker Agent

A weekly experiment loop where Claude invents novel marketing hypotheses, launches them against a test cohort, measures conversion 7 days later, and feeds learnings back into the next experiment.

**Experiment types:** `timing` (unusual send windows), `offer` (free add-ons, credits), `message_angle` (scarcity, nostalgia, social proof), `channel_sequence` (SMS → email follow-up)

**Assets**

| Asset | Role |
|-------|------|
| `routers/growth_agent.py` | `POST /api/growth/run-cycle`, `/measure`, `/baseline/update`; `GET /experiments`, `/insights` |
| `experiments` table | One row per experiment: hypothesis, type, channel, cohort size, results |
| `experiment_contacts` table | Which contacts are in each experiment + order outcome |
| `growth_baseline` table | Historical 7-day baseline conversion rates for comparison |
| `[Claude] Growth Agent Cycle` n8n | Runs every Monday 7:30 AM: measure prior → design new → dispatch → email report |

---

## 10. Daily Reporting

Two Claude-written email reports land in the team inbox each morning — an operational summary of what the system did, and an outcome summary of what converted.

**Assets**

| Asset | Role |
|-------|------|
| `routers/agents.py` `/report/activity` | Queries last 24 h of agent runs, actions, escalations; Claude writes HTML + CSV |
| `routers/agents.py` `/report/outcome` | Queries orders, opens, goal achievements; Claude writes HTML + CSV |
| `[Reporting] Daily Activity Report` n8n | Fires at 8:00 AM daily |
| `[Reporting] Daily Outcome Report` n8n | Fires at 8:30 AM daily |
| `[Reporting] Daily Field Brief` n8n | Fires at 7:30 AM — generates field sales call list |
| Gmail-SMTP n8n credential (`Sk6XzPNPnJTXHEbr`) | Delivers report emails to `REPORT_EMAIL_TO` (default `core@dabbahwala.com`) |

---

## 11. Team Empowerment

Ground team observations, social media ad copies, and user-configured agent rules flow into Claude's decision-making — so institutional knowledge shapes every outreach message.

**Assets**

| Asset | Role |
|-------|------|
| `team_content` table | Stores ground notes, ad copies, Google Doc content |
| `routers/team_content.py` | `POST /sync` (Google Docs), `POST /submit` (form), `GET /browse`, `POST /search` |
| `[Google] Docs & Drive Sync` n8n | Polls Drive folder every 30 min; classifies docs as `ground_note` or `ad_copy` |
| Google Drive folder `1O0ES9uiDL6AWf9QMMYiyRUWGtymDjPF5` | Source of team documents |
| `agent_playbook` table | 6 rule categories: exclusion, priority, inference, decision, messaging, general |
| `routers/playbook.py` | CRUD + Airtable sync for playbook rules |
| `[Airtable] Playbook Sync` n8n | Syncs rules from Airtable every 15 min |
| Layer 1–3 system prompts | Inject active playbook rules before every Claude call |

---

## 12. Field Sales Management

High-intent contacts and failed deliveries are escalated as tasks in Airtable for the field team. Outcomes recorded in Airtable close the feedback loop back into the system.

**Assets**

| Asset | Role |
|-------|------|
| `opportunities` table | Pending → dispatched → outcome_recorded lifecycle |
| `routers/opportunities.py` | Detect, create, dispatch, and outcome endpoints |
| `create_opportunity()` stored proc | Dedup-safe opportunity creation |
| Airtable "Field Sales Tasks" base | Where field agents see and update their task queue |
| `[Airtable] Outcome Sync` n8n | Polls Airtable every 15 min; posts outcomes back via `POST /api/opportunities/{id}/outcome` |
| `routers/telnyx.py` `/field-agent-message` | Logs SMS sent by field agents from personal phones |

---

## 14. Daily E2E Test Harness

Every system, agent, integration, and automation workflow is automatically validated at 5:00 AM daily. A single HTTP call triggers 55+ end-to-end tests across 14 logical groups. Results are emailed to `vivek@dabbahwala.com` with per-test pass/fail detail. Zero real-customer impact: all test data uses `source='test_harness'` and is cascade-deleted at the end of every run.

**Test Contact:** phone `+18444322224` (Telnyx self-loop), email `vivek@dabbahwala.com`

**Test Groups**

| Group | What Is Tested |
|-------|---------------|
| 1 — System Connectivity | DB connection, `/health`, Telnyx API, Instantly API, Airtable API, Shipday API, Anthropic API, n8n API |
| 2 — Database Schema | All core + agent pipeline tables, stored functions, campaign_routing seed, n8n workflow count |
| 3 — Test Contact Setup | Create isolated contact; verify DB round-trip |
| 4 — Events & Webhooks | `ingest_event` (SMS, email_open); Telnyx inbound webhook; Shipday DELIVERED + FAILED webhooks |
| 5 — Telnyx / SMS | Real outbound SMS self-loop; `telnyx_messages` DB check; action_queue SMS flow |
| 6 — AI Agent Pipeline | 4-layer Claude cycle on test contact → verify inference_results, decision_recommendations, orchestrator_log, action_queue |
| 7 — Intelligence & Lifecycle | `POST /api/lifecycle/run` (SQL rules); `POST /api/intelligence/run-cycle` (all 5 phases); segment distribution |
| 8 — Instantly Email | All 5 DW campaigns exist; add `vivek@` as lead → verify → fetch analytics → remove |
| 9 — Airtable | Weekly Menu fetch; menu sync; playbook sync; Field Sales Task create + delete |
| 10 — Action Queue | Pending endpoint; create test entry; mark done |
| 11 — Order Processing | CSV upload; menu_items populated; order summary endpoint |
| 12 — Reports | Activity report (Claude); outcome report (Claude); `/api/reports/daily` endpoint |
| 13 — Self-Service & Chatbot | Query categories; tier-1 `pipeline_snapshot`; customer_lookup; chatbot RAG ask; opportunities/detect |
| 14 — Cleanup | Cascade-delete all test records; verify zero remaining |

**Assets**

| Asset | Role |
|-------|------|
| `migrations/056_test_harness.sql` | `test_runs` table — persists every run with full JSONB results |
| `app/services/test_harness_service.py` | All 55+ test functions; `run_full_suite()` entry point; cleanup logic |
| `app/routers/test_harness.py` | `POST /api/test/run`, `GET /api/test/results`, `GET /api/test/results/{run_id}` |
| `n8n/system_test_suite.json` | Daily 5 AM trigger; parse results; send pass/fail email via Gmail-SMTP |
| n8n workflow ID `M7bwNMGrUMRvAHH4` | Live workflow — active in n8n |

---

## 13. Claude Desktop MCP

Marketing and ops team members can query live Postgres data conversationally in Claude Desktop without writing SQL — using 30+ purpose-built tools.

**Assets**

| Asset | Role |
|-------|------|
| `mcp_server/server.py` | FastMCP app; registers all tool groups |
| `mcp_server/tools/contacts.py` | `get_contact_detail()`, `search_contacts()` |
| `mcp_server/tools/analytics.py` | `get_lifecycle_summary()`, `get_campaign_performance()`, `get_engagement_trends()` |
| `mcp_server/tools/communications.py` | `get_communication_history()`, delivery tracking |
| `mcp_server/tools/recommendations.py` | `suggest_reactivation_targets()`, `recommend_content_strategy()` |
| `mcp_server/tools/opportunities.py` | `detect_opportunities()`, `create_opportunity()`, `get_high_intent_signals()` |
| `mcp_server/tools/agents.py` | `get_latest_inference()`, `get_latest_decision()`, `get_orchestrator_history()`, `get_pending_actions()`, `get_agent_cycle_summary()` |
| `mcp_server/tools/instantly.py` | `instantly_list_campaigns()`, `instantly_get_campaign_analytics()`, `instantly_list_leads()`, `instantly_get_email_events()` |
| `mcp_server/tools/shipday.py` | `get_shipday_order()`, `list_shipday_orders()`, `get_shipday_carriers()`, `get_shipday_order_tracking()` |
