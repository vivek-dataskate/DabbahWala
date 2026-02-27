# DabbahWala — User Stories

> Last updated: 2026-02-27

Stories are grouped by **persona**. Each story follows the standard format:
> *As a [persona], I want to [action] so that [outcome].*

---

## Personas

| # | Persona | Who They Are |
|---|---------|-------------|
| 1 | **Marketing Operator** | Manages campaigns, contacts, broadcasts, playbook rules, and daily reports |
| 2 | **Field Agent** | On-ground sales rep; receives daily call briefs, logs outcomes, escalates hot leads |
| 3 | **Admin** | Deploys schema, runs E2E tests, manages infrastructure and n8n workflows |
| 4 | **Analytics / Growth User** | Queries marketing metrics, monitors AI experiments, researches competitors |
| 5 | **Customer** | End-customer of the food delivery service — receives SMS/email, places orders |

---

## 1. Marketing Operator

### Contact Management

- As a Marketing Operator, I want to bulk-upload contacts via CSV so that I can seed new leads into the system quickly.
- As a Marketing Operator, I want to view a contact's lifecycle segment (cold, engaged, active_customer, lapsed, etc.) so I understand where they are in the funnel.
- As a Marketing Operator, I want to manually override a contact's priority so the AI Stack focuses on high-value prospects first.
- As a Marketing Operator, I want to flag a contact as opted-out so they are excluded from all outreach channels.
- As a Marketing Operator, I want to place a contact in a cooling-off period so they aren't contacted too frequently and don't feel spammed.
- As a Marketing Operator, I want to add a free-text note on a contact so the AI can use it as context in future outreach cycles.

### Broadcasts

- As a Marketing Operator, I want to send an SMS broadcast to a specific lifecycle segment (e.g., all lapsed customers) with a targeted offer.
- As a Marketing Operator, I want to send an email broadcast to all engaged contacts announcing a new menu item or promotion.
- As a Marketing Operator, I want to preview which contacts will receive a broadcast before I send it, so I can validate the audience.

### Email Campaigns (Instantly)

- As a Marketing Operator, I want to see which Instantly email campaign each lifecycle segment maps to, so I know contacts are in the right sequence.
- As a Marketing Operator, I want to track email open rates, click rates, and reply rates per campaign in one dashboard.
- As a Marketing Operator, I want contacts to automatically move between campaigns when their lifecycle segment changes, so sequences stay relevant.
- As a Marketing Operator, I want to create or update SMS templates for different outreach scenarios (welcome, re-engagement, special offer) so messaging stays fresh.

### Playbook Rules

- As a Marketing Operator, I want to add a new playbook rule in Airtable (e.g., "never SMS on Sundays") so the AI respects business and compliance constraints.
- As a Marketing Operator, I want to set rule priority so conflicting instructions are resolved predictably.
- As a Marketing Operator, I want to deactivate a rule temporarily without deleting it, so I can re-enable it easily for seasonal campaigns.
- As a Marketing Operator, I want playbook changes in Airtable to sync to the system automatically (daily 6 AM) so I don't have to trigger deployments.

### Menu Management

- As a Marketing Operator, I want to add a new menu item in Airtable and have it automatically sync to the database so the AI references accurate menu content in outreach.
- As a Marketing Operator, I want to view the history of price changes and status changes for any menu item so I have an audit trail.
- As a Marketing Operator, I want items removed from Airtable to be marked as discarded in the database (not hard-deleted) so historical order data remains intact.

### Reports

- As a Marketing Operator, I want to receive a daily activity report by email each morning summarising new orders, SMS activity, and email campaign performance.
- As a Marketing Operator, I want to receive a daily outcome report showing AI-driven actions taken (SMSes sent, leads escalated, campaigns moved) so I can see what the system did overnight.
- As a Marketing Operator, I want to view which contacts converted after a specific AI-driven outreach action, so I can measure ROI.

---

## 2. Field Agent

### Daily Brief

- As a Field Agent, I want to receive a daily brief each morning (7:30 AM) listing my top-10 priority contacts to call today, ordered by conversion likelihood.
- As a Field Agent, I want each brief entry to include the contact's name, phone number, lifecycle stage, last interaction summary, and AI-recommended talking points so I'm prepared before each call.
- As a Field Agent, I want the brief delivered to my inbox (or Airtable) so I don't need to log into a separate system.

### Outcome Logging

- As a Field Agent, I want to log a call outcome (connected, not answered, interested, not interested) via Airtable or SMS so the AI updates its plan for that contact.
- As a Field Agent, I want to mark a contact as a "hot lead" after a call so they get escalated to the marketing team immediately.
- As a Field Agent, I want to add a free-text call note so the AI can incorporate what I learned in future outreach cycles.

### Task Management

- As a Field Agent, I want to see my open field tasks prioritised in Airtable so I always know who to contact next.
- As a Field Agent, I want completed tasks to be archived automatically so my task list stays clean and focused.
- As a Field Agent, I want new tasks assigned by the AI to appear in my Airtable view within 4 hours of the AI Stack running.

---

## 3. Admin

### Deployment & Schema

- As an Admin, I want schema migrations to run automatically on every deploy via `render_build.sh` so the database is always up to date without manual steps.
- As an Admin, I want migrations to be idempotent (`CREATE TABLE IF NOT EXISTS`) so re-running the build script is always safe.
- As an Admin, I want the next available migration number tracked in `CLAUDE.md` so multiple sessions don't create conflicting migration files.

### Testing

- As an Admin, I want to run the full E2E test suite (55+ tests, 14 groups) on demand via `POST /api/test/run` to verify all features are working.
- As an Admin, I want the daily automated test suite (5 AM) to email me if any tests fail so I'm alerted before business hours.
- As an Admin, I want every new feature or bug fix to include corresponding tests in `test_harness_service.py` and `TESTS.md` so test coverage never regresses.

### n8n Workflow Management

- As an Admin, I want to view all 26 n8n workflow schedules and their statuses in one place (`/api/admin/schedules`).
- As an Admin, I want to activate or deactivate a workflow via API without logging into the n8n UI.
- As an Admin, I want all workflows to fetch credentials at runtime from `/api/credentials` so no secrets are embedded in workflow JSON.

### Credentials & Security

- As an Admin, I want to store and rotate API keys (Airtable, Instantly, Telnyx, Shipday, Anthropic) in Render environment variables so they never appear in source code.
- As an Admin, I want all n8n workflows to authenticate via a single "DW Admin Secret" HTTP Header credential so rotating the master secret updates all workflows at once.

### Connectivity

- As an Admin, I want a manual connectivity-check workflow (`[System] Connectivity Check`) that tests all external integrations (Telnyx, Airtable, Shipday, Instantly) and reports failures, so I can diagnose outages quickly.

---

## 4. Analytics / Growth User

### Marketing Queries

- As an Analytics User, I want to ask plain-English questions like "How many lapsed customers placed an order last month?" and get SQL-backed answers instantly.
- As an Analytics User, I want 14 pre-built Tier-1 marketing queries (lifecycle breakdown, top contacts by order value, campaign funnel metrics, etc.) available without writing SQL.
- As an Analytics User, I want a Claude-powered Tier-2 query mode for complex multi-step questions that can't be answered with a single SQL query.
- As an Analytics User, I want query results formatted as tables so they're easy to read and copy into reports.

### AI Stack Visibility

- As an Analytics User, I want to view the Observer Layer output (sentiment, intent, engagement score, engagement trend) for any contact so I can understand how the AI perceives them.
- As an Analytics User, I want to see the Advisor Layer recommendation (lifecycle stage, channel, offer type, escalation flag) for each contact along with the reasoning so I can audit AI decisions.
- As an Analytics User, I want to review the Orchestrator's final action decision and which playbook guardrails were applied so I can validate business rule compliance.
- As an Analytics User, I want to see the full action queue — pending, in-progress, and completed AI-driven actions — so I have a real-time view of the system's outreach pipeline.

### Growth Experiments

- As a Growth User, I want the Goal Agent to automatically hypothesize A/B experiments (timing, offer, message angle, channel sequence) to improve conversion rates, so growth is data-driven and continuous.
- As a Growth User, I want to see which experiments are currently active, how many contacts are enrolled in each, and real-time conversion rates, so I know what's being tested.
- As a Growth User, I want successful experiment signals to be automatically harvested as reusable SQL rules so proven tactics become permanent system behaviour.
- As a Growth User, I want a weekly growth summary report showing experiment outcomes, conversion deltas vs. baseline, and recommended next hypotheses.
- As a Growth User, I want duplicate hypotheses to be rejected (via `hypothesis_hash`) so the system doesn't run the same experiment twice.

### Competitor Research

- As a Growth User, I want the Competitor Agent to automatically research competing food delivery services every Monday and surface actionable insights so I'm aware of market moves.
- As a Growth User, I want competitor insights to automatically seed new Goal Agent hypotheses so research findings translate directly into testable experiments.
- As a Growth User, I want each competitor research run logged in `competitor_agent_runs` for auditing and trend comparison over time.

### Lifecycle Analytics

- As an Analytics User, I want to see how contacts flow between lifecycle segments over time so I can identify bottlenecks in the funnel.
- As an Analytics User, I want to know which lifecycle rules are triggering most frequently so I can tune thresholds and keep the stage engine accurate.
- As an Analytics User, I want to track re-engagement campaign effectiveness (reply rate, order conversion) for lapsed customers specifically, since they are the highest-value recovery target.
- As an Analytics User, I want 7-day and 30-day engagement rollups (opens, clicks, SMS, orders) per contact available in a single table so any query can join them without recomputing metrics.

---

## 5. Customer

### SMS Experience

- As a Customer, I want to receive a personalised SMS with a relevant offer (e.g., "We miss you! Here's 10% off your next order") when I haven't ordered in a while, so re-engagement feels human rather than automated.
- As a Customer, I want to reply STOP to immediately opt out of all SMS communications, and have that respected permanently.
- As a Customer, I want SMS messages to feel timely and contextual — referencing my last order, my dietary preferences, or a menu item I'd enjoy — not generic mass-blasts.
- As a Customer, I want SMS messages sent at reasonable hours (no late-night or early-morning texts).

### Email Experience

- As a Customer, I want to receive a welcome email sequence after my first order introducing me to the full menu.
- As a Customer, I want re-engagement emails when I've been inactive, with a compelling, personalised offer to return.
- As a Customer, I want to be able to unsubscribe from marketing emails and have it respected immediately across all campaigns.
- As a Customer, I want emails to be relevant and not repetitive — not the same offer I received last week.

### Ordering & Delivery

- As a Customer, I want to receive an order confirmation with estimated delivery time so I know my order is on its way.
- As a Customer, I want a delivery status update (out for delivery, delivered) so I'm not left wondering.
- As a Customer, I want a friendly follow-up message after delivery asking for feedback so the restaurant knows what I thought.

### Chatbot

- As a Customer, I want to ask questions about the menu (ingredients, prices, dietary options, portion sizes) and receive instant, accurate answers.
- As a Customer, I want chatbot answers to reflect the current menu — not stale data — so I can trust what I'm told.
- As a Customer, I want to ask things like "What vegetarian options do you have under $15?" and get a specific, helpful answer rather than a generic response.

---

## Story Count Summary

| Persona | Stories |
|---------|---------|
| Marketing Operator | 21 |
| Field Agent | 9 |
| Admin | 11 |
| Analytics / Growth User | 17 |
| Customer | 13 |
| **Total** | **71** |
