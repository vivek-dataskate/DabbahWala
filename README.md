# DabbahWala Marketing System

Automated, AI-driven marketing orchestration for DabbahWala — a fresh Indian food delivery service in Atlanta.

## Docs

| Document | Contents |
|----------|---------|
| [LIFECYCLE.md](LIFECYCLE.md) | **Start here** — plain-language explanation of how the lifecycle engine, intelligence engine, and AI pipeline work together to convert customers. Includes the customer journey, opportunity lifecycle, feedback loop, and why all three engines are necessary. |
| [SYSTEM.md](SYSTEM.md) | Full technical reference — stack, schema, API layer, agent pipeline, n8n workflows, deployment |
| [FEATURES.md](FEATURES.md) | Business features and the assets (code, DB tables, workflows) that power each one |
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
      └─ 10-category marketing query interface
      │
      ▼
  n8n  (digitalworker.dataskate.io) — 22 active workflows
      ├─ Action Queue Executor → Telnyx / Instantly / Airtable
      ├─ Broadcast Dispatch → SMS / email
      └─ Reporting → daily HTML + CSV emails
```

## Key Numbers

| Metric | Value |
|--------|-------|
| API endpoints | 85+ |
| Database tables | 21+ |
| Migrations | 055 (next: 056) |
| n8n workflows | 22 active |
| Claude calls per contact cycle | 8 |
| Lifecycle segments | 8 |
| Instantly campaigns | 5 |
| MCP tools (Claude Desktop) | 30+ |
