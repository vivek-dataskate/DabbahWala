# DabbahWala — Claude Code Instructions

## Git Workflow

After making any code changes:
1. **Ask the user before pushing** — confirm the changes look correct
2. **Handle all git operations** — commit, push, create PR, and merge via GitHub API using `$GITHUB_TOKEN`
3. Always target `main` as the base branch for PRs
5. Branch names must start with `claude/` and end with the session ID suffix

### E2E Test Update (mandatory on every commit)

**Every commit that adds or modifies a feature must also:**
1. Add the corresponding test(s) in `app/services/test_harness_service.py` inside the appropriate `_gN_*` function
2. Register those tests in `TESTS.md` under the correct group table

Use the **Group Selection Guide** in `TESTS.md` to pick the right group. If no existing group fits, create a new `_gN_*` function, add its call to `run_full_suite()`, and add a new section to `TESTS.md`.

This applies to **every** commit — including bug fixes, new endpoints, schema changes, n8n workflow additions, and integration changes.

### Pre-PR Doc Check (mandatory)

Before opening any PR, review the branch diff (`git diff main...HEAD`) and update docs if affected:

| Changed | Update |
|---------|--------|
| Schema, stored procs, migrations | `SYSTEM.md` § Database Schema |
| New/modified API routes or routers | `SYSTEM.md` § API Layer |
| Agent pipeline logic | `SYSTEM.md` § Claude AI Agent Pipeline |
| n8n workflow added/changed | `SYSTEM.md` § n8n Workflow Layer + `FEATURES.md` relevant feature |
| External service integration | `SYSTEM.md` § External Service Integrations |
| Feature added or changed end-to-end | `FEATURES.md` — add/update the feature section |
| Next migration number used | `CLAUDE.md` § Database Migrations (increment the number) |
| n8n workflow count changed | `CLAUDE.md` § n8n Workflow Status |
| Any new/modified feature (always) | `TESTS.md` + `app/services/test_harness_service.py` |

If nothing in those categories changed, docs are fine as-is — no update needed.
Commit doc updates in the same branch before opening the PR.
Use `/update-docs` to run the check interactively.

### GitHub Token
The `GITHUB_TOKEN` is stored in `~/.claude/CLAUDE.md` (global memory, never commit it to the repo).
If the env var is empty, read it from that file and use it directly in API calls.

## Deployment
- Hosted on **Render**, auto-deploys on merge to `main`
- Build command: `scripts/render_build.sh` (installs deps + runs all `migrations/*.sql`)
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

## Database Migrations
- All migrations live in `migrations/` and are numbered sequentially
- Next available migration number: **059**
- Use `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` for idempotency

## Credentials & Integrations

### n8n
- Instance: `https://digitalworker.dataskate.io`
- API key (JWT): `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJkMmFmN2JlMi1hMTYwLTRlZmUtYjFhOC0wMjlmM2U3OWZmMDkiLCJpc3MiOiJuOG4iLCJhdWQiOiJwdWJsaWMtYXBpIiwiaWF0IjoxNzcxNTQ3NzAzfQ.lOtKLp-YEdulBGSOD62uKCPTJBHOl_-0rDy2qa79FqE`
- Header: `X-N8N-API-KEY: <key>`
- When creating/updating workflows via API, only send `{name, nodes, connections, settings}` — n8n rejects `staticData`, `pinData`, `tags`, `meta`
- To **activate** a workflow: `POST /api/v1/workflows/{id}/activate` (PATCH is not allowed on this instance)
- To **deactivate** a workflow: `POST /api/v1/workflows/{id}/deactivate`
- Credentials resolve by **name** on first push — use the exact credential name and n8n will auto-match and return the real ID
- Workflow files live in `n8n/` — IDs tracked in `n8n/config.json`
- **⚠️ n8n Variables / environment variables are NOT available on this instance** — never use `$env.ANYTHING` in workflow nodes. Hardcode all values (URLs, phone numbers, static config) directly in the node parameters. This applies to every workflow, past and future.

### Gmail / SMTP
- Credential name in n8n: **`Gmail-SMTP`** (credential ID: `Sk6XzPNPnJTXHEbr`)
- Used by: `action_queue_executor` (`send_email_report` action), `broadcast_dispatch` (email channel)
- Host: `smtp.gmail.com`, Port: 465, SSL on
- Reports sent to: `REPORT_EMAIL_TO` env var (default `core@dabbahwala.com`)

### Google OAuth
- **Google Drive OAuth2** — n8n credential ID: `LUu1v42BgnEflv6f`
  - Used by: `action_queue_executor` (`upload_google_drive` action), `google_docs_sync`
- **Google Docs OAuth2** — n8n credential ID: `FcNSuTgdmTt3M4D5`
  - Used by: `google_docs_sync` (reads doc content for chatbot index)
- Drive folder for chatbot docs: `1O0ES9uiDL6AWf9QMMYiyRUWGtymDjPF5`

### Airtable
- API key: stored in Render env as `AIRTABLE_API_KEY` and in `~/.claude/CLAUDE.md` (never commit to repo)
- Base ID: `appuy2VTIao6XVpIW`
- Menu table: **`Weekly Menu`** — fields: Name, Category, Is Veg, Description, Image URL, Week Start, Active, Price

### n8n Workflow Status
- All **24 scheduled workflows** are active as of 2026-02-25
- `[Claude — Inference] Competitor Research Agent` (ID: TBD — activate after first push) added 2026-02-25
- `[Airtable — Evidence] Menu Sync` (ID: `baZV5ViA5lXNCTWR`) replaced the old Playwright weekly scrape
- `[System — Test] Daily E2E Test Suite` (ID: `M7bwNMGrUMRvAHH4`) — daily 5 AM E2E test runner, added 2026-02-25
- Only `[Shipday — Evidence] Historical Import` is intentionally inactive (manual one-shot trigger)
- Credential IDs for all integrations are tracked in `n8n/config.json`

### Instantly
- API key (Bearer): stored in Render env as `INSTANTLY_API_KEY`
- The base64 string `OThjYmE4NjQtMjMwYS00ZGM2LWIzMTgtNWY2YzYxZTZmNDEyOkJVbkNMdkRjVW5zWQ==` is the **Instantly** credential (not n8n)

### GitHub
- Token: `GITHUB_TOKEN` env var — used for PR creation/merge via GitHub API
- Repo: `vivek-dataskate/DabbahWala`
