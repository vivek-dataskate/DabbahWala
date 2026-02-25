# DabbahWala — E2E Test Registry

This file is the **canonical reference** for all end-to-end tests in the DabbahWala test harness.

**Rule:** Every code commit that adds or modifies a feature must:
1. Add the corresponding test(s) to `app/services/test_harness_service.py` in the appropriate group
2. Register those tests here in the correct group section below

Test runner: `POST /api/test/run` · Results: `GET /api/test/results`
Source file: `app/services/test_harness_service.py`

---

## Group 1 — System Connectivity (`1_connectivity`)

Verifies the app and all external services are reachable with valid credentials.

| Test Name | What It Checks |
|-----------|----------------|
| `db_connection` | PostgreSQL connection via `get_cursor` |
| `api_health` | `GET /health` returns `{status: ok}` |
| `telnyx_api` | Telnyx API key valid — `GET /v2/messaging_profiles` |
| `instantly_api` | Instantly API key valid — `GET /campaign/list` |
| `airtable_api` | Airtable API key valid — `GET /Weekly Menu?maxRecords=1` |
| `shipday_api` | Shipday API key valid — `GET /orders` |
| `anthropic_api` | Anthropic API key valid — sends minimal `claude-haiku` message |
| `n8n_api` | n8n API key valid — `GET /api/v1/workflows?limit=1` |

---

## Group 2 — Database Schema (`2_schema`)

Validates that all required tables, stored functions, and seed data are present.

| Test Name | What It Checks |
|-----------|----------------|
| `core_tables_exist` | All 10 core tables present in `dabbahwala` schema |
| `agent_tables_exist` | All 5 agent pipeline tables present |
| `stored_functions_exist` | 5 required stored functions exist |
| `campaign_routing_seeded` | `campaign_routing` table has ≥5 rows |
| `n8n_workflow_count` | n8n instance has ≥22 workflows |

**Core tables checked:** `contacts`, `events`, `orders`, `order_items`, `telnyx_messages`, `telnyx_calls`, `delivery_status`, `engagement_rollups`, `menu_items`, `opportunities`

**Agent tables checked:** `customer_goals`, `inference_results`, `decision_recommendations`, `orchestrator_log`, `action_queue`

**Stored functions checked:** `ingest_event`, `run_lifecycle_cycle`, `refresh_engagement_rollups`, `store_telnyx_message`, `update_delivery_status`

---

## Group 3 — Test Contact Setup (`3_contact_setup`)

Creates an isolated test contact used throughout all subsequent groups.

| Test Name | What It Checks |
|-----------|----------------|
| `cleanup_stale_contact` | Removes leftover test contacts from prior failed runs |
| `create_test_contact` | Inserts contact with `source='test_harness'`, phone `+18444322224` |
| `verify_test_contact` | Confirms contact is in DB with correct phone and source |

---

## Group 4 — Event & Webhook Ingestion (`4_events_webhooks`)

Tests all inbound event and webhook ingestion endpoints.

| Test Name | What It Checks |
|-----------|----------------|
| `ingest_sms_event` | `POST /api/events/ingest` with `sms_received` type |
| `ingest_email_open_event` | `POST /api/events/ingest` with `email_open` type |
| `telnyx_inbound_webhook` | `POST /api/telnyx/message` simulates inbound SMS |
| `shipday_webhook_delivered` | `POST /api/delivery/status` with `delivered` status |
| `shipday_webhook_failed` | `POST /api/delivery/status` with `delivery_failed` status |
| `delivery_events_in_db` | Confirms ≥2 `delivery_status` rows for test contact |

---

## Group 5 — Telnyx / SMS (`5_telnyx_sms`)

Tests real outbound SMS sending and message queue mechanics.

| Test Name | What It Checks |
|-----------|----------------|
| `telnyx_send_sms` | Sends real SMS from/to `+18444322224` (self-loop) via Telnyx API |
| `telnyx_messages_in_db` | Confirms `telnyx_messages` row exists for test contact |
| `sms_action_queue_flow` | Inserts `send_sms` action into queue, marks it done via API |

---

## Group 6 — AI Agent Pipeline (`6_agent_pipeline`)

Runs the full 4-layer Claude agent cycle and verifies all pipeline tables are written to.

| Test Name | What It Checks |
|-----------|----------------|
| `agent_goal_create` | Inserts `customer_goals` row for test contact |
| `agent_cycle_run` | `POST /api/agents/cycle/run-for-contact` completes successfully |
| `agent_inference_results` | `inference_results` rows written for test contact |
| `agent_decision_results` | `decision_recommendations` rows written |
| `agent_orchestrator_log` | `orchestrator_log` entry created |
| `agent_action_queued` | `GET /api/agents/action-queue/pending` returns valid response |

---

## Group 7 — Intelligence & Lifecycle (`7_intelligence_lifecycle`)

Tests the 5-phase intelligence cycle and lifecycle segment assignment.

| Test Name | What It Checks |
|-----------|----------------|
| `lifecycle_run` | `POST /api/lifecycle/run` completes, returns counts |
| `intelligence_cycle_run` | `POST /api/intelligence/run-cycle` runs all 5 phases |
| `intelligence_all_phases` | All 5 phases present in response: `intake`, `evidence`, `inference`, `decisions`, `execution` |
| `lifecycle_segment_distribution` | `contacts.lifecycle_segment` populated for ≥1 segment |
| `lead_status_transitions` | `decision_log` has entries in last 24h |

---

## Group 8 — Instantly / Email Campaigns (`8_instantly_email`)

Tests Instantly email campaign operations end-to-end.

| Test Name | What It Checks |
|-----------|----------------|
| `instantly_campaigns_list` | `GET /campaign/list` returns campaigns |
| `instantly_all_5_campaigns` | All 5 DW campaign names present in Instantly |
| `instantly_lead_add` | Adds test email to `DW-NurtureSlow-ColdContacts` campaign |
| `instantly_lead_verify` | Confirms test lead exists in campaign |
| `instantly_analytics` | `GET /analytics/campaign/summary` returns data |
| `instantly_campaign_sync_endpoint` | `POST /api/webhooks/sync-campaigns` accepts payload |
| `instantly_lead_remove` | Removes test email from campaign (cleanup) |

**Expected campaigns:** `DW-NurtureSlow-ColdContacts`, `DW-PromoStandard-ActiveEngaged`, `DW-NewCustomerOnboarding`, `DW-PromoAggressive-LapsedCustomers`, `DW-Reactivation-LongDormant`

---

## Group 9 — Airtable Integration (`9_airtable`)

Tests Airtable menu sync, playbook sync, and field sales task creation.

| Test Name | What It Checks |
|-----------|----------------|
| `airtable_menu_fetch` | `GET /Weekly Menu?maxRecords=5` returns records |
| `airtable_menu_sync` | `POST /api/menu/sync` syncs Airtable → DB |
| `airtable_playbook_sync` | `POST /api/playbook/sync-from-airtable` succeeds |
| `airtable_field_task_lifecycle` | Creates a field sales task in Airtable |
| `airtable_field_task_delete` | Deletes the test task (cleanup) |

---

## Group 10 — Action Queue (`10_action_queue`)

Verifies the action queue API and DB mechanics.

| Test Name | What It Checks |
|-----------|----------------|
| `action_queue_pending_endpoint` | `GET /api/agents/action-queue/pending` returns 200 |
| `action_queue_create_entry` | Inserts `send_email_report` action into `action_queue` |
| `action_queue_mark_done` | `POST /api/agents/action-queue/{id}/done` returns 200 |

---

## Group 11 — Order Processing (`11_order_processing`)

Tests CSV order ingestion and menu resolution.

| Test Name | What It Checks |
|-----------|----------------|
| `order_csv_process` | Uploads test CSV to `POST /api/daily-orders/process` |
| `menu_items_present` | `menu_items` table has >0 rows (Airtable sync worked) |
| `order_summary_endpoint` | `GET /api/daily-orders/summary/{today}` returns 200 or 404 |

---

## Group 12 — Reports (`12_reports`)

Tests Claude-generated activity and outcome reports.

| Test Name | What It Checks |
|-----------|----------------|
| `activity_report_generate` | `POST /api/agents/report/activity` returns HTML body |
| `outcome_report_generate` | `POST /api/agents/report/outcome` returns HTML body |
| `reports_daily_endpoint` | `GET /api/reports/daily/{today}` returns 200 or 404 |
| `report_data_endpoints` | `GET /api/agents/report/activity-data` and `outcome-data` return 200 |

---

## Group 13 — Self-Service & Chatbot (`13_query_chatbot`)

Tests the query engine and RAG chatbot.

| Test Name | What It Checks |
|-----------|----------------|
| `query_categories` | `GET /api/query/categories` returns ≥5 categories |
| `query_tier1_pipeline_snapshot` | `POST /api/query` with `pipeline_snapshot` returns answer |
| `query_tier1_customer_lookup` | `POST /api/query` with `customer_lookup` finds test contact |
| `chatbot_ask` | `POST /api/chatbot/ask` returns Claude-generated answer |
| `opportunities_detect` | `GET /api/opportunities/detect` returns 200 |

---

## Group 14 — Data Cleanup (`14_cleanup`)

Always runs last, even if earlier groups fail. Removes all test data.

| Test Name | What It Checks |
|-----------|----------------|
| `cleanup_test_contacts_db` | Cascade-deletes all contacts with `source='test_harness'` |
| `verify_test_data_removed` | Confirms 0 test contacts remain in `contacts` table |

---

## Adding New Tests

When committing code that adds or changes a feature, add a test in the **same commit**:

1. **Pick the right group** — use the table above. If nothing fits, create a new group in `test_harness_service.py` and add it here.
2. **Add the test function** inside the appropriate `_gN_*` function in `app/services/test_harness_service.py`.
3. **Register it here** — add a row to the correct group table above with the test name and what it checks.
4. **If adding a new group** — add the `_gN_*` function call in `run_full_suite()` and create a new section in this file.

### Group Selection Guide

| What changed | Group |
|---|---|
| New external API integration | 1 — System Connectivity |
| New DB table or stored function | 2 — Database Schema |
| New event type or webhook endpoint | 4 — Event & Webhook Ingestion |
| New SMS sending path | 5 — Telnyx / SMS |
| New agent layer or Claude prompt | 6 — AI Agent Pipeline |
| New lifecycle rule or intelligence phase | 7 — Intelligence & Lifecycle |
| New Instantly campaign or email action | 8 — Instantly / Email |
| New Airtable table or sync endpoint | 9 — Airtable |
| New action type in `action_queue` | 10 — Action Queue |
| New order/menu endpoint | 11 — Order Processing |
| New report type | 12 — Reports |
| New query category or chatbot feature | 13 — Self-Service & Chatbot |
