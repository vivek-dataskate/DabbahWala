# DabbahWala Marketing System

Automated AI-driven marketing orchestration for DabbahWala — a fresh Indian home-cooked food delivery service in Atlanta.

**→ Full system documentation: [OVERVIEW.md](OVERVIEW.md)**

## Quick Start

```bash
cp .env.example .env
# Fill in DATABASE_URL, ANTHROPIC_API_KEY, TELNYX_API_KEY, etc.
pip install -r requirements.txt
uvicorn app.main:app --reload
```

**Production:** auto-deploys to Render on merge to `main` via `scripts/render_build.sh`.

**n8n:** auto-synced to `digitalworker.dataskate.io` on push to `main` via `.github/workflows/sync_n8n.yml`.

## Key Numbers

| Metric | Value |
|--------|-------|
| API endpoints | 88+ |
| Database tables | 22+ |
| n8n workflows | 26 active-scheduled + 1 manual |
| Claude calls per contact cycle | 8 |
| Lifecycle segments | 8 |
| Instantly campaigns | 6 |
| E2E test groups | 14 |

## Docs

| File | Contents |
|------|---------|
| [OVERVIEW.md](OVERVIEW.md) | Full system reference — architecture, all agents, n8n, API, DB, journeys, how-tos |
| [TESTS.md](TESTS.md) | E2E test registry — all groups, what each test checks, how to add tests |
| [CLAUDE.md](CLAUDE.md) | Git workflow, credentials, migration rules for Claude Code sessions |
