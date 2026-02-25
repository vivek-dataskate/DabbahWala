# DabbahWala — Claude Code Instructions

## Git Workflow

After making any code changes:
1. **Ask the user before pushing** — confirm the changes look correct
2. **Handle all git operations** — commit, push, create PR, and merge via GitHub API using `$GITHUB_TOKEN`
3. Always target `main` as the base branch for PRs
5. Branch names must start with `claude/` and end with the session ID suffix

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
- Next available migration number: **056**
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
- GitHub secret `N8N_API_KEY` must be set to the JWT above for `sync_n8n.yml` to work
- Workflow files live in `n8n/` — IDs tracked in `n8n/config.json`

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
- All **22 scheduled workflows** are active as of 2026-02-25
- `[Airtable — Evidence] Menu Sync` (ID: `baZV5ViA5lXNCTWR`) replaced the old Playwright weekly scrape
- Only `[Shipday — Evidence] Historical Import` is intentionally inactive (manual one-shot trigger)
- Credential IDs for all integrations are tracked in `n8n/config.json`

### Instantly
- API key (Bearer): stored in Render env as `INSTANTLY_API_KEY`
- The base64 string `OThjYmE4NjQtMjMwYS00ZGM2LWIzMTgtNWY2YzYxZTZmNDEyOkJVbkNMdkRjVW5zWQ==` is the **Instantly** credential (not n8n)

### GitHub
- Token: `GITHUB_TOKEN` env var — used for PR creation/merge via GitHub API
- Repo: `vivek-dataskate/DabbahWala`
