# DabbahWala — Operator's Guide

How to use the system day-to-day. Every customer journey, system function, and feature explained from an operator's perspective — what to do, where to do it, and what to watch for.

> **Navigation:** [README](README.md) · [Technical Reference](SYSTEM.md) · [Tests](TESTS.md)

---

## Table of Contents

1. [Daily Operator Checklist](#1-daily-operator-checklist)
2. [Customer Journey Functions](#2-customer-journey-functions)
   - 2A. [Cold Lead → First Order (Prospect Conversion)](#2a-cold-lead--first-order-prospect-conversion)
   - 2B. [First Order → Repeat Buyer (Onboarding Retention)](#2b-first-order--repeat-buyer-onboarding-retention)
   - 2C. [Active Customer Retention (Keeping Regulars)](#2c-active-customer-retention-keeping-regulars)
   - 2D. [App Customer → Direct Order (Platform Conversion)](#2d-app-customer--direct-order-platform-conversion)
   - 2E. [Lapsed Customer Re-engagement (14–29 Days Dormant)](#2e-lapsed-customer-re-engagement-1429-days-dormant)
   - 2F. [Dormant Win-Back (30+ Days Gone)](#2f-dormant-win-back-30-days-gone)
   - 2G. [Delivery Failure → Relationship Recovery](#2g-delivery-failure--relationship-recovery)
   - 2H. [High-Intent Escalation → Field Sales](#2h-high-intent-escalation--field-sales)
3. [System Functions](#3-system-functions)
   - 3A. [Adding Contacts](#3a-adding-contacts)
   - 3B. [Configuring Agent Behaviour (Playbook)](#3b-configuring-agent-behaviour-playbook)
   - 3C. [Sending a Broadcast](#3c-sending-a-broadcast)
   - 3D. [Checking System Health](#3d-checking-system-health)
   - 3E. [Running Tests](#3e-running-tests)
   - 3F. [Adjusting Workflow Schedules](#3f-adjusting-workflow-schedules)
   - 3G. [Running a Manual AI Agent Cycle](#3g-running-a-manual-ai-agent-cycle)
   - 3H. [Triggering a Manual Intelligence Sweep](#3h-triggering-a-manual-intelligence-sweep)
   - 3I. [Querying the System (Marketing Questions)](#3i-querying-the-system-marketing-questions)
   - 3J. [Using Claude Desktop (MCP)](#3j-using-claude-desktop-mcp)
4. [Feature How-Tos](#4-feature-how-tos)
   - 4A. [Order Intake](#4a-order-intake)
   - 4B. [SMS Outreach](#4b-sms-outreach)
   - 4C. [Broadcast Campaigns](#4c-broadcast-campaigns)
   - 4D. [Email Campaigns (Instantly)](#4d-email-campaigns-instantly)
   - 4E. [Field Agent Operations](#4e-field-agent-operations)
   - 4F. [Menu Management](#4f-menu-management)
   - 4G. [Agent Playbook Rules](#4g-agent-playbook-rules)
   - 4H. [Growth Experiments](#4h-growth-experiments)
   - 4I. [Reports](#4i-reports)
   - 4J. [Chatbot (Team Q&A)](#4j-chatbot-team-qa)
5. [Troubleshooting](#5-troubleshooting)

---

## 1. Daily Operator Checklist

What to review every morning before starting the day:

| Time | What arrives automatically | Where to review |
|------|--------------------------|----------------|
| 7:30 AM | Field agent daily brief — top 10 contacts to call today | Email to `core@dabbahwala.com` + Airtable Field Sales Tasks |
| 8:00 AM | Activity report — AI cycles run, SMS/emails sent, actions queued overnight | Email to `core@dabbahwala.com` |
| 8:30 AM | Outcome report — orders placed, conversions, email opens, field agent scorecard | Email to `core@dabbahwala.com` |

**Airtable:** Check [Field Sales Tasks] for new escalations added overnight by the AI Stack. Claim and call any `hot` priority tasks first.

**Dashboard:** `https://dabbahwala-latest.onrender.com/dashboard` → shows lifecycle distribution (how many contacts are in each segment) and recent agent activity.

**If anything looks wrong:** Run a connectivity check manually from n8n → `[System] Connectivity Check` → click Execute. Each node lights green/red for each service.

---

## 2. Customer Journey Functions

Each function runs automatically. This section tells you what the system is doing, how to influence it, and what to do when a contact needs hands-on help.

---

### 2A. Cold Lead → First Order (Prospect Conversion)

**What the system does automatically:**
1. New contact imported → assigned to `DW-NurtureSlow-ColdContacts` Instantly email campaign
2. First email open detected → promoted to `engaged` → moved to `DW-PromoStandard-ActiveEngaged`
3. After 3+ opens with no order → SMS sent with menu highlight
4. If they reply to any SMS → real-time AI cycle fires, follow-up sent within minutes
5. First order placed → goal marked achieved, onboarding starts

**Your job:**
- Import new contacts regularly (see §3A)
- Ensure Instantly campaigns have fresh email copy (see §4D)
- If a promising lead is stuck for 7+ days: go to Airtable Field Sales Tasks → create a manual task for a personal call

**How to manually push a contact through:**
1. Open the dashboard → find the contact → note their `lifecycle_segment`
2. Call `POST /api/agents/cycle/run-for-contact` with `contact_id` to force an immediate AI cycle
3. Or: update their `priority_override` to `high` via `PATCH /api/contacts/{id}/priority` — the AI Stack will treat them as urgent on the next batch cycle

**Success signal:** `convert_to_order` goal row in `customer_goals` transitions `converted = true`

**Watch for:** Contact stuck on `cold` for 14+ days with no email opens → verify email address is valid and the Instantly campaign is active

---

### 2B. First Order → Repeat Buyer (Onboarding Retention)

**What the system does automatically:**
1. First order confirmed → contact moved to `new_customer` → `DW-NewCustomerOnboarding` campaign starts
2. Delivery confirmed (Shipday status = COMPLETED) → 4-hour delay → thank-you SMS sent with reorder nudge
3. Day 5, no second order → Intelligence Engine detects `new_customer_no_repeat` → SMS sent with subscription pitch
4. Channel rotates (email, then SMS, then field call) if no response

**Your job:**
- Monitor the `DW-NewCustomerOnboarding` Instantly campaign — the first 7 days after a first order are the highest-leverage window
- Check Airtable for any `new_customer_no_repeat` field sales tasks — a personal call in week 1 converts at much higher rates than an automated SMS

**How to personalise the onboarding message:**
- Open Airtable → Agent Playbook → add a `messaging` category rule: *"For new customers, always reference the item they ordered in the first thank-you message"*
- The AI Offer Agent reads playbook rules on every cycle and will incorporate this instruction automatically

**Success signal:** Second order placed within 14 days → contact promoted to `active_customer`

**Watch for:** Thank-you SMS not sent after delivery → check Shipday is polling (n8n `[Order Intake] Order Collector` should be green) and that the `delivery_status` table has the `delivered` event

---

### 2C. Active Customer Retention (Keeping Regulars)

**What the system does automatically:**
1. Every delivery → 4-hour delay → thank-you SMS + reorder nudge
2. After 3+ one-time orders → `subscription_candidates` signal fires → subscription pitch SMS sent
3. Contact silent for 14+ days (with 5+ total orders) → `high_value_at_risk` signal fires → field sales call created in Airtable (hot priority, confidence 0.88)
4. AI Stack cycles every 3 hours — maintains loyalty-focused messaging tone and references order history

**Your job:**
- When Airtable shows a `high_value_at_risk` escalation: call the customer within the same day — this is your best customer showing first signs of drift
- Weekly: check the Outcome Report (8:30 AM email) → field agent scorecard shows how many at-risk customers were successfully retained last week

**How to increase loyalty messaging intensity:**
- Airtable → Agent Playbook → add `messaging` rule: *"For active_customer segment, always include a loyalty note such as 'You're one of our best customers — here's something special for you'"*

**How to manually add a subscription:**
- Contact the customer directly and record the outcome in Airtable Field Sales Tasks → the `[Field Agent] Outcome Sync` workflow will update the contact record

**Success signal:** `subscription_type` field on the contact row is populated; no `lapsed_customer` transitions for this contact

**Watch for:** High-value customer's last order drifts past 14 days without a field sales task appearing in Airtable → check that the `high_value_at_risk` signal query is running (verify Intelligence Engine cycle ran in the last 2 hours via `/api/intelligence/run-cycle` response)

---

### 2D. App Customer → Direct Order (Platform Conversion)

**What the system does automatically:**
1. Contact's `primary_source` is tagged as a food delivery app (Uber Eats, DoorDash, etc.)
2. Intelligence Engine detects `app_customers_for_conversion` weekly → SMS sent + campaign moved to direct ordering campaign
3. Message angle: positions direct ordering as cheaper, faster, subscription-eligible

**Your job:**
- When importing contacts from third-party platforms, tag their `primary_source` correctly so the signal fires
- Review weekly if this cohort is converting: run `POST /api/query` with category `sms_performance` filtered to this segment

**How to tag a contact's source:**
- CSV upload: include `primary_source` column → set to `uber_eats`, `doordash`, or `grubhub`
- Single contact: `POST /api/prospects/add` with `primary_source` field

**How to run the conversion push manually:**
- `POST /api/intelligence/run-cycle` → the SIGNAL phase will detect all app customers due for conversion and create opportunity rows

**Success signal:** Contact places an order via direct website (order_type != `app`) within 30 days of the signal firing

**Watch for:** Signal never firing — check that contacts have `primary_source` set to an app value; the signal query filters on this field

---

### 2E. Lapsed Customer Re-engagement (14–29 Days Dormant)

**What the system does automatically:**
1. Last order 14–29 days ago → Stage Engine moves contact to `lapsed_customer` → enrolled in `DW-PromoAggressive-LapsedCustomers` Instantly campaign
2. AI Stack escalates offer angle progressively: discount → urgency → personal reference
3. Any engagement signal (email click, SMS reply) → `lapsed_reengaged` fires → hot field sales call created in Airtable (confidence 0.90)
4. After 3+ no-answers → AI Stack Escalation Agent recommends field sales intervention

**Your job:**
- This is the highest-ROI window for human outreach
- When Airtable shows a `lapsed_reengaged` task: **call same day** — the customer just showed interest; the window is short
- Review the `DW-PromoAggressive-LapsedCustomers` Instantly campaign weekly — the copy should be more direct and urgent than the standard campaign

**How to see all current lapsed customers:**
- Dashboard → lifecycle distribution chart shows count of `lapsed_customer` contacts
- Or: `POST /api/query` → category: `activity_report` → shows segment breakdown

**How to run a targeted re-engagement sweep:**
- `POST /api/agents/cycle/run-daily-sweep` triggers the AI Stack for all contacts not run in 72+ hours — catches lapsed contacts that haven't received an AI cycle recently

**Success signal:** Contact re-orders → Stage Engine moves them back to `new_customer` or `active_customer`; `lapsed_reengaged` opportunity row updated with `outcome = ordered`

**Watch for:** `lapsed_customer` count growing week-over-week without any field sales tasks being created → check the Intelligence Engine cycle is running hourly and the `lapsed_reengaged` signal query is detecting SMS/email activity correctly

---

### 2F. Dormant Win-Back (30+ Days Gone)

**What the system does automatically:**
1. Last order 30+ days ago → Stage Engine moves to `reactivation_candidate` → enrolled in `DW-Reactivation-LongDormant` Instantly campaign
2. Daily lapsed sweep (`[Intelligence] Lapsed Re-engagement` n8n workflow) runs with a random time offset so messages arrive on different days each week
3. AI Stack rotates channels continuously: SMS → email → field call → back to SMS (never stops unless `optout` or `do_not_contact`)
4. If call transcript contains reorder keywords ("same as last time", "order again") → `reorder_intent` signal fires immediately → hot SMS sent

**Your job:**
- For contacts that have been dormant 60+ days: check Airtable for escalation tasks and try a personal voice note or offer from the owner
- The AI Escalation Agent generates creative intervention ideas for stuck contacts (6+ touches, no conversion) — read these in the `orchestrator_log` table via MCP or the dashboard

**How to find contacts stuck in reactivation for 60+ days:**
```
POST /api/query
{
  "category": "activity_report",
  "date_from": "2025-12-01",
  "date_to": "today"
}
```
Or via Claude Desktop MCP: *"Show me contacts in reactivation_candidate segment with last order more than 60 days ago"*

**How to manually mark a contact as do-not-contact:**
- `PATCH /api/contacts/{id}/priority` with `priority_override: "do_not_contact"` — the Orchestrator will output `none` for this contact on every cycle, no exceptions

**Success signal:** Contact re-orders; Stage Engine resets their journey to `new_customer`

**Watch for:** `reactivation_candidate` count growing rapidly → either the retention and lapsed re-engagement functions aren't catching people early enough, or the win-back campaign copy needs refreshing

---

### 2G. Delivery Failure → Relationship Recovery

**What the system does automatically:**
1. Shipday reports `FAILED` or `RETURNED` delivery → `delivery_failed` event logged within 30 minutes
2. AI Stack Orchestrator fires delivery guardrail immediately — **overrides all other recommendations**
3. `escalate_airtable` action created with `urgency = high` — Airtable task appears within minutes
4. All promotional SMS/email for this contact is blocked until the outcome is recorded

**Your job:**
- Check Airtable every morning for `high urgency` delivery failure tasks — these must be called **same day**
- Script for the call: apologise, offer a resolution (refund, free next delivery, replacement), record what was agreed
- Record the outcome in Airtable → `[Field Agent] Outcome Sync` will update the contact record and unblock outreach

**Recovery call script:**
1. *"Hi [name], this is [your name] from DabbahWala — I'm calling personally about your delivery on [date]"*
2. *"I can see it didn't go right and I'm really sorry about that"*
3. Offer from the options: full refund / credit / free next order / personal delivery
4. If they're willing to try again: offer to personally handle their next order

**Success signal:** Outcome recorded as `recovered` or `ordered`; Airtable task closed; AI Stack resumes normal outreach on next cycle

**Watch for:** Delivery failure with no Airtable task appearing → check Shipday status webhook is configured (`POST /api/webhooks/shipday`) and the `[System] Action Queue` workflow is running

---

### 2H. High-Intent Escalation → Field Sales

**What the system does automatically:**
1. AI Stack Escalation Agent triggers when:
   - Customer has `ready_to_order` intent but 3+ automated touches haven't converted
   - Sentiment is very negative
   - 6+ total automated touches with no conversion
   - Delivery failure (see §2G)
2. Airtable task created with specific creative instructions from the AI — not just "call this person" but *"try a voice note from the owner"* or *"offer to handle next order personally"*
3. `[Field Agent] Daily Brief` compiles and emails the top 10 contacts to call each morning at 7:30 AM

**Your job:**
- Work through the Airtable Field Sales Tasks queue in priority order: `hot` first, then `warm`
- Read the AI's suggested approach in the task notes — it's specific to this customer's history
- Record every outcome in Airtable (ordered / not_interested / no_answer / declined) — this feeds back into AI reasoning for that contact's next cycle

**Outcome recording matters because:**
- `ordered` → Offer Agent continues using the same approach that worked
- `2+ declined` → Offer Agent switches to a different message angle automatically
- `3+ no_answer` → Escalation Agent raises urgency further
- `6+ touches, no conversion` → Escalation Agent generates creative alternatives

**How to add notes that inform the AI:**
- When recording an outcome in Airtable, write free-text in the `outcome_notes` field — "Customer said they're trying another service but open to coming back in March" — the AI reads this on the next cycle

**Success signal:** Outcome recorded; contact re-orders; goal marked `converted` or `retained`

---

## 3. System Functions

---

### 3A. Adding Contacts

**Bulk import (CSV):**
1. Download the template: `GET /api/prospects/template`
2. Fill in: `first_name`, `last_name`, `email`, `phone`, `address`, `primary_source` (optional)
3. Upload: `POST /api/prospects/upload-csv` (multipart form)
4. The system automatically: assigns `lifecycle_segment = cold`, creates a `convert_to_order` goal, enrolls in the cold nurture Instantly campaign

**Single contact:**
```
POST /api/prospects/add
{
  "first_name": "Priya",
  "last_name": "Sharma",
  "email": "priya@example.com",
  "phone": "+14045551234",
  "source": "referral"
}
```

**Bulk update existing contacts (e.g. update addresses after a delivery route change):**
1. Download update template: `GET /api/prospects/update-template`
2. Fill in changes; match by `email` or `phone`
3. Upload: `POST /api/prospects/update-csv`
   - Updatable fields: `first_name`, `last_name`, `address`, `priority_override`, `sales_notes`

**Dashboard:** Admin tab → Contacts → Import

---

### 3B. Configuring Agent Behaviour (Playbook)

The Agent Playbook is how you configure the AI without touching code. Every rule is injected into every Claude call at inference time.

**Where to manage rules:** Airtable → Agent Playbook table
**When changes take effect:** Within 15 minutes (Playbook Sync workflow runs every 15 min)

**Rule structure:**

| Field | What to fill in |
|-------|----------------|
| Rule Name | Short identifier, e.g. `no_friday_sms` |
| Category | `exclusion`, `priority`, `observer`, `advisor`, `messaging`, `general` |
| Instruction | Plain English rule, e.g. *"Never send SMS on Fridays after 3 PM"* |
| Priority | 1 (highest) to 100 (lowest) — higher priority rules are evaluated first |
| Active | Check to enable; uncheck to suspend without deleting |

**Category guide:**

| Category | Use it to... | Example |
|----------|-------------|---------|
| `exclusion` | Block actions completely — highest precedence | *"Never contact contacts tagged 'corporate account' without approval"* |
| `priority` | Bias AI toward certain contacts or approaches | *"Prioritise contacts who placed 2+ orders above all cold leads"* |
| `observer` | Influence how the AI classifies intent/sentiment | *"If SMS mentions 'catering', always classify intent as ready_to_order"* |
| `advisor` | Direct the choice of channel, offer, or stage | *"For reactivation contacts, always use SMS — never email-only"* |
| `messaging` | Control copy style, tone, or content | *"All messages must end with our phone number +1-404-XXX-XXXX"* |
| `general` | Anything that doesn't fit above | *"Mention the Thursday special in any Thursday outreach"* |

**How to test a new rule without full deployment:**
- Set Active = No initially
- Run `POST /api/agents/cycle/run-for-contact` for a test contact
- Review `orchestrator_log` via the dashboard to see the reasoning
- If the reasoning looks right, set Active = Yes in Airtable

---

### 3C. Sending a Broadcast

A broadcast is a one-time message to a defined audience — separate from the automated AI outreach.

**Use cases:** Delay alerts ("kitchen delayed today — 30 extra minutes"), promo announcements, new menu launches

**How to send:**
```
POST /api/broadcasts
{
  "message": "Fresh Biryani special today only — order by 1 PM for lunch delivery!",
  "channel": "sms",
  "audience_segment": "active_customer",
  "scheduled_at": null
}
```

- `channel`: `sms` or `email`
- `audience_segment`: any lifecycle segment, or `all` for all non-optout contacts
- `scheduled_at`: ISO timestamp for future sending; null = send immediately

**The broadcast is dispatched:** `[Broadcast] Dispatch` n8n workflow runs every hour and sends all pending broadcast recipients

**Track delivery:** Airtable → Broadcast Recipients table, or `GET /api/broadcasts/{id}` shows sent/failed counts

**Warning:** Broadcasts bypass the AI Stack's per-contact frequency controls. Do not send more than one broadcast per week to the same segment — high frequency causes optouts.

---

### 3D. Checking System Health

**Quick health check:**
- `GET https://dabbahwala-latest.onrender.com/health` → returns `{"status": "ok"}` if DB is reachable

**Full connectivity check (all 6 external services):**
1. Go to n8n → `[System] Connectivity Check`
2. Click Execute Workflow
3. Each node lights green (reachable) or red (unreachable)
4. Services checked: FastAPI, Telnyx, Airtable, Instantly, Shipday, Google

**Dashboard health indicators:**
- `https://dabbahwala-latest.onrender.com/dashboard`
- Shows: lifecycle segment distribution, recent action queue counts, last intelligence cycle timestamp

**If a service is red in connectivity check:**

| Service | Fix |
|---------|-----|
| FastAPI | Check Render dashboard — may be sleeping (free tier) or failed deploy |
| Telnyx | Verify `TELNYX_API_KEY` in Render env vars |
| Airtable | Verify `AIRTABLE_API_KEY` in Render env vars |
| Instantly | Verify `INSTANTLY_API_KEY` in Render env vars |
| Shipday | Verify `SHIPDAY_API_KEY` in Render env vars |
| Google | n8n credential `LUu1v42BgnEflv6f` may need OAuth re-authorisation |

---

### 3E. Running Tests

**Automated daily tests:** `[System] Feature Tests` runs every morning at 5 AM. Results emailed to `core@dabbahwala.com`. Each test group (G1–G14) is a separate node in n8n — green = pass, red = fail.

**Manual full test suite:**
```
POST /api/test/run
```
Returns a `run_id`. Poll `GET /api/test/results/{run_id}` for status.

**Run a single group:**
```
GET /api/test/run/{group_id}
```
e.g. `GET /api/test/run/g6` runs only the agent pipeline tests

**Test group reference:**

| Group | What it tests |
|-------|--------------|
| G1 | Connectivity — DB + all 5 external services |
| G2 | Schema — every table and column present |
| G3 | Contact setup — create a test contact + order |
| G4 | Events — event ingestion works |
| G5 | Telnyx SMS — send and receive test SMS |
| G6 | Agent Pipeline — Observer → Advisor → Orchestrator completes |
| G7 | Intelligence — all 5 Contact Sweep phases run |
| G8 | Instantly — campaign operations work |
| G9 | Airtable — playbook rules exist and field tasks sync |
| G10 | Action Queue — queue + dispatch cycle works |
| G11 | Orders — Shipday ingestion works |
| G12 | Reports — daily reports generate successfully |
| G13 | Chatbot — query answering works |
| G14 | Cleanup — all test data deleted |

**If G6 (Agent Pipeline) fails:**
- Check `ANTHROPIC_API_KEY` is valid in Render env vars
- Check the model name in `app/services/` matches the current Claude model

---

### 3F. Adjusting Workflow Schedules

Some workflows may need schedule changes (e.g. shifting the daily report from 8 AM to 9 AM for a different timezone).

**Via dashboard:**
1. `https://dabbahwala-latest.onrender.com/dashboard` → Admin tab (⚙️)
2. Find the workflow in the schedule table
3. Click Edit → adjust the time → Save
4. Change takes effect on the next run

**Via API:**
```
POST /api/admin/schedules/{workflow_id}
{
  "field": "trigger_at_hour",
  "trigger_at_hour": 9,
  "trigger_at_minute": 0
}
```

**What not to change:**
- `[SMS] Dispatch Queue` — keep at 10 min; increasing delays customer replies
- `[Intelligence] Contact Sweep` — keep hourly; longer gaps means missed opportunity windows
- `[System] Action Queue` — keep at 30 min; this dispatches all queued actions

---

### 3G. Running a Manual AI Agent Cycle

**For a single contact** (e.g. right after a phone call to make the AI aware of new context):
```
POST /api/agents/cycle/run-for-contact
{
  "contact_id": 123
}
```
Returns the Orchestrator's chosen action (send_sms / move_campaign / escalate_airtable / none) and full reasoning chain.

**For all eligible contacts** (e.g. after importing a large new contact list):
```
POST /api/agents/cycle/run-all
```
Runs the full 8-call pipeline for every contact with an active goal. Cap: 200 contacts per run.

**For lapsed contacts specifically:**
```
POST /api/agents/cycle/run-all-lapsed
```
Targets all `lapsed_customer` and `reactivation_candidate` contacts not run in 72+ hours.

**To read what the AI decided and why:**
- Dashboard → contact page → AI History tab
- Or via MCP: *"Show me the orchestrator history for contact [email]"*

---

### 3H. Triggering a Manual Intelligence Sweep

The Intelligence Engine runs hourly automatically, but you can trigger it manually after a bulk import or after updating a large batch of contacts.

**Full 5-phase sweep:**
```
POST /api/intelligence/run-cycle
```
Returns a summary: how many contacts were scanned, how many signals detected, how many opportunities created, lifecycle segment distribution.

**Lifecycle stage engine only** (faster — just SQL rules, no signal detection):
```
POST /api/lifecycle/run
```
Use this after uploading a CSV of orders to immediately move contacts to the right lifecycle stages.

**Detect pending opportunities without running the full cycle:**
```
GET /api/opportunities/detect
```
Returns all contacts currently matching any of the 7 signal types, without creating opportunity rows.

---

### 3I. Querying the System (Marketing Questions)

The query interface answers 14 categories of business questions in plain language. No SQL needed.

**Endpoint:** `POST /api/query`

**Available categories:**

| Category | What it answers |
|----------|----------------|
| `lifecycle_overview` | How many contacts are in each stage right now |
| `sms_performance` | Delivery rates, reply rates, opt-outs for SMS |
| `email_performance` | Opens, clicks, replies by Instantly campaign |
| `activity_report` | What the system did in a date range (actions, SMS, cycles run) |
| `outcome_report` | Orders placed, conversions, goal achievements in a date range |
| `top_contacts` | Highest-value contacts by order count/value |
| `at_risk_contacts` | Contacts showing drift signals |
| `reactivation_targets` | Best candidates for win-back outreach |
| `campaign_performance` | Per-campaign stats (opens, clicks, unsubscribes) |
| `order_trends` | Order volume, peak days, top menu items |
| `field_agent_scorecard` | Call outcomes per field agent |
| `signal_summary` | How many contacts matched each of the 7 signal types |
| `ai_stack_summary` | AI cycle activity — actions chosen, channels used |
| `menu_performance` | Which menu items appear most in orders and AI recommendations |

**Example query:**
```
POST /api/query
{
  "category": "at_risk_contacts",
  "date_from": "2025-01-01",
  "date_to": "2025-01-31",
  "limit": 20
}
```

**For conversational questions:** Use the Chatbot (§4J) or Claude Desktop (§3J)

---

### 3J. Using Claude Desktop (MCP)

The MCP server connects Claude Desktop directly to your Postgres database. Use it for ad-hoc analysis, looking up specific contacts, and pulling business intelligence without writing SQL.

**Setup (one-time):** Add to `~/.claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "dabbahwala": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/path/to/DabbahWala",
      "env": {
        "DATABASE_URL": "<your-postgres-url>"
      }
    }
  }
}
```

**What you can ask Claude Desktop:**

| Question type | Example |
|--------------|---------|
| Contact lookup | *"Find the contact history for priya@gmail.com"* |
| Lifecycle snapshot | *"How many contacts are in each lifecycle stage?"* |
| Campaign stats | *"What's the open rate for the reactivation campaign this month?"* |
| Reactivation candidates | *"Show me the top 20 contacts most likely to reactivate"* |
| AI pipeline review | *"What has the AI decided for contact ID 456 in the last 7 days?"* |
| Pending actions | *"What actions are currently queued and waiting to be dispatched?"* |
| Opportunity review | *"Which contacts have a hot priority opportunity pending right now?"* |
| Order trends | *"What were the most ordered items last week?"* |

**Available tool groups:** contacts, analytics, communications, recommendations, opportunities, agents, shipday, instantly

---

## 4. Feature How-Tos

---

### 4A. Order Intake

**How orders enter the system:**

| Source | How | Frequency |
|--------|-----|----------|
| Shipday delivery platform | `[Order Intake] Order Collector` polls API | Every 30 min |
| Daily CSV file | Placed in Google Drive → `[Order Intake] Daily CSV Upload` processes it | Daily 1 PM EST |
| Manual/test | `POST /api/shipday/ingest-orders` directly | On demand |

**After an order is ingested:**
- Contact upserted in `contacts` table
- `order_placed` event logged
- Lifecycle Engine runs → stage updated
- AI Stack triggered → thank-you SMS queued after delivery confirmation

**If an order is missing:** Check the Shipday dashboard first — if the order exists there, trigger a manual sync: `POST /api/shipday/ingest-orders` with a date range. If it's in the daily CSV but wasn't processed: verify the CSV is in the correct Google Drive folder and the column headers match the template.

**Backfilling historical orders:** `[Order Intake] Historical Import` workflow — manual trigger only. Imports up to 1 year of Shipday history. Run this once after initial setup or after a gap in syncing.

---

### 4B. SMS Outreach

**Inbound SMS** (customers texting you):
- Collected every 30 min via `[SMS] Inbound Collector` polling Telnyx MDR
- Also collected in real-time if the Telnyx webhook is configured: `POST /api/webhooks/telnyx`
- Stored in `telnyx_messages` table (direction = inbound)
- Every inbound SMS triggers an immediate AI agent cycle for that contact

**Outbound SMS** (system texting customers):
- Written to `action_queue` by the AI Stack or Intelligence Engine
- Dispatched every 10 min by `[SMS] Dispatch Queue` → Telnyx Messages API
- From number: `+18444322224`

**Guardrails enforced automatically:**
- Max 3 SMS per week per contact
- Never same channel twice in 24 hours
- `optout` and `do_not_contact` contacts are never messaged

**Manually send an SMS to a contact:**
```
POST /api/telnyx/message
{
  "contact_id": 123,
  "message": "Hi Priya! Your Thursday batch is ready — order by 11 AM.",
  "agent_name": "manual"
}
```

**Field agent personal phone SMS** (logged retroactively):
```
POST /api/telnyx/field-agent-message
{
  "contact_id": 123,
  "message": "Spoke to her, she wants to restart weekly orders",
  "direction": "outbound"
}
```

**Checking SMS performance:** `POST /api/query` → category: `sms_performance`

---

### 4C. Broadcast Campaigns

Broadcasts are one-to-many messages sent to a segment. Unlike AI outreach (which is personalised), broadcasts send the same message to everyone in the audience.

**Creating a broadcast:**
```
POST /api/broadcasts
{
  "message": "Big news: we're now delivering to Alpharetta! Order at dabbahwala.com",
  "channel": "sms",
  "audience_segment": "all",
  "exclude_optout": true
}
```

**Audience options:**
- `all` — every non-optout contact
- Any lifecycle segment: `cold`, `engaged`, `active_customer`, `new_customer`, `lapsed_customer`, `reactivation_candidate`
- SMS and email channels both supported

**Dispatch timing:**
- Null `scheduled_at` = sent on the next `[Broadcast] Dispatch` cycle (within 1 hour)
- Scheduled: set `scheduled_at` to any future ISO timestamp

**Best practice:**
- Limit broadcasts to 1 per week per audience — they bypass AI frequency controls
- Use the `active_customer` segment for promotions, `all` only for service announcements
- Test with a small segment first: create a broadcast for 5 test contacts, verify delivery, then send to full audience

---

### 4D. Email Campaigns (Instantly)

The system uses 5 lifecycle-mapped Instantly email campaigns that run on autopilot. Contacts are automatically moved between campaigns as their lifecycle stage changes.

**Campaign map:**

| Campaign name | Who receives it | Tone |
|--------------|----------------|------|
| `DW-NurtureSlow-ColdContacts` | New imports, no engagement yet | Soft introduction, menu discovery |
| `DW-PromoStandard-ActiveEngaged` | Opened emails, not yet ordered | Menu highlights, social proof |
| `DW-NewCustomerOnboarding` | Placed exactly 1 order | Welcome, subscription pitch, loyalty building |
| `DW-ActiveCustomer` | Regular buyers (2+ orders, recent) | Loyalty rewards, upsell, seasonal |
| `DW-PromoAggressive-LapsedCustomers` | Not ordered in 14–29 days | Urgency, discounts, "we miss you" |
| `DW-Reactivation-LongDormant` | Not ordered in 30+ days | Strong re-engagement, personal tone |

**Updating email copy:**
- Log into Instantly directly at `app.instantly.ai`
- Edit the campaign sequences there
- Changes are live immediately — no code deployment needed

**Campaign performance tracking:**
- `[Email Campaigns] Performance Tracker` pulls stats hourly into `instantly_analytics`
- View with: `POST /api/query` → category: `campaign_performance`
- Or via MCP: *"What's the open rate for the reactivation campaign?"*

**If a contact is in the wrong campaign:**
- Trigger an AI cycle: `POST /api/agents/cycle/run-for-contact` with `contact_id`
- The Orchestrator may issue a `move_campaign` action if the contact's stage doesn't match their current campaign
- Or manually: `POST /api/lifecycle/run` will re-evaluate rules and queue a campaign move

**Seeding new contacts into Instantly:**
- On import, contacts are auto-enrolled via `POST /api/agents/cycle/run-all` during the next batch cycle
- If a bulk import missed Instantly enrollment: run `POST /api/campaigns/bulk-push-to-instantly` (background job, pushes all pending campaign_queue rows)

---

### 4E. Field Agent Operations

**Daily workflow for field agents:**

1. **7:30 AM** — Read the daily brief email (from `core@dabbahwala.com`). Contains your top 10 contacts to call, with AI-generated notes on each.
2. **Check Airtable** → Field Sales Tasks table → filter by your name or by `hot` priority
3. **Call contacts** in priority order: `hot` → `warm`
4. **Record outcome** in the Airtable task:
   - `ordered` — they placed an order
   - `not_interested` — they don't want to order
   - `no_answer` — no response after calling
   - `declined` — they declined your offer
5. Add free-text notes — the AI reads these on the next cycle

**Reading the AI's recommended approach:**
Each Airtable task has an AI-generated `suggested_message` or approach in the notes. This is specific to that customer's history — follow it when possible.

**Logging outreach from your personal phone:**
```
POST /api/telnyx/field-agent-message
{
  "contact_id": <id>,
  "message": "<what you sent>",
  "direction": "outbound"
}
```
Or ask your manager to log it via the dashboard.

**After recording outcomes:**
- `[Field Agent] Outcome Sync` runs every 4 hours and pulls outcomes from Airtable into the system
- The AI Stack reads these outcomes on the next cycle and adapts its approach for each contact

---

### 4F. Menu Management

**Adding a new item:**
1. Go to Airtable → Menu Catalog table
2. Add a new row: Item Name, Category, Is Veg, Description, Image URL, Price, Added Date
3. The item will appear in the system within 24 hours (next sync at 6:30 AM)
4. The AI Menu Agent will immediately start recommending it in outreach copy

**Removing/discontinuing an item:**
1. Delete the row from Airtable → Menu Catalog
2. On the next sync, the item is marked `discarded` in Postgres (never fully deleted — history preserved in `menu_catalog_history`)
3. The AI stops recommending it immediately after the sync

**Changing a price:**
1. Update the price in Airtable
2. The sync records a `price_change` row in `menu_catalog_history` with old and new price

**Forcing an immediate sync** (e.g. new menu launches today):
```
POST /api/menu/sync
```

**Checking what items are currently active:**
```
GET /api/menu/items
```

**Checking an item's price history:**
```
GET /api/menu/items/{id}/history
```

---

### 4G. Agent Playbook Rules

See §3B for full playbook management guide.

**Most useful rules to configure when starting:**

```
Category: exclusion
Rule: "Never contact any contact whose notes field contains 'do not disturb'"
Priority: 1

Category: messaging
Rule: "All SMS messages must be under 140 characters"
Priority: 5

Category: advisor
Rule: "For contacts who have ordered 5+ times, always prefer field sales call over SMS"
Priority: 10

Category: observer
Rule: "If any SMS or call mentions dietary restrictions, update intent to needs_info"
Priority: 20

Category: messaging
Rule: "On Fridays, always mention the weekend special in the message"
Priority: 50
```

**Syncing rules manually:**
```
POST /api/playbook/sync-from-airtable
```

**Reading current active rules:**
```
GET /api/playbook/rules
```

---

### 4H. Growth Experiments

The Growth Engine automatically designs and runs A/B experiments to find what outreach approaches convert best.

**What runs automatically:**

| Schedule | What happens |
|----------|-------------|
| Monday 6:30 AM | `[Growth] Competitor Research` — scrapes competitor emails/sites, generates 8 new hypotheses |
| Monday 7:30 AM | `[Growth] Weekly Growth Agent` — measures all running experiments, designs new ones, emails a report |
| Daily 9 AM | `[Growth] Goal Agent` — 4-phase cycle: HYPOTHESIZE → EXPERIMENT → MEASURE → HARVEST |

**Viewing current experiments:**
```
GET /api/growth/experiments
```
Returns all running experiments with current conversion rates vs baseline.

**Reading growth insights** (what has worked so far):
```
GET /api/growth/insights
```
Returns proven signals from completed experiments.

**Reading competitor analysis:**
```
GET /api/competitor-agent/runs
```
Returns what competitors were doing and what hypotheses were generated.

**What you don't need to do:** Growth experiments are fully automated. The system selects test/control cohorts, measures results, and harvests proven approaches into `discovered_signals` automatically. You just need to read the Monday morning growth report.

---

### 4I. Reports

**Automatic daily reports (no action needed):**

| Report | Arrives | What's in it |
|--------|---------|-------------|
| Field Agent Daily Brief | 7:30 AM | Top 10 contacts to call today, AI approach notes per contact |
| Daily Activity Report | 8:00 AM | AI cycles run, SMS sent, emails queued, actions dispatched — last 24 hours |
| Daily Outcome Report | 8:30 AM | Orders placed, conversions achieved, email open rates, field agent scorecard |

**Triggering a report on-demand:**
```
# Activity report
POST /api/agents/report/activity

# Outcome report
POST /api/agents/report/outcome
```
Both return HTML + CSV and queue a `send_email_report` action to `REPORT_EMAIL_TO`.

**Historical daily metrics:**
```
GET /api/reports/daily/{date}
# e.g. GET /api/reports/daily/2025-01-15
```

**Generating a date-range report via query:**
```
POST /api/query
{
  "category": "outcome_report",
  "date_from": "2025-01-01",
  "date_to": "2025-01-31"
}
```

**Report email not arriving:**
- Check `REPORT_EMAIL_TO` env var is set in Render
- Check the `action_queue` table has a `send_email_report` row — if it's stuck in `pending`, run `[System] Action Queue` manually in n8n
- Check Gmail SMTP credentials in n8n: credential `Sk6XzPNPnJTXHEbr`

---

### 4J. Chatbot (Team Q&A)

The chatbot answers plain-English questions about DabbahWala's business, menu, and marketing by searching a knowledge base built from Google Docs in your Drive folder.

**Asking a question:**
```
POST /api/chatbot/ask
{
  "question": "What are our most popular vegetarian items?"
}
```

**Or:** `POST /api/query` → category is intelligently inferred from the question for 14 structured query types

**Adding knowledge to the chatbot:**
1. Create or update a Google Doc in Drive folder `1O0ES9uiDL6AWf9QMMYiyRUWGtymDjPF5`
2. For ad copy / social media content: include "ad copy", "social media", "facebook", or "instagram" in the doc title
3. For ground notes (processes, scripts, context): any other title
4. The doc will be indexed within 30 minutes by `[Chatbot] Docs Sync`

**Forcing a full reindex:**
```
POST /api/chatbot/reindex
```
Or trigger `[Chatbot] Docs Reindex` in n8n manually.

**Getting suggested questions:**
```
GET /api/chatbot/suggest
```
Returns common questions the chatbot is well-positioned to answer based on the current knowledge base.

---

## 5. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| AI cycles not running | `ANTHROPIC_API_KEY` expired or invalid | Update key in Render env vars |
| SMS not being sent | `[SMS] Dispatch Queue` paused or Telnyx key invalid | Check n8n workflow status; run connectivity check |
| Emails not sending | Gmail SMTP credential expired | Re-authorise n8n credential `Sk6XzPNPnJTXHEbr` |
| Contacts stuck in wrong Instantly campaign | `campaign_queue` rows not being dispatched | Run `POST /api/campaigns/bulk-push-to-instantly` |
| Airtable field sales tasks not appearing | `action_queue` stuck | Run `[System] Action Queue` manually in n8n |
| Playbook rules not taking effect | Sync hasn't run since rule was added | `POST /api/playbook/sync-from-airtable` |
| Menu items not updating | Airtable sync failed | `POST /api/menu/sync` to force; check `AIRTABLE_API_KEY` |
| Daily reports not arriving | SMTP failure or action_queue stuck | Check `action_queue` for pending `send_email_report`; verify SMTP creds |
| Lifecycle segments not updating | Stage Engine not running | `POST /api/lifecycle/run` manually; check `[Intelligence] Stage Runner` in n8n |
| No orders appearing in system | Shipday sync failed | `POST /api/shipday/ingest-orders`; verify `SHIPDAY_API_KEY` |
| `column "emails_sent" does not exist` in logs | Stats columns missing from `campaign_routing` (table predates columns) | Deploy migration `007_campaign_routing_stats_columns.sql`; all columns added with `ADD COLUMN IF NOT EXISTS` |
| Growth experiments all showing 0% conversion | Measure phase hasn't run yet | Growth experiments need 72 hours minimum or 30 conversion events |
| Contact receiving messages after opt-out | `optout` segment not set | `PATCH /api/contacts/{id}/priority` with `priority_override: "do_not_contact"` immediately; check Telnyx STOP handling |

---

*Last updated: 2026-02-27*
*See also: [Technical Reference — SYSTEM.md](SYSTEM.md) · [Tests — TESTS.md](TESTS.md)*
