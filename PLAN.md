# DabbahWala — Complete Pending Work Plan

## Status (as of 2026-02-26)

| Step | Status |
|------|--------|
| 0 — Restore Agent Playbook | ✅ DONE (committed) |
| 1 — Fix n8n crashes + deploy missing workflows | ✅ DONE (committed) |
| 2 — Terminology rename | ✅ DONE (confirmed already applied in prior session) |
| 3 — Airtable cleanup | ✅ DONE (committed) |
| 4 — Lapsed sweep 300→200 | ✅ DONE (committed) |
| 5 — Centralized credential service | 🔄 IN PROGRESS (see below) |
| 6 — Rename n8n workflows | ⏳ Pending |
| 7 — Two visible n8n test workflows | ⏳ Pending |
| 8 — Router reorganization | ⏳ Pending |
| 9 — Rewrite FEATURES.md | ⏳ Pending |
| 10 — DB cleanup + migration squash | ⏳ Pending |
| 11 — Python structured logging | ⏳ Ongoing (applied as files touched) |
| 12 — n8n workflow sticky notes | ⏳ Ongoing (applied as workflows touched) |
| 13 — Dashboard enhancements | ⏳ Pending |
| 14 — Campaign JSONs + test_data/ | ⏳ Pending |

Branch: `claude/review-context-plan-gYPY1`

---

## STEP 5 (Remaining Work) — Centralized Credential Service

### What's done
- `app/routers/config.py` created (untracked — needs commit)
- `app/main.py` updated to include config router at `/api/credentials` and `/api/internal` (unstaged — needs commit)

### What remains

#### A. Commit Python changes
- `app/routers/config.py` (new) + `app/main.py` (updated imports + router includes)

#### B. Prereq: user creates n8n credential
User must create one HTTP Header Auth credential in n8n:
- Name: `DW Admin Secret`
- Header name: `X-Admin-Secret`
- Header value: `<value of ADMIN_SECRET Render env var>`

#### C. Update n8n workflow JSONs (17 files)

All 17 workflows with `"credentials"` blocks need three changes:
1. Add a "Get Credentials" HTTP Request node immediately after the trigger
2. Wire the trigger → "Get Credentials" → (rest of workflow)
3. Replace credential references:
   - Telnyx `httpHeaderAuth` → `sendHeaders: true` with `Authorization: Bearer {{ $('Get Credentials').first().json.TELNYX_API_KEY }}`
   - Airtable `httpHeaderAuth` → header `Authorization: Bearer {{ $('Get Credentials').first().json.AIRTABLE_API_KEY }}`
   - Instantly `httpHeaderAuth` → header `Authorization: Bearer {{ $('Get Credentials').first().json.INSTANTLY_BEARER }}`
   - Shipday `httpHeaderAuth` → header `Authorization: Basic {{ $('Get Credentials').first().json.SHIPDAY_API_KEY }}`
   - Gmail SMTP node → replace with `POST /api/internal/send-email` HTTP Request
   - Google Drive OAuth node → replace with `POST /api/internal/drive/upload` HTTP Request
   - Airtable native nodes → replace with HTTP Request using dynamic Airtable API key

#### D. Get Credentials node template (inject into every workflow)
```json
{
  "id": "get-credentials",
  "name": "Get Credentials",
  "type": "n8n-nodes-base.httpRequest",
  "typeVersion": 4.2,
  "parameters": {
    "method": "GET",
    "url": "=https://dabbahwala-latest.onrender.com/api/credentials",
    "authentication": "genericCredentialType",
    "genericAuthType": "httpHeaderAuth",
    "options": {}
  },
  "credentials": {
    "httpHeaderAuth": {
      "id": "<DW Admin Secret n8n cred ID>",
      "name": "DW Admin Secret"
    }
  }
}
```

#### E. Push all 17 updated workflows to n8n live via PUT /api/v1/workflows/{id}

#### Files to update
`n8n/sms_dispatch.json`, `n8n/broadcast_dispatch.json`, `n8n/telnyx_inbound_collector.json`,
`n8n/hourly_intelligence_cycle.json`, `n8n/airtable_playbook_sync.json`, `n8n/lifecycle_cycle_cron.json`,
`n8n/instantly_campaign_sync.json`, `n8n/shipday_delivery_collector.json`,
`n8n/telnyx_sms_historical_import.json`, `n8n/action_queue_executor.json`,
`n8n/growth_agent_cycle.json`, `n8n/system_test_suite.json`, `n8n/google_docs_sync.json`,
`n8n/airtable_outcome_sync.json`, `n8n/marketing_query_form.json`,
`n8n/airtable_menu_sync.json` (if applicable), `n8n/menu_sync_weekly.json` (inactive — skip)

---

## Remaining Steps (6–14)

---

### STEP 0 — Restore Agent Playbook (Immediate — Last Session Request) [DONE]

**Why:** User deleted the Airtable "Agent Playbook" table. The n8n playbook sync workflow
reads from Airtable and POSTs to `/api/playbook/sync-from-airtable`. Without the Airtable
table, the sync crashes and agents lose their rules.

**Changes:**
- Seed the Postgres `agent_playbook` table directly via a new migration
  (`063_seed_playbook_rules.sql`) with all 19 canonical rules across 5 categories
- The 19 seeded rules cover: exclusion (3), priority (2), inference/observer (5),
  decision/advisor (4), messaging (3), general (2)
- Add the same rules back to the Airtable "Agent Playbook" table via the Airtable API
  so future syncs work again
- Update `n8n/airtable_playbook_sync.json` to gracefully handle the case where the table
  is empty (remove hard crash on missing table)

**Files:**
- `migrations/063_seed_playbook_rules.sql` (new)
- `n8n/airtable_playbook_sync.json` (fix crash handling)

---

### STEP 1 — Fix n8n Structural Issues (Part 1 — Workflows Crashing)

**Why:** Three n8n workflows are crashing due to dead node references, two workflows
were never deployed, and the Playwright scraper is redundant.

**Changes:**
A. `n8n/airtable_playbook_sync.json` — remove dead "Notify Slack" connection (line 219)
B. `n8n/lapsed_customer_cycle.json` — remove dead "Notify Slack" connection
C. Deactivate the menu Playwright scraper workflow in n8n (POST .../deactivate) + delete
   `n8n/menu_sync_weekly.json` from repo (Airtable sync is the single source of truth)
D. Deploy `competitor_agent_cycle` and `goal_agent_cycle` to n8n (currently have no IDs
   in config.json — never deployed), add their IDs to `n8n/config.json`

**Files:**
- `n8n/airtable_playbook_sync.json`
- `n8n/lapsed_customer_cycle.json`
- `n8n/menu_sync_weekly.json` (delete)
- `n8n/config.json`

---

### STEP 2 — Terminology Standardization (Part 12)

**Why:** The names "Inference" and "Decision" are used in two separate systems:
Contact Sweep phases AND AI Stack layers — causing constant confusion. The fix is to
rename the sweep phases and the AI stack layers to non-overlapping names.

**Changes:**

A. **intelligence.py** — rename 5 phase functions and docstrings:
   - `_phase_intake()` → `_phase_collect()`
   - `_phase_evidence()` → `_phase_profile()`
   - `_phase_inference()` → `_phase_signal()`
   - `_phase_decision()` → `_phase_route()`
   - `_phase_execution()` → `_phase_dispatch()`
   - Update `CycleResult` field names accordingly

B. **agents.py** — rename layer names in comments, variable names, log strings:
   - "Inference agents" → "Observer agents"
   - "Decision agents" → "Advisor agents"
   - `_PLAYBOOK_CATEGORIES` keys: `"inference"` → `"observer"`, `"decision"` → `"advisor"`
   - DB write functions: `_store_inference_results()` → `_store_observations()`
   - DB write functions: `_store_decision_recs()` → `_store_action_plan()`

C. **Migration `064_terminology_rename.sql`** (new):
   ```sql
   ALTER TABLE inference_results RENAME TO contact_observations;
   ALTER TABLE decision_recommendations RENAME TO action_plans;
   ```

D. **mcp_server/tools/agents.py** — update tool names:
   - `get_inference_results` → `get_latest_observations`
   - `get_decision_recommendations` → `get_latest_action_plan`

E. **test_harness_service.py** — update table names and test function references

F. **n8n/config.json** — rename 4 workflows:
   - Lifecycle Cycle Runner → Stage Runner
   - Hourly Intelligence Cycle → Contact Sweep
   - Agent Orchestration → AI Stack

G. Call n8n API to rename those 4 workflows live

H. **SYSTEM.md / FEATURES.md / CLAUDE.md / TESTS.md** — replace all old terminology

**Files:**
- `app/routers/intelligence.py`
- `app/routers/agents.py`
- `mcp_server/tools/agents.py`
- `app/services/test_harness_service.py`
- `migrations/064_terminology_rename.sql` (new)
- `n8n/config.json`
- `SYSTEM.md`, `FEATURES.md`, `CLAUDE.md`, `TESTS.md`

---

### STEP 3 — Airtable Cleanup (Part 2)

**Why:** Three Airtable tables (Team Content, Marketing Queries, Team Inputs) are no
longer needed. Code still writes to them. Remove all references so they can be deleted
from Airtable UI.

**Changes:**
A. `app/services/airtable_sync.py` — remove `log_query_to_airtable()` function and
   all calls to it
B. `app/routers/team_content.py` — remove any Airtable write calls (Google Docs remains)
C. `n8n/` workflows — strip any nodes that write to Team Content / Marketing Queries /
   Team Inputs tables in Airtable
D. Update `n8n/config.json` to remove references to deleted workflows
E. User manually deletes the 3 empty Airtable tables from the UI after deploy

**Files:**
- `app/services/airtable_sync.py`
- `app/routers/team_content.py`
- Affected n8n JSON files

---

### STEP 4 — Reduce Lapsed Sweep Batch Size (Part 3)

**Why:** The daily lapsed customer sweep processes 300 contacts per run — too many
Claude calls. Reduce to 200.

**Change:** One line in `app/routers/agents.py` — change batch limit constant from
300 to 200.

**Files:**
- `app/routers/agents.py`

---

### STEP 5 — Centralized Credential Service (Part 5)

**Why:** Credentials are scattered: some hardcoded in n8n JSON, some in n8n credential
store, some in Python. Centralize non-secret config AND API keys in Render env vars;
n8n fetches at runtime via a single secure HTTP endpoint. n8n credential store → 1
credential (DW Admin Secret bootstrap).

**Changes:**

A. **New endpoint `GET /api/credentials`** in a new router `app/routers/config.py`:
   - Validates `X-Admin-Secret` header against `ADMIN_SECRET` env var
   - Returns JSON: `{TELNYX_API_KEY, TELNYX_FROM_NUMBER, AIRTABLE_API_KEY,
     INSTANTLY_BEARER, REPORT_EMAIL_TO, AIRTABLE_BASE_ID, ...}`

B. **New endpoint `POST /api/internal/send-email`** (email proxy):
   - Accepts `{to, subject, body_html}` + validates `X-Admin-Secret`
   - Sends via smtplib using `GMAIL_SMTP_USER` / `GMAIL_SMTP_PASSWORD` env vars
   - n8n calls this instead of using Gmail-SMTP credential

C. **Google Drive/Docs via service account** — new `app/services/drive.py`:
   - Reads `GOOGLE_SERVICE_ACCOUNT_JSON` env var (base64-encoded)
   - Exposes upload/read functions
   - New endpoints `POST /api/internal/drive/upload` and `GET /api/internal/docs/{doc_id}`
   - n8n workflows call these instead of using Google OAuth credentials

D. **Update all affected n8n workflows** — add a "Get Credentials" HTTP node at the
   top of each workflow that calls `GET /api/credentials` with the admin secret, then
   reference `$('Get Credentials').first().json.FIELD_NAME` in all credential fields

E. **n8n credential store** — result is 1 credential: "DW Admin Secret" (HTTP Header Auth)
   containing `X-Admin-Secret: <value>`. All other credentials removed from n8n.

F. **Render env vars** to add: `ADMIN_SECRET`, `GMAIL_SMTP_USER`, `GMAIL_SMTP_PASSWORD`,
   `GOOGLE_SERVICE_ACCOUNT_JSON` (one-time setup by user)

**Files:**
- `app/routers/config.py` (new)
- `app/services/drive.py` (already exists — update it)
- All n8n workflow JSONs that use credentials
- `n8n/config.json`

---

### STEP 6 — Rename n8n Workflows to Feature Taxonomy (Part 6)

**Why:** Workflows are currently named by technology (Shipday, Telnyx, Airtable, Claude).
Rename to 12-feature taxonomy so feature scope is immediately visible in n8n UI.

**Renamed groups (30 → 29 after scraper removal):**

| Feature | Workflows |
|---------|-----------|
| [Order Intake] | Order Collector, Feedback Sync, Historical Import, Daily CSV |
| [SMS] | Inbound Collector, Dispatch Queue |
| [Broadcast] | Dispatch, Form |
| [Email Campaigns] | Performance Tracker, Campaign Sync, Bulk Seed, Campaign Setup |
| [Intelligence] | Stage Runner, Contact Sweep, AI Stack, Lapsed Re-engagement |
| [Field Agent] | Outcome Sync, Daily Brief |
| [Agent Rules] | Playbook Sync |
| [Menu] | Airtable Sync |
| [Growth] | Competitor Research, Goal Agent, Weekly Growth Agent |
| [Reports] | Daily Activity, Daily Outcome |
| [Chatbot] | Docs Sync, Docs Reindex, Query Form |
| [System] | Action Queue, Daily Tests |

**Changes:**
- Call n8n API (`PATCH /api/v1/workflows/{id}`) to rename each workflow live
- Update all workflow names in `n8n/config.json`
- Update all workflow name references in JSON files themselves

**Files:**
- `n8n/config.json`
- All `n8n/*.json` files (name field)

---

### STEP 7 — Two Visible n8n Test Workflows (Part 7)

**Why:** The current `[System] Daily Tests` workflow is a black box — it calls
`POST /api/test/run` and you can't see pass/fail per test in n8n. Replace with two
workflows where every node is independently green/red.

**Changes:**

A. **New workflow: `[System] Connectivity Check`** (`n8n/system_connectivity_check.json`):
   - "Get Credentials" node → then 6 parallel nodes, one per service:
     Telnyx ping, Airtable ping, Instantly ping, FastAPI health, Email SMTP,
     Google Drive ping
   - Each node independently succeeds/fails → visible in n8n execution view

B. **New workflow: `[System] Feature Tests`** (`n8n/system_feature_tests.json`):
   - Replaces the black-box daily runner
   - One HTTP node per test group (g1 through g9):
     `GET /api/test/run/1`, `GET /api/test/run/2`, etc.
   - Each node is independently green/red per group

C. **New endpoint `GET /api/test/run/{group_id}`** in `app/routers/test_harness.py`:
   - Routes to existing `_g1_*()`, `_g2_*()` functions in test_harness_service.py
   - Returns `{"group": N, "passed": X, "failed": Y, "results": [...]}`

D. **Deactivate and delete** old `[System] Daily Tests` workflow from n8n

**Files:**
- `n8n/system_connectivity_check.json` (new)
- `n8n/system_feature_tests.json` (new)
- `app/routers/test_harness.py`
- `n8n/config.json`

---

### STEP 8 — Router Reorganization (Part 8)

**Why:** 30 routers named by technology/data source. Merge thin/duplicate routers,
rename to feature-based grouping, delete dead files.

**Changes:**

A. **Merge duplicate menu routers** — `airtable_menu.py` + any other menu router
   → single `app/routers/menu.py`

B. **Merge Shipday routers** — `shipday_sync.py` + `shipday_historical.py`
   → single `app/routers/orders.py`

C. **Delete dead router** — `team_content.py` (after Airtable cleanup in Step 3,
   this only handles Google Docs sync; absorb into `app/routers/chatbot.py`)

D. **Rename routers to feature names:**
   - `lifecycle.py` → keep as `stage_engine.py` (Stage Engine)
   - `intelligence.py` → keep but update comments (Contact Sweep)
   - `agents.py` → keep as coordinator; extract layers:
     - `app/routers/observer_agents.py` (was inference layer)
     - `app/routers/advisor_agents.py` (was decision layer)
     - `app/routers/orchestrator.py`
     - `agents.py` becomes thin coordinator calling these
   - `telnyx.py` → `sms.py`
   - `broadcasts.py` → keep
   - `campaigns.py` → keep
   - `query.py` → `chatbot.py` (merge with chatbot router)
   - `prospects.py` → `growth.py` (merge with growth_agent.py)

E. **Extract shared `_fire_agent_cycle()` utility** — currently duplicated in at least
   2 router files → move to `app/services/agent_service.py`

F. **Update `app/main.py`** — update all router includes to new names

G. **Result: ~21 routers** (down from 30)

**Files:**
- `app/routers/` — many files renamed/merged/deleted
- `app/services/agent_service.py` (new utility)
- `app/main.py`

---

### STEP 9 — Rewrite FEATURES.md (Part 9)

**Why:** FEATURES.md currently lists features in technical order. Rewrite it to match
the 12-feature taxonomy, showing for each feature: what it does, which n8n workflows
power it, which Python routers handle it, and which test group covers it.

**Structure per feature section:**
```
## [Order Intake]
Purpose: ...
n8n: [Order Intake] Order Collector, [Order Intake] Daily CSV
Python: app/routers/orders.py
Tests: Group 2 (Shipday)
Flow: Shipday webhook → orders.py → ... → campaign_queue
```

**Files:**
- `FEATURES.md` (full rewrite)

---

### STEP 10 — Database Cleanup (Part 10)

**Why:** 67 migration files with duplicate numbers, ~6 dead tables never queried,
duplicate experiment tracking systems (goal + growth), Instantly campaign IDs
duplicated in 3 places.

**Changes:**

A. **Drop dead tables** (in new migration `065_drop_dead_tables.sql`):
   - Identify ~6 tables never referenced in Python code
   - Drop them with `DROP TABLE IF EXISTS`

B. **Consolidate Instantly campaign IDs**:
   - `campaign_routing` table is the single source of truth (Part 10 of plan, migration 062 done)
   - Remove `_CAMPAIGN_META` and `_EXISTING_CAMPAIGN_IDS` hardcoded dicts from `agents.py`
   - All campaign lookups read from `campaign_routing` table

C. **Unify experiment tables** — `goal_experiments` + `experiments` (growth) are nearly
   identical structures. Migration `066_unify_experiments.sql`:
   - Merge into single `experiments` table with `agent_type` column
   - Same for `goal_experiment_contacts` + `experiment_contacts`
   - Unify `goal_agent_runs` + `competitor_agent_runs` into `agent_runs` with `agent_type`

D. **Squash migrations** — condense 67 files into 15 clean schema files:
   - `001_core_contacts.sql`
   - `002_orders_and_delivery.sql`
   - `003_engagement_and_lifecycle.sql`
   - `004_campaigns_and_broadcast.sql`
   - `005_sms_and_communications.sql`
   - `006_ai_stack_tables.sql`
   - `007_agent_playbook.sql`
   - `008_experiments_and_growth.sql`
   - `009_chatbot_and_docs.sql`
   - `010_menu_catalog.sql`
   - `011_field_agent.sql`
   - `012_test_harness.sql`
   - `013_views_and_functions.sql`
   - `014_indexes.sql`
   - `015_seed_data.sql`
   - The squash is additive — the `IF NOT EXISTS` pattern means existing prod tables are untouched

**Files:**
- `migrations/065_drop_dead_tables.sql` (new)
- `migrations/066_unify_experiments.sql` (new)
- All 67 old migration files → 15 new squashed files
- `app/routers/agents.py` (remove _CAMPAIGN_META dict)
- `CLAUDE.md` (update next migration number)

---

### STEP 11 — Python Structured Logging (Cross-Cutting)

**Why:** Discussed in context.md — every Python file should have `debug`, `info`, and
`error` level logging so failures are diagnosable in Render logs without adding print
statements.

**Standard pattern to apply to every router and service:**
```python
import logging
logger = logging.getLogger(__name__)

# At function entry (debug):
logger.debug("run_contact_sweep called contact_id=%s", contact_id)

# On successful completion (info):
logger.info("contact_sweep complete contact_id=%s phase=%s actions=%d", ...)

# On caught errors (error with exc_info):
logger.error("contact_sweep failed contact_id=%s error=%s", contact_id, e, exc_info=True)
```

**Changes:**
A. Add `logging.basicConfig` config to `app/main.py` with format:
   `%(asctime)s %(levelname)s %(name)s %(message)s` at level `INFO`
   (override to `DEBUG` via `LOG_LEVEL` env var)

B. Add `logger = logging.getLogger(__name__)` + entry/exit/error log calls to every
   router and service file being modified in Steps 0–10. Do NOT bulk-add to files
   not otherwise changed — apply as each file is touched.

C. Key files that need logging urgently (high failure rate, currently silent):
   - `app/routers/intelligence.py` — log each phase start/end + contact count
   - `app/routers/agents.py` — log each layer start, contact ID, layer result
   - `app/services/airtable_sync.py` — log sync counts and any API errors
   - `app/routers/playbook.py` — log rule counts on sync
   - `app/routers/telnyx.py` / `sms.py` — log every inbound/outbound SMS

**Files:**
- `app/main.py`
- All routers and services touched in Steps 0–10

---

### STEP 12 — n8n Workflow Notes / Documentation (Cross-Cutting)

**Why:** Discussed in context.md — every n8n workflow should have a "sticky note" node
(or node-level notes) explaining: what the workflow is, its purpose, when it triggers,
and what happens next in the chain.

**Standard note structure for each workflow:**
```
NAME: [Feature] Workflow Name
PURPOSE: One sentence — what this workflow does.
TRIGGER: How/when it starts (cron schedule, webhook, manual, called by another workflow).
INPUT: What data it receives (if webhook/sub-workflow).
OUTPUT / NEXT: What it produces and what happens after (enqueues action, calls API, notifies).
CREDENTIALS USED: Which services it calls (fetched via /api/credentials).
```

**Changes:**
A. Add a "Sticky Note" node (type `n8n-nodes-base.stickyNote`) to the start of every
   workflow JSON with the above structure filled in — done as each workflow is updated
   in Steps 1, 5, 6, and 7.

B. Add short `notes` strings to individual nodes that perform non-obvious operations
   (e.g., a Filter node with complex conditions, a Code node with business logic).

C. Priority workflows to document immediately (highest operational confusion):
   - `airtable_playbook_sync.json` — sync chain confusing
   - `action_queue_executor.json` — multi-action fan-out is complex
   - `agent_orchestration_cron.json` → `[Intelligence] AI Stack` — most complex
   - `lapsed_customer_cycle.json` — multi-step re-engagement chain
   - `intelligence_cycle.json` → `[Intelligence] Contact Sweep` — 5-phase loop

**Files:**
- All `n8n/*.json` files (sticky note added as each file is touched in Steps 1–7)

---

### STEP 13 — Dashboard Chatbot Enhancements

**Why:** Discussed in context.md — the admin dashboard chatbot should be reorganized
around features (the 12-feature taxonomy), surface the 3 engine tiles, show the
customer lifecycle journey, and support caching/vector search for technical questions.

**What was done already (previous session):**
- ✅ Feature tiles added (12 feature groups)
- ✅ 3 engine tiles (Stage Engine, Contact Sweep, AI Stack)
- ✅ Lifecycle journey panel

**What still needs to be done:**

A. **Feature tile → chat integration** — clicking a tile should pre-fill a question
   about that feature (e.g., clicking "Order Intake" asks "How does order intake work?").
   Verify `askTile()` JS function is wired up correctly after the dashboard session edits.

B. **Technical question caching** — for repeated questions like "what tables exist?" or
   "explain the stage engine", store the answer in `dashboard_chat_cache` table:
   - New migration `067_dashboard_chat_cache.sql`:
     `(id, question_hash TEXT UNIQUE, answer TEXT, created_at)`
   - Before calling Claude, check cache by hashing the question (MD5)
   - Cache TTL: 24 hours (skip cache if `created_at < now() - interval '24 hours'`)
   - New endpoint `POST /api/chat/ask` that handles the cache check + Claude call

C. **Vector search for docs questions** — questions about chatbot-indexed Google Docs
   content should query the existing `chatbot_chunks` (or equivalent) table using
   pgvector similarity search instead of sending all doc content to Claude:
   - Embed the question using a small embedding model
   - Retrieve top-5 relevant chunks
   - Pass only those chunks as context to Claude
   - Reduces token cost for doc-heavy questions

D. **Lifecycle journey visualization** — the customer lifecycle section should show
   the actual segment counts from `contacts` table live (a simple API call:
   `GET /api/contacts/lifecycle-summary` returning counts per lifecycle_segment).
   Currently shows static labels.

E. **Stage Engine tile** — clicking it should explain the SQL rules that govern stage
   transitions. Since these rules are hardcoded in the stored procedure, extract them
   into a readable JSON or YAML file (`config/stage_rules.yml`) so the dashboard can
   display them and they can be updated without a full deploy.

**Files:**
- `app/static/dashboard.html`
- `app/routers/chat.py` (update or new)
- `app/routers/contacts.py` (add lifecycle-summary endpoint)
- `migrations/067_dashboard_chat_cache.sql` (new)
- `config/stage_rules.yml` (new)

---

### STEP 14 — Campaign JSONs and Test Data Organization (Part 11)

**Why:** Campaign JSON files are split between `campaigns/` and `data/campaigns/`.
Test fixture data is inline in Python. Consolidate both.

**Changes:**

A. **Campaigns folder** — merge all campaign JSON files into `campaigns/`:
   - Move everything from `data/campaigns/` into `campaigns/`
   - Single `campaigns/instantly_campaigns.json` already exists — consolidate any others
   - Update any code references to old paths

B. **Test data folder** — create `test_data/` with fixture files by feature:
   - `test_data/contacts.json` — sample contacts for test harness
   - `test_data/orders.json` — sample order payloads
   - `test_data/sms.json` — sample SMS payloads
   - `test_data/campaigns.json` — test campaign IDs
   - Update `test_harness_service.py` to load fixtures from these files instead of
     having them inline

**Files:**
- `campaigns/` (reorganized)
- `test_data/` (new directory + files)
- `app/services/test_harness_service.py`

---

## Execution Order Summary

| Step | What | Risk | Dependencies |
|------|------|------|-------------|
| 0 | Restore playbook + seed rules | Low | None |
| 1 | Fix n8n crashes + deploy missing workflows | Low | None |
| 2 | Terminology rename (phases + layers + DB) | Medium | Steps 0+1 done |
| 3 | Airtable cleanup | Low | None |
| 4 | Lapsed sweep 300→200 | Very Low | None |
| 5 | Centralized credentials | High | Steps 1+2 done |
| 6 | Rename n8n workflows | Low | Step 5 done |
| 7 | Visible test workflows | Medium | Steps 5+6 done |
| 8 | Router reorganization | High | Step 2 done |
| 9 | Rewrite FEATURES.md | Low | Steps 6+8 done |
| 10 | DB cleanup + migration squash | High | Steps 2+3 done |
| 11 | Python logging (applied as each file is touched) | Low | Ongoing |
| 12 | n8n workflow notes (applied as each workflow is touched) | Low | Ongoing |
| 13 | Dashboard enhancements (cache, vector, lifecycle live data) | Medium | Step 10 done |
| 14 | Campaign JSONs + test_data/ | Low | Step 10 done |

---

## Deferred

**Part 4 — OTP Abandoned Session:** Parked until msg91 integration details are received
from the website team. Once the OTP verification pattern is confirmed (webhook vs.
frontend relay vs. backend verify), the implementation will be:
- New `website_sessions` table (migration 067)
- POST /api/auth/website-visitor endpoint
- Shipday webhook marks session as converted
- POST /api/intelligence/check-abandoned-sessions (runs every 30 min via n8n)

---

## Critical Files to Modify

| File | Steps |
|------|-------|
| `app/routers/intelligence.py` | 2 |
| `app/routers/agents.py` | 2, 4, 8, 10 |
| `app/services/airtable_sync.py` | 3 |
| `app/routers/playbook.py` | 0 |
| `app/main.py` | 8 |
| `n8n/config.json` | 0, 1, 2, 6, 7 |
| `n8n/airtable_playbook_sync.json` | 0, 1 |
| `n8n/lapsed_customer_cycle.json` | 1 |
| `migrations/063_seed_playbook_rules.sql` | 0 (new) |
| `migrations/064_terminology_rename.sql` | 2 (new) |
| `CLAUDE.md` | 2, 10 |
| `SYSTEM.md` | 2, 9 |
| `FEATURES.md` | 9 |

---

## Verification

After all steps:
1. `POST /api/playbook/sync-from-airtable` succeeds with 19 rules
2. `GET /api/playbook/rules/for-prompt` returns rules with observer/advisor category names
3. n8n airtable_playbook_sync workflow runs without Slack crash
4. n8n lapsed_customer_cycle workflow runs without Slack crash
5. n8n competitor_agent_cycle and goal_agent_cycle are active and visible in n8n UI
6. `GET /api/credentials` with correct X-Admin-Secret returns all keys
7. SMS dispatch test sends successfully via Telnyx
8. Email test sends via FastAPI email proxy
9. All n8n workflows show feature-group names in UI
10. [System] Connectivity Check shows 6 green nodes
11. [System] Feature Tests shows per-group pass/fail
12. `GET /api/test/run/1` returns group results
13. `app/main.py` includes all renamed routers without import errors
14. Migration squash: prod DB tables all present with new names after 064 runs
15. `SELECT COUNT(*) FROM contact_observations` returns rows (was inference_results)
