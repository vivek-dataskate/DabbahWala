# DabbahWala — Claude Code Instructions

## Git Workflow

After making any code changes:
1. **Ask the user before pushing** — confirm the changes look correct
2. **Handle all git operations** — commit, push, create PR, and merge via GitHub API using `$GITHUB_TOKEN`
3. Always target `main` as the base branch for PRs
5. Branch names must start with `claude/` and end with the session ID suffix

### GitHub Token
The `GITHUB_TOKEN` is stored in `~/.claude/CLAUDE.md` (global memory, never commit it to the repo).
If the env var is empty, read it from that file and use it directly in API calls.

## Deployment
- Hosted on **Render**, auto-deploys on merge to `main`
- Build command: `scripts/render_build.sh` (installs deps + runs all `migrations/*.sql`)
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

## Database Migrations
- All migrations live in `migrations/` and are numbered sequentially
- Next available migration number: **043**
- Use `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` for idempotency

## Credentials & Integrations

### n8n
- Instance: `https://digitalworker.dataskate.io`
- API key (JWT): `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJkMmFmN2JlMi1hMTYwLTRlZmUtYjFhOC0wMjlmM2U3OWZmMDkiLCJpc3MiOiJuOG4iLCJhdWQiOiJwdWJsaWMtYXBpIiwiaWF0IjoxNzcxNTQ3NzAzfQ.lOtKLp-YEdulBGSOD62uKCPTJBHOl_-0rDy2qa79FqE`
- Header: `X-N8N-API-KEY: <key>`
- When creating/updating workflows via API, only send `{name, nodes, connections, settings}` — n8n rejects `staticData`, `pinData`, `tags`, `meta`
- GitHub secret `N8N_API_KEY` must be set to the JWT above for `sync_n8n.yml` to work
- Workflow files live in `n8n/` — IDs tracked in `n8n/config.json`

### Instantly
- API key (Bearer): stored in Render env as `INSTANTLY_API_KEY`
- The base64 string `OThjYmE4NjQtMjMwYS00ZGM2LWIzMTgtNWY2YzYxZTZmNDEyOkJVbkNMdkRjVW5zWQ==` is the **Instantly** credential (not n8n)

### GitHub
- Token: stored in `$GITHUB_TOKEN` env var (PAT with repo scope) — use for PR creation/merge via GitHub API
- Repo: `vivek-dataskate/DabbahWala`
