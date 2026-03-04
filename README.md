# DabbahWala Marketing System

Automated, AI-driven marketing orchestration for DabbahWala — a fresh Indian food delivery service in Atlanta.

## Docs

| Document | Contents |
|----------|---------|
| [SYSTEM.md](SYSTEM.md) | **Start here** — full technical reference: stack, schema, API layer, three-engine architecture, agent pipeline, n8n workflows, deployment |
| [GUIDE.md](GUIDE.md) | Operator's guide — daily checklist, customer journeys, feature how-tos, troubleshooting |
| [TESTS.md](TESTS.md) | E2E test registry — all test groups, what each test checks, how to add new tests |
| [CLAUDE.md](CLAUDE.md) | Git workflow, credentials, deployment notes for Claude Code sessions |

## Quick Start

```bash
cp .env.example .env
# Fill in DATABASE_URL, ANTHROPIC_API_KEY, TELNYX_API_KEY, etc.
pip install -r requirements.txt
uvicorn app.main:app --reload
```

**Production:** auto-deploys to Render on merge to `main` via `scripts/render_build.sh` (installs deps + runs all `migrations/*.sql`).

**n8n:** auto-synced to `digitalworker.dataskate.io` on push to `main` by `.github/workflows/sync_n8n.yml`.

## At a Glance

```
  Events (Telnyx / Shipday / Instantly / CSV orders)
      │
      ▼
  FastAPI  (dabbahwala-latest.onrender.com)
      ├─ 4-layer Claude agent pipeline → action_queue
      ├─ 5-phase intelligence cycle → opportunities
      └─ 14-category marketing query interface
      │
      ▼
  n8n  (digitalworker.dataskate.io) — 26 active workflows
      ├─ Action Queue Executor → Telnyx / Instantly / Airtable
      ├─ Broadcast Dispatch → SMS / email
      └─ Reporting → daily HTML + CSV emails
```

## Key Numbers

| Metric | Value |
|--------|-------|
| API endpoints | 88+ |
| Database tables | 22+ |
| Migrations | 9 files (5 consolidated + 4 additive; next: 008) |
| n8n workflows | 26 active-scheduled + 1 manual |
| Claude calls per contact cycle | 8 |
| Lifecycle segments | 8 |
| Instantly campaigns | 5 active lifecycle campaigns |
| MCP tools (Claude Desktop) | 35+ |
