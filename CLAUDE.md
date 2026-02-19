# DabbahWala — Claude Code Instructions

## Git Workflow

After making any code changes:
1. **Ask the user before pushing** — confirm the changes look correct
2. **Handle all git operations** — commit, push, create PR, and merge via GitHub API using `$GITHUB_TOKEN`
3. Always target `main` as the base branch for PRs
5. Branch names must start with `claude/` and end with the session ID suffix

## Deployment
- Hosted on **Render**, auto-deploys on merge to `main`
- Build command: `scripts/render_build.sh` (installs deps + runs all `migrations/*.sql`)
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

## Database Migrations
- All migrations live in `migrations/` and are numbered sequentially (e.g. `039_*.sql`)
- Next available migration number: **040**
- Use `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` for idempotency
