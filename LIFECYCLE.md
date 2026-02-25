# DabbahWala — How the System Actually Works

A plain-language deep dive into customer lifecycle management, the intelligence engine, the AI agent pipeline, and how they collectively convert prospects into paying customers.

> **Navigation:** [README](README.md) · [System Reference](SYSTEM.md) · [Features](FEATURES.md) · [Claude Instructions](CLAUDE.md)

---

## Table of Contents

1. [The Primary Objective](#1-the-primary-objective)
2. [The Three Engines — Overview](#2-the-three-engines--overview)
3. [Why All Three Engines Are Necessary — Not Redundant](#3-why-all-three-engines-are-necessary--not-redundant)
4. [Important: The Naming Collision](#4-important-the-naming-collision)
5. [Engine 1 — The Lifecycle Engine (SQL, hourly)](#5-engine-1--the-lifecycle-engine-sql-hourly)
6. [Engine 2 — The Intelligence Engine (SQL, hourly)](#6-engine-2--the-intelligence-engine-sql-hourly)
7. [Engine 3 — The AI Agent Pipeline (Claude, every 3 h + real-time)](#7-engine-3--the-ai-agent-pipeline-claude-every-3-h--real-time)
8. [What Is an Opportunity?](#8-what-is-an-opportunity)
9. [How the Engines Run in Parallel](#9-how-the-engines-run-in-parallel)
10. [The Full Customer Journey — Cold Lead to Paying Customer](#10-the-full-customer-journey--cold-lead-to-paying-customer)
11. [The Feedback Loop — How the System Learns](#11-the-feedback-loop--how-the-system-learns)

---

## 1. The Primary Objective

Every automated process in DabbahWala exists to answer one question:

> **What is the single best thing to say to this specific customer, right now, to get them to place their next order?**

The system maintains a **goal** for every contact (`convert_to_order`, `retain`, or `reactivate`) and continuously works toward it using three parallel engines — a SQL lifecycle engine, a SQL intelligence engine, and an AI agent pipeline powered by Claude.

---

## 2. The Three Engines — Overview

| Engine | What it is | Runs | Powered by |
|--------|-----------|------|------------|
| **Lifecycle Engine** | SQL rule evaluator — classifies every contact into a lifecycle stage and routes them to the correct email campaign | Hourly | Pure SQL stored function |
| **Intelligence Engine** | Signal scanner — detects behavioural patterns across all contacts and creates **opportunity** records for high-value actions | Hourly | Pure SQL functions |
| **AI Agent Pipeline** | Per-contact AI reasoner — reads every available signal and produces one concrete outreach action per contact | Every 3 h (batch) + real-time after every inbound event | 8 Claude calls per contact per cycle |

All three run independently and in parallel. They share the same database, so their outputs compound — the lifecycle stage set by Engine 1 is read by Engine 3; the opportunity created by Engine 2 provides context that Engine 3 uses when deciding what to say.

---

## 3. Why All Three Engines Are Necessary — Not Redundant

This is the most important thing to understand about the architecture. The three engines are not doing the same job three different ways. They solve three fundamentally different problems, and removing any one of them would break the system's ability to generate orders.

### The problem each engine solves

**Engine 1 (Lifecycle Engine)** answers: *"What category is this customer in, and what marketing programme should they be receiving?"*

It knows nothing about individual behaviour beyond a few SQL predicates. It cannot craft a message, choose a channel, or decide urgency. Its job is purely classification and campaign routing. Without it, contacts would stay in the wrong email campaigns — a lapsed customer receiving cold-lead nurture emails, an active customer receiving reactivation pitches. Wrong campaign = irrelevant content = unsubscribes.

**Engine 2 (Intelligence Engine)** answers: *"Which contacts, right now, are showing a signal strong enough to warrant immediate specific action — and what kind of action?"*

It scans all contacts at once using fast SQL aggregates. It costs almost nothing to run and processes thousands of contacts in seconds. Its output is a prioritised list: "these 12 people just hit a threshold that predicts they'll respond if contacted today." Without it, the AI pipeline would have to decide who to prioritise from scratch each cycle, wasting expensive Claude calls on contacts who aren't ready and potentially missing the ones who are.

**Engine 3 (AI Agent Pipeline)** answers: *"For this specific person, given everything I know about them, what is the exact right message to send, on which channel, worded in which way, to make them place an order today?"*

It cannot run on all contacts — it makes 8 Claude API calls per person, which takes time and costs money. It must be selective. Its output is not a category or a signal — it is a ready-to-send message (or an escalation decision) for a single human being. Without it, every outreach message would be a generic template. Generic templates produce generic results.

### How they chain together to produce an order

```
ENGINE 1            ENGINE 2              ENGINE 3
Lifecycle           Intelligence          AI Agent Pipeline
─────────────────   ───────────────────   ──────────────────────────────────
Routes contacts     Scans all contacts    Takes the contacts flagged by
to the right        for behavioural       Engine 2 + those who sent inbound
email campaigns     signals every hour    messages, and for each one:
24/7                                      • reads their full individual history
                    Finds: "These 8       • understands their tone & intent
                    people are            • picks the right channel & timing
                    ready to act"         • writes personalised copy
                                          • decides if a human needs to step in
                    Creates
                    opportunity rows      Produces one action per person:
                    in the database       send_sms / move_campaign /
                                          escalate_airtable / none

                                          ↓
                                          Action Queue → dispatched to
                                          Telnyx / Instantly / Airtable
                                          → CUSTOMER RECEIVES MESSAGE
                                          → CUSTOMER PLACES ORDER
```

### What breaks if you remove one

| Remove this | What breaks |
|-------------|------------|
| **Lifecycle Engine** | Contacts stay in wrong email campaigns. New customers receive cold-lead drip emails. Lapsed customers get generic promos instead of aggressive reactivation. Email channel stops being relevant — open rates collapse. |
| **Intelligence Engine** | The AI pipeline has no signal to prioritise against. It runs on all eligible contacts every 3 hours but cannot distinguish between "this person just opened 5 emails and almost ordered" and "this person hasn't done anything in 3 months." The right contacts don't get urgent action; opportunity windows are missed. Also, field sales team gets no leads — there are no opportunity records to dispatch. |
| **AI Agent Pipeline** | Outreach becomes generic. The Intelligence Engine finds who to contact, but the message sent is a static template. A lapsed customer who hated discounts gets a discount offer. A customer who just had a bad delivery gets a promo SMS before a recovery call. A first-time buyer who asked about the veg menu gets a non-veg pitch. Conversion rates drop because messages aren't matched to the individual. |

The three engines are not alternatives to each other. They are **sequential filters and refiners**: Engine 1 keeps everyone in the right programme, Engine 2 spots who needs attention now, Engine 3 figures out exactly what to do about it.

---

## 4. Important: The Naming Collision

The Intelligence Engine (Engine 2) has five phases, two of which are named **Inference** and **Decision**. The AI Agent Pipeline (Engine 3) also has layers named **Inference** and **Decision**. They sound the same. They are completely different things.

| | Intelligence Engine | AI Agent Pipeline |
|-|--------------------|--------------------|
| "Inference" | SQL functions that detect patterns — e.g., `detect_engaged_no_order()` counts opens and orders in Postgres | Three parallel **Claude AI calls** — Sentiment Agent, Intent Agent, Engagement Agent |
| "Decision" | SQL that writes rows to the `opportunities` table based on signal counts | Four parallel **Claude AI calls** — Stage Agent, Channel Agent, Offer Agent, Escalation Agent |
| Claude involved? | **No** — zero Claude calls anywhere in the Intelligence Engine | **Yes** — all reasoning is Claude |
| Scope | Scans **all contacts** at once with SQL aggregates | Runs **one contact at a time** with full individual context |

When you read "Inference" or "Decision" in documentation, always check which engine is being discussed.

---

## 5. Engine 1 — The Lifecycle Engine (SQL, hourly)

### What it does

The lifecycle engine decides what **category** each contact belongs to — and therefore which email campaign they should receive. It asks: *"Based on this contact's purchase history and recent engagement, which marketing programme should they be in?"*

It never generates copy, never calls Claude, and never sends a message. It only sets labels and queues campaign moves.

### The 8 Lifecycle Stages

Every contact always has exactly one lifecycle stage, stored in `contacts.lifecycle_segment`.

| Stage | What it means | Typical contact |
|-------|--------------|----------------|
| `cold` | No engagement or orders yet | Fresh import; never opened an email |
| `engaged` | Opening/clicking emails but hasn't ordered | Warm prospect — seen the menu, interested |
| `new_customer` | Placed exactly 1 order in the last 7 days | Just tried DabbahWala for the first time |
| `active_customer` | 2+ orders, most recent within 14 days | Regular, happy customer |
| `cooling` | Recently overcontacted (2+ SMS, zero clicks in 7 days) | Showing fatigue — needs a break |
| `lapsed_customer` | Last order 14–29 days ago | Starting to drift; needs a nudge |
| `reactivation_candidate` | Last order 30+ days ago | Churned in practice; needs a strong re-engagement push |
| `optout` | Explicitly unsubscribed or sent STOP | Never contact again |

### How Transitions Are Decided — The Rules Engine

Lifecycle transitions are driven by the `rules` table. Each rule has:
- A **predicate** — a SQL condition evaluated against the contact's current data
- An **action** — what to set if the predicate is true (new stage, new campaign, SMS level, etc.)
- A **priority** — rules are evaluated highest-priority first; first match wins

The `evaluate_rules()` stored function loops through all contacts and all active rules each hour. When a predicate matches, the contact's `lifecycle_segment` is updated and a row is inserted into `campaign_queue` for the new Instantly campaign.

**The 7 base rules (highest priority first):**

| Priority | Rule | Predicate | Result |
|----------|------|-----------|--------|
| 70 | `optout` | `lifecycle_segment = 'optout'` | Stay optout — no campaign |
| 60 | `fatigue` | 2+ SMS sent in 7 days AND zero clicks | Move to `cooling` for 14 days |
| 50 | `first_order` | Exactly 1 total order within last 7 days | Promote to `new_customer`, start onboarding campaign |
| 40 | `active_customer` | Last order within 14 days AND 2+ total orders | Promote to `active_customer`, standard promo campaign |
| 30 | `lapsed` | Last order 14–29 days ago | Move to `lapsed_customer`, aggressive promo campaign |
| 20 | `reactivation` | Last order 30+ days ago | Move to `reactivation_candidate`, reactivation campaign |
| 10 | `any_open` | 1+ email opens in 7 days AND currently `cold` | Promote to `engaged`, standard promo campaign |

### Campaign Routing

When a contact's stage changes, `campaign_queue` receives a row specifying which Instantly email campaign to move them to:

| Lifecycle Stage | Instantly Campaign |
|----------------|--------------------|
| `cold` | `DW-NurtureSlow-ColdContacts` |
| `engaged` / `active_customer` | `DW-PromoStandard-ActiveEngaged` |
| `new_customer` | `DW-NewCustomerOnboarding` |
| `lapsed_customer` | `DW-PromoAggressive-LapsedCustomers` |
| `reactivation_candidate` | `DW-Reactivation-LongDormant` |

n8n's Action Queue Executor polls `campaign_queue` and executes the moves in Instantly automatically.

### What triggers the lifecycle engine?

- The `[Claude] Lifecycle Cycle Runner` n8n workflow fires `POST /api/lifecycle/run` every hour
- The Intelligence Engine also calls `run_lifecycle_cycle()` at the end of its own hourly cycle (Phase 5)
- Daily order CSV upload triggers a lifecycle run immediately after processing

---

## 6. Engine 2 — The Intelligence Engine (SQL, hourly)

### What it does

The Intelligence Engine scans the entire contact database every hour looking for **behavioural signals** — patterns that indicate a contact is ready to act, at risk of churning, or likely to convert if reached the right way. When it finds one, it creates an **opportunity** record in the database.

No Claude is involved anywhere. Every detection is a SQL query.

### The 5 Phases

The full cycle runs as a single call to `POST /api/intelligence/run-cycle`, triggered every hour by n8n.

---

**Phase 1 — INTAKE (data collection)**

Counts all recent activity across the system — email opens and clicks from Instantly, inbound SMS and calls from Telnyx, orders placed — looking at the last 2 hours to catch anything since the previous cycle. Also counts recent Telnyx messages and calls separately. This gives the cycle a snapshot of how active the system was recently.

No action is taken here; it is purely counting for the cycle's summary output.

---

**Phase 2 — EVIDENCE (refresh metrics)**

Calls `refresh_engagement_rollups()` to recalculate the rolling 7-day and 30-day engagement metrics for every contact: `opens_7d`, `clicks_7d`, `orders_7d`, `sms_clicks_7d`. These rollup figures are stored in `engagement_rollups` and are what every signal detection query in Phase 3 reads.

Also calculates the current lifecycle distribution — how many contacts are in each stage — for reporting.

---

**Phase 3 — SIGNAL DETECTION (pattern recognition, pure SQL)**

This is the phase called "Inference" in the cycle, but there is no Claude involved. Seven SQL functions scan the freshly-updated rollup data and identify contacts who match specific behavioural patterns.

| Signal | SQL Detection Logic | What it means |
|--------|--------------------|-|
| `engaged_no_order` | `opens_7d >= 3` AND no order in last 7 days | Contact is reading emails repeatedly but hasn't bought yet — they're interested, just need a push |
| `new_customer_no_repeat` | Exactly 1 total order, first order 5+ days ago | First-time buyer who hasn't come back — the highest-leverage retention window |
| `lapsed_reengaged` | In `lapsed_customer` segment AND recent SMS reply or email click | A lapsed customer just showed a signal of life — hot moment to re-engage |
| `reorder_intent` | Call transcript contains keywords like "order again", "same as last time", "when can I get" | Customer verbally signalled they want to reorder; didn't complete it |
| `app_customers_for_conversion` | `primary_source` is a food delivery app AND no direct website order in 30 days | Customer ordering via Uber Eats / DoorDash — opportunity to bring them direct |
| `subscription_candidates` | 3+ one-time orders AND no subscription | Regular buyer who hasn't locked in a subscription — obvious upsell |
| `high_value_at_risk` | 5+ total orders AND no order in 14+ days AND not already lapsed/optout | High-value customer showing first signs of drift — intervene before they churn |

---

**Phase 4 — OPPORTUNITY CREATION (this is the "Decision" phase)**

For each contact in each detected signal group, this phase calls `create_opportunity()` — a SQL stored function — to write a row to the `opportunities` table. The row specifies:

- **Who** — `contact_id`
- **What action** — `send_email`, `send_sms`, or `field_sales_call`
- **Priority** — `hot` (act today), `warm` (act this week), or `cold`
- **Why** — a human-readable reason string
- **Suggested message** — a pre-written starting point for the outreach
- **Confidence score** — 0.75–0.92 depending on signal strength

Different signals get different actions:

| Signal | Action | Priority | Confidence |
|--------|--------|----------|------------|
| `engaged_no_order` | Email | warm | 0.75 |
| `new_customer_no_repeat` | SMS | warm | 0.80 |
| `lapsed_reengaged` | Field sales call | hot | 0.90 |
| `reorder_intent` | SMS | hot | 0.92 |
| `app_customers_for_conversion` | SMS + campaign move | warm | 0.82 |
| `subscription_candidates` | SMS | warm | 0.78 |
| `high_value_at_risk` | Field sales call | hot | 0.88 |

`create_opportunity()` deduplicates — if a pending or dispatched opportunity already exists for a contact, it won't create another.

---

**Phase 5 — EXECUTION (run lifecycle engine)**

Calls `run_lifecycle_cycle()` — the same SQL lifecycle engine described in Engine 1. This means the lifecycle engine always runs at the end of each intelligence cycle, ensuring stage transitions are up to date before any opportunities are acted on.

Also counts pending `campaign_queue` rows and pending `opportunities` rows for the cycle summary.

---

## 7. Engine 3 — The AI Agent Pipeline (Claude, every 3 h + real-time)

### What it does

The AI Agent Pipeline is where individual-level AI reasoning happens. For each eligible contact, it makes 8 Claude API calls to understand who this person is right now and choose the single best action to take. Unlike the Intelligence Engine which scans all contacts with the same SQL queries, the AI pipeline runs fully personalised reasoning — it reads every SMS this person has sent, every delivery event, every order history detail, every previous action outcome.

### The Goal Object — Why the Agent Is Running

Before the pipeline starts, it checks the `customer_goals` table for an active goal for this contact. Every contact has at most one active goal at a time:

| Goal | When assigned | What it means |
|------|--------------|---------------|
| `convert_to_order` | Cold/engaged contacts | No orders yet — push for a first purchase |
| `retain` | Active customers | Already buying — keep them coming back |
| `reactivate` | Lapsed/reactivation contacts | Bought before, gone quiet — win them back |

The goal is passed into every Claude call as part of the context. Every agent knows what the pipeline is ultimately trying to achieve for this specific person.

### When the pipeline runs

**Batch mode:** The `[Claude] Agent Orchestration` n8n workflow calls `POST /api/agents/cycle/run-all` every 3 hours. This queries all contacts who are eligible (have an active goal, opened/clicked email in 30 days, had any event in 30 days, or placed an order in 60 days — excluding `churned` and `optout`) and runs the full 8-call pipeline for each one.

**Real-time mode:** Whenever the Telnyx Inbound Collector receives a new SMS or call from a customer, it immediately calls `POST /api/agents/cycle/run-for-contact` for that specific contact. This means a customer who replies "interested!" at 2 PM gets a reasoning cycle and a follow-up action within minutes, not 3 hours later.

### Layer 1 — Inference (3 parallel Claude calls)

These three agents read the contact's full history and produce a structured assessment of where this person is emotionally and behaviourally right now.

**Input for all three:**
- Full contact profile (segment, total orders, engagement rollups)
- Last 30 days of events (orders, opens, clicks, SMS received, delivery events)
- Last 30 days of SMS and call transcripts
- Last 10 opportunity outcomes (did previous outreach lead to an order? A rejection? No answer?)
- All active playbook rules from `agent_playbook`

---

**Sentiment Agent** — reads communication tone and outcome history to classify the customer's current emotional state as `positive`, `neutral`, or `negative`, with a confidence score and a one-sentence summary. If recent opportunity outcomes show the customer ordered after being contacted, that's weighted as positive sentiment evidence even without fresh messages.

**Intent Agent** — classifies purchase intent:

| Intent | Evidence signals |
|--------|----------------|
| `ready_to_order` | Recent delivery event, order placed, reorder keywords in transcript |
| `needs_info` | Questions about menu/prices, multiple opens without clicking |
| `price_sensitive` | SMS mentions "price", "too expensive", haggling |
| `not_interested` | Explicitly declined, multiple no-answers, low engagement trend |
| `unknown` | Insufficient evidence to classify |

The Intent Agent also reads the contact's historical order preferences (top 5 items by quantity, preferred order days, subscription vs one-time ratio) so it can note signals like "this customer always orders on Thursdays and hasn't ordered yet this Thursday."

**Engagement Agent** — scores overall engagement from 0.0 (completely cold) to 1.0 (highly active), classifies the trend as `rising`, `flat`, or `falling`, and notes how many hours ago the last meaningful interaction occurred.

All three outputs are stored as a single row in `inference_results`, linked to the contact and goal.

---

### Layer 2 — Decision (4 parallel Claude calls)

These four agents take the Layer 1 inference bundle and produce concrete recommendations. They run in parallel, each solving a different sub-problem.

**Stage Agent** — recommends whether the contact's lifecycle stage should change, based on inference signals. For example: if intent is `ready_to_order` and engagement is rising, it might recommend moving from `lapsed_customer` back to `engaged`. This is advisory — the AI recommendation is logged but the actual stage is set by the SQL lifecycle engine.

**Channel Agent** — decides which communication channel to use next and when:

| Decision | Options |
|----------|---------|
| Channel | `sms`, `email`, `call`, `none` |
| Timing | `immediate`, `tomorrow`, `3days`, `none` |

The Channel Agent enforces an explicit rotation strategy — if the last action was email, recommend SMS next; if SMS was last, recommend email. If SMS has been tried 3+ times with no replies, escalate to `call`. It also randomises timing for lapsed contacts so messages arrive on different days of the week and feel less automated.

**Offer Agent** — selects the type of message angle and drafts the actual copy (max 160 characters, SMS-ready). It cycles through 6 messaging progressions based on how many total outreach attempts have been made:

| Attempt # | Angle |
|-----------|-------|
| 1 | Warm reminder — "We miss you, here's what's new" |
| 2 | Social proof — "Our customers are raving about X dish" |
| 3 | Discount/incentive — "Special comeback offer, X% off" |
| 4 | Urgency/scarcity — "Limited fresh batches this week" |
| 5 | Personal/emotional — References their actual past order |
| 6+ | Curiosity/novelty — Teases a new dish or seasonal special |

The Offer Agent reads previous outcome data: if discounts led to orders, keep offering discounts; if two or more offers were declined, switch to social proof instead.

**Escalation Agent** — decides whether a human field sales agent needs to intervene. Escalation is triggered when:
- Sentiment is very negative
- Intent is `ready_to_order` but the customer has been stalled despite multiple automated touches
- 3+ failed automated attempts (no-answer, not-interested outcomes)
- 6+ total automated touches with no conversion
- Goal deadline is approaching

When escalating for stuck contacts, the agent generates specific creative instructions for the field team — not just "contact this customer" but "try a personal voice note from the owner" or "offer to personally handle the next order."

All four outputs are stored as a single row in `decision_recommendations`.

---

### Layer 3 — Orchestrator (1 Claude call)

The Orchestrator reads all four Layer 2 recommendations plus the latest delivery event and produces the single final action. It is the arbiter — when the four decision agents disagree, or when a delivery event overrides everything, the Orchestrator makes the call.

**Delivery-aware guardrails (checked first, override everything):**

| Latest delivery event | Orchestrator action |
|----------------------|---------------------|
| `delivered` | Send a warm thank-you SMS with reorder nudge (unless already contacted in last 24 h) |
| `delivery_failed` / `delivery_returned` | `escalate_airtable` with `urgency=high` — relationship recovery before any selling |
| `out_for_delivery` / `driver_assigned` / `delivery_arrived` | `none` — do not interrupt an order in progress |
| No delivery event in 7 days | Use intent and engagement signals normally |

**General guardrails (checked second):**
- Never contact via the same channel more than once in 24 hours
- Maximum 3 SMS per week per contact
- `escalate_airtable` always beats automated channels
- If `intent=not_interested` → `none` unless escalation urgency is high
- If `priority_override=do_not_contact` → always `none`, no exceptions
- If `priority_override=high` → prefer `escalate_airtable` or `send_sms` even on weak signals

**Anti-repetition logic:**
- If recent actions show `move_campaign` or `send_sms` in the last 24 h → `none` for today
- If only email/campaign actions in 7 days AND zero email opens → rotate to `send_sms`
- If only SMS actions in 7 days → rotate to `move_campaign` (email channel)

**Persistence principle:** No-response to email or SMS is never a reason to stop. The Orchestrator keeps rotating channels and message angles indefinitely. The only reason to choose `none` permanently is `priority_override=do_not_contact`, `lifecycle_segment=optout`, or clear `not_interested` intent.

The Orchestrator outputs one of four actions:

| Action | What it does |
|--------|-------------|
| `send_sms` | Write a row to `action_queue`; n8n SMS Dispatch picks it up and sends via Telnyx |
| `move_campaign` | Write a row to `action_queue`; n8n Action Queue Executor moves the lead to the appropriate Instantly campaign |
| `escalate_airtable` | Write a row to `action_queue`; n8n Action Queue Executor creates a task in Airtable for the field sales team |
| `none` | Nothing written to `action_queue` — wait for next cycle |

The action, channel, full reasoning chain, and list of guardrails that were evaluated are all stored in `orchestrator_log`. The action itself is written to `action_queue` with `status='pending'`.

---

### Layer 4 — Daily Report Agents (2 Claude calls, daily)

Two report agents run each morning and are not part of the per-contact cycle:

- **Activity Report Agent** (8:00 AM) — reads the last 24 hours of agent runs, actions queued, SMS/calls sent, and field agent performance, then generates an HTML email summary with a CSV attachment.
- **Outcome Report Agent** (8:30 AM) — reads orders detected, email opens, goal achievements, order day patterns, top menu items, and field agent scorecard, then generates an HTML email with actionable insights.

Both reports are sent via Gmail-SMTP to `REPORT_EMAIL_TO`.

---

### Playbook Injection — How Users Configure Agent Behaviour

The `agent_playbook` table (synced from Airtable every 15 minutes) injects user-configured rules into the system prompt of every Layer 1, 2, and 3 agent. This means marketing managers can change how the AI reasons without any code changes.

Rules are fetched once at the start of each contact's cycle and injected into all 8 Claude calls.

| Category | Effect |
|----------|--------|
| `exclusion` | Override everything — "Never contact contacts tagged VIP without approval" |
| `priority` | Bias reasoning — "Prioritise contacts with 3+ orders over cold leads" |
| `inference` | Shape classification — "If SMS says 'price', always classify as price_sensitive" |
| `decision` | Direct actions — "Always use SMS for reactivation, never email" |
| `messaging` | Copy style — "Include delivery slot info in all thank-you messages" |
| `general` | Open-ended instructions that don't fit other categories |

---

## 8. What Is an Opportunity?

An **opportunity** represents a detected moment where a specific customer is likely to respond positively to a specific type of outreach. It is a record in the `opportunities` table, created when either the Intelligence Engine or a human/API call determines that a contact should be reached out to.

### What an opportunity contains

| Field | What it holds |
|-------|--------------|
| `contact_id` | Who this is for |
| `action` | What to do — `send_email`, `send_sms`, or `field_sales_call` |
| `priority` | `hot` (act today), `warm` (act this week), `cold` |
| `reason` | Human-readable explanation — e.g. "Lapsed customer re-engaged after 45 days" |
| `suggested_message` | A pre-written starting point for the outreach copy |
| `confidence_score` | 0.00–1.00 — how strongly the signal indicates this will work |
| `status` | Current stage in the opportunity lifecycle |
| `airtable_record_id` | Set when dispatched to Airtable field sales |
| `outcome` | Set when a human records what happened |
| `outcome_notes` | Free-text detail from the field agent |

### How opportunities are created

Opportunities are created in three ways:

1. **Intelligence Engine (Phase 4, hourly, automated)** — the most common source. When the SQL signal detection in Phase 3 finds a matching contact, Phase 4 calls `create_opportunity()` for each one. This happens without any human involvement.

2. **AI Agent Pipeline (Layer 3, on escalation)** — when the Orchestrator decides to `escalate_airtable`, it writes to `action_queue`; the Action Queue Executor then creates an Airtable task. This is not technically an `opportunities` row — it goes through `action_queue` — but functionally represents the same thing for field sales.

3. **Manual / API** — the `POST /api/opportunities` endpoint and Claude Desktop MCP tools allow humans or external processes to create opportunities directly.

### The opportunity lifecycle

```
[Signal detected by Intelligence Engine]
            │
            ▼
     status = 'pending'
            │
   [n8n Action Queue Executor dispatches it]
   [Telnyx / Instantly / Airtable]
            │
            ▼
    status = 'dispatched'
    dispatched_at = now()
    airtable_record_id = <id> (for field_sales_call)
            │
   [Field agent records result in Airtable]
   [n8n Airtable Outcome Sync polls every 15 min]
   [POST /api/opportunities/{id}/outcome]
            │
            ▼
   status = 'outcome_recorded'
   outcome = 'ordered' | 'not_interested' | 'no_answer' | 'declined'
   outcome_notes = <agent's notes>
```

### How opportunity outcomes feed back into AI reasoning

This is the system's learning loop. When an AI agent cycle runs for a contact, it fetches the last 10 opportunity outcomes via `_fetch_recent_outcomes()`. These outcomes are injected into every Layer 1 and Layer 2 agent's context as a feedback signal:

- **Ordered after a discount offer** → Offer Agent learns to continue using discounts for this person
- **2+ declined offers** → Offer Agent switches to social proof instead
- **3+ no-answers** → Escalation Agent recommends human intervention
- **6+ automated touches, no conversion** → Escalation Agent flags this as a stuck case
- **Positive reply or order after SMS** → Sentiment and Intent Agents weight current state as more positive

The agents do not update a model or fine-tune weights — they receive the outcomes as text in their context window and reason about them just as a human sales manager would review past call notes before calling a customer.

---

## 9. How the Engines Run in Parallel

The three engines all run via n8n workflows on different schedules, triggered independently. They share the same PostgreSQL database but do not wait for each other.

```
n8n schedule    Engine                          What it does
─────────────   ────────────────────────────    ────────────────────────────────────────
Every hour      Lifecycle Engine                evaluate_rules() on all contacts
                                                → update lifecycle_segment
                                                → queue campaign moves

Every hour      Intelligence Engine (full)      INTAKE → EVIDENCE → SIGNAL DETECTION
                                                → OPPORTUNITY CREATION → run lifecycle
                                                (this also calls the Lifecycle Engine
                                                 at the end of Phase 5)

Every 3 hours   AI Agent Pipeline (batch)       Run all 8 Claude calls for every
                                                eligible contact
                                                → write to action_queue

Real-time       AI Agent Pipeline (single)      Triggered immediately after any inbound
(after inbound  contact)                        SMS or call — runs full 8-call pipeline
                                                for that specific contact only

Every 30 min    Action Queue Executor           Dispatch pending action_queue rows:
                                                SMS via Telnyx, email via Instantly,
                                                field tasks via Airtable

Every 15 min    Airtable Outcome Sync           Poll Airtable for field agent outcomes
                                                → POST /api/opportunities/{id}/outcome

Every 15 min    Playbook Sync                   Sync agent_playbook from Airtable
                                                → available at next agent cycle
```

### How they complement each other

The Lifecycle Engine is fast and deterministic — it classifies thousands of contacts in seconds using simple SQL predicates. It ensures every contact is in the right email campaign at all times.

The Intelligence Engine is fast and broad — it finds the subset of contacts worth acting on urgently. By creating `opportunities` before the AI pipeline runs, it gives the AI agents a list of "already-detected-hot" contacts to prioritise.

The AI Agent Pipeline is slow and deep — it spends significant compute (8 Claude calls per contact) doing personalised reasoning for each individual. It reads everything about that person and makes a nuanced, human-like decision. Running it every 3 hours (rather than every hour) is a cost and latency tradeoff: it's expensive, but the quality of decision is far higher than any SQL rule could produce.

The real-time trigger bridges the gap — when a customer reaches out, the system doesn't wait 3 hours for the next batch. It reasons immediately and can send a follow-up within minutes.

---

## 10. The Full Customer Journey — Cold Lead to Paying Customer

Here is how a new contact progresses from import to paying customer, with each system's contribution visible at every step.

### Step 1: Contact enters as cold lead

A contact is imported (via CSV, Airtable sync, or direct order). They start as `lifecycle_segment = 'cold'` with no engagement data.

- **Lifecycle Engine:** Routes them to the `DW-NurtureSlow-ColdContacts` Instantly campaign. They start receiving drip emails.
- **AI Agent Pipeline:** If they have an active goal (`convert_to_order`), they are eligible for batch cycles every 3 hours — but with no events yet, the agents will see zero engagement and typically output `none` (no action) or `move_campaign` to start the email sequence.
- **Intelligence Engine:** Not yet detected by any signal — all signal queries require either engagement events or order history.

### Step 2: Contact opens their first email

The Instantly Campaign Performance n8n workflow polls Instantly hourly and logs an `email_open` event.

- **Intelligence Engine (next hourly cycle):**
  - EVIDENCE phase refreshes rollups → `opens_7d = 1`
  - SIGNAL DETECTION: `any_open` predicate matches (1+ opens, currently `cold`) but this is handled by the Lifecycle Engine
  - No opportunity yet — 1 open is below the `engaged_no_order` threshold of 3+
- **Lifecycle Engine:** `any_open` rule fires → segment promoted to `engaged`, campaign moved to `DW-PromoStandard-ActiveEngaged`
- **AI Agent Pipeline (next batch cycle):**
  - Intent Agent sees 1 email open — classifies as `needs_info`
  - Channel Agent recommends staying on email (they just opened one)
  - Offer Agent drafts a warm reminder about the menu
  - Orchestrator: `move_campaign` — already on the right campaign, so may output `none`

### Step 3: Contact opens 3 emails in a week, still no order

- **Intelligence Engine:** SIGNAL DETECTION detects `engaged_no_order` (3+ opens, no order in 7 days)
  - OPPORTUNITY CREATION creates `opportunities` row: `action=send_email`, `priority=warm`, `confidence=0.75`
  - Suggested message: "We noticed you've been checking us out! Today's menu is fresh — free delivery over $35"
- **AI Agent Pipeline (batch cycle, reads opportunity context):**
  - Sentiment Agent: `positive` (opens are a positive signal)
  - Intent Agent: `needs_info` (opening but not ordering — still evaluating)
  - Offer Agent: Attempt #1 — warm reminder angle, drafts specific menu copy
  - Orchestrator: sends SMS with the menu reminder (rotates off email since that channel is being worked by Instantly)
- **Action:** SMS sent via Telnyx. `action_queue` row marked `done`.

### Step 4: Contact replies to SMS — "What's on the menu this week?"

- **Real-time trigger fires immediately:** Telnyx Inbound Collector receives the SMS and calls `POST /api/agents/cycle/run-for-contact`
- **AI Agent Pipeline (real-time):**
  - Intent Agent sees the question in the transcript → classifies as `needs_info` heading toward `ready_to_order`
  - Channel Agent: `immediate` timing, `sms` channel (they just replied)
  - Offer Agent: social proof angle — "This week we have X, Y, Z — our most popular item is the Thali Box, customers order it every week"
  - Orchestrator: `send_sms` immediately
- **Action:** SMS sent within minutes of their reply.

### Step 5: Contact places their first order

- **Daily CSV upload (1 PM EST):** Order appears in the CSV → `ingest_event()` called → `order_placed` event logged → `total_orders` incremented to 1 → `last_order_at` = today
- **Lifecycle Engine (immediately after CSV upload):** `first_order` rule fires → segment → `new_customer` → campaign → `DW-NewCustomerOnboarding`
- **AI Agent Pipeline (triggered post-upload):** Sees `order_placed` event → Orchestrator applies delivery guardrail: wait for delivery, then send thank-you
- **Shipday Delivery Collector (30 min later):** Delivery status `COMPLETED` → `delivered` event logged
- **AI Agent Pipeline (real-time):** Delivery event detected → Orchestrator: `send_sms` — warm thank-you with reorder nudge
- **Goal update:** If goal was `convert_to_order`, it is now achieved → goal marked `converted = true`

### Step 6: Five days pass, no second order

- **Intelligence Engine:** SIGNAL DETECTION detects `new_customer_no_repeat` (1 order, 5+ days since first)
  - Creates opportunity: `action=send_sms`, `priority=warm`, suggested message includes subscription pitch
- **AI Agent Pipeline (batch):**
  - Offer Agent: Attempt #2 — social proof angle this time, references their first order
  - Channel Agent: SMS (they responded to SMS before)
  - Orchestrator: `send_sms` — "Hope you enjoyed your first DabbahWala meal! Subscribe weekly and save 15–20%"

### Step 7: Contact becomes a regular

Two more orders are placed within 14 days → `active_customer` stage → `DW-PromoStandard-ActiveEngaged` campaign → goal changes to `retain`. The AI pipeline continues running every 3 hours, but with `retain` as the goal, the Offer Agent angles toward loyalty messaging and the Escalation Agent threshold rises (they're already a customer, so less urgency to escalate).

### Step 8: Contact goes quiet — 20 days, no order

- **Lifecycle Engine:** `lapsed` rule fires (last order 14–29 days ago) → segment → `lapsed_customer` → campaign → `DW-PromoAggressive-LapsedCustomers`
- **Intelligence Engine:** No `lapsed_reengaged` signal yet (they haven't shown a sign of life)
- **AI Agent Pipeline:** Goal changes to `reactivate`. Offer Agent cycles through more aggressive angles. Escalation Agent starts considering a human touch.

### Step 9: Contact re-engages — clicks an email link

- **Intelligence Engine:** SIGNAL DETECTION detects `lapsed_reengaged` (lapsed segment + email click)
  - Creates opportunity: `action=field_sales_call`, `priority=hot`, `confidence=0.90`
  - Suggested message: "Call them to welcome them back; mention improvements, offer subscription"
- **Action Queue Executor:** Creates an Airtable task for the field sales team
- **Airtable Outcome Sync (15 min later):** Field agent sees the task, calls the customer
- **Outcome recorded in Airtable:** `outcome=ordered`, notes: "They wanted to restart weekly orders"
- **Feedback loop:** This `ordered` outcome now appears in the next AI pipeline cycle → Sentiment Agent classifies as `positive`, Intent Agent classifies as `ready_to_order` → Orchestrator sends a subscription confirmation SMS

---

## 11. The Feedback Loop — How the System Learns

The system does not use machine learning or model fine-tuning. Instead, it uses **outcome-aware context injection** — every time an AI agent cycle runs, it reads the history of what worked and what didn't for that specific contact, and the Claude agents reason about it directly.

```
Intelligence Engine detects signal
            │
            ▼
    opportunity created (pending)
            │
            ▼
    Action Queue Executor dispatches it
    (SMS / email / Airtable task)
            │
            ▼
    Customer responds (or doesn't)
            │
            ▼
    Airtable Outcome Sync records result:
    outcome = 'ordered' / 'not_interested' /
              'no_answer' / 'declined'
            │
            ▼
    Next AI agent cycle reads outcomes
    via _fetch_recent_outcomes()
            │
            ├─ Ordered after discount → Offer Agent uses discount again
            ├─ 2+ declines → Offer Agent switches to social proof
            ├─ 3+ no-answers → Escalation Agent triggers human intervention
            ├─ 6+ touches, no conversion → Creative escalation with new strategy
            └─ Positive reply → Sentiment/Intent Agents weight state positively
```

This loop means the system gets progressively better at deciding how to handle each individual contact over time — not by retraining a model, but by giving Claude increasingly detailed outcome history to reason about.

---

*See also: [SYSTEM.md](SYSTEM.md) for technical reference · [FEATURES.md](FEATURES.md) for business feature descriptions · [README.md](README.md) for quick start*
