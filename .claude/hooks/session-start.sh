#!/bin/bash
# SessionStart hook — restores ~/.claude/CLAUDE.md with global session context.
# Runs automatically when Claude Code starts a session in this repo.
#
# REQUIRED env vars (set these once in your shell or Claude Code settings):
#   GITHUB_TOKEN      — GitHub PAT with repo access
#   AIRTABLE_API_KEY  — Airtable personal access token
#   INSTANTLY_API_KEY — Instantly API key (base64 cred)

set -euo pipefail

mkdir -p ~/.claude

cat > ~/.claude/CLAUDE.md << EOF
# Global Session Context — DabbahWala

## Repositories
| Repo | Purpose |
|---|---|
| \`vivek-dataskate/DabbahWala\` | **Primary backend** — Python FastAPI, Postgres, n8n, MCP server, agent pipeline |
| \`DataSkateCOE/dabbahwala-ui\` | Next.js frontend (customer-facing + admin) |

## Git Workflow
- **Token:** \`${GITHUB_TOKEN:-NOT_SET}\` (also used as \`GITHUB_TOKEN\`)
- After every commit: ask for permission before opening PR, merging to main, and deleting branch.
- Multiple sessions may be running concurrently — always \`git fetch\` + rebase before pushing.
- Branch naming: \`claude/<short-description>-<session-id>\`
- On every sync with main: update \`ARCHITECTURE.md\`, \`README.md\`, and docs to reflect changes.
- Backend auto-deploys to Render on merge to main.

## API Keys
- **n8n:** \`eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJkMmFmN2JlMi1hMTYwLTRlZmUtYjFhOC0wMjlmM2U3OWZmMDkiLCJpc3MiOiJuOG4iLCJhdWQiOiJwdWJsaWMtYXBpIiwiaWF0IjoxNzcxNTQ3NzAzfQ.lOtKLp-YEdulBGSOD62uKCPTJBHOl_-0rDy2qa79FqE\`
  - Instance: \`https://digitalworker.dataskate.io\`
  - Header: \`X-N8N-API-KEY: <key>\`
- **Airtable:** \`${AIRTABLE_API_KEY:-NOT_SET}\`
  - Base ID: \`appuy2VTIao6XVpIW\`
- **Instantly:** \`${INSTANTLY_API_KEY:-NOT_SET}\`

## Architecture Rules
- **MCP** = Claude Desktop querying Postgres or calling external services (read/write data)
- **Sub-agents** = parallel/isolated tasks (codebase reads, schema planning, n8n analysis)

## Backend — vivek-dataskate/DabbahWala
- **Runtime:** Python FastAPI on Render (\`dabbahwala-latest.onrender.com\`)
- **DB:** PostgreSQL 16 on Render
- **Build:** \`scripts/render_build.sh\` → installs deps → runs all \`migrations/*.sql\` → runs tests
- **Start:** \`uvicorn app.main:app --host 0.0.0.0 --port \$PORT\`
- **Next migration number: 056**
- **Claude model in use:** \`claude-sonnet-4-5-20250929\`
- All **22 n8n workflows active** (except \`[Shipday — Evidence] Historical Import\` — manual only)
- Migrations must use \`CREATE TABLE IF NOT EXISTS\` / \`CREATE INDEX IF NOT EXISTS\` (idempotent)

## MCP Server (mcp_server/)
Exposes PostgreSQL marketing data to Claude Desktop. Tool groups: contacts, analytics,
communications, recommendations, opportunities, agents.
MCP tools query Postgres via the FastAPI app — they do NOT call external APIs directly.

## n8n Credential IDs
- Gmail-SMTP: \`Sk6XzPNPnJTXHEbr\`
- Google Drive OAuth2: \`LUu1v42BgnEflv6f\`
- Google Docs OAuth2: \`FcNSuTgdmTt3M4D5\`
- Reports sent to: \`REPORT_EMAIL_TO\` env var (default \`core@dabbahwala.com\`)
- Telnyx from number: \`+18444322224\`
EOF

echo "~/.claude/CLAUDE.md restored."

# Warn if required secrets are missing
missing=()
[[ "${GITHUB_TOKEN:-}" == "" ]] && missing+=("GITHUB_TOKEN")
[[ "${AIRTABLE_API_KEY:-}" == "" ]] && missing+=("AIRTABLE_API_KEY")
[[ "${INSTANTLY_API_KEY:-}" == "" ]] && missing+=("INSTANTLY_API_KEY")

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "WARNING: The following env vars are not set — update ~/.claude/CLAUDE.md manually:" >&2
  for v in "${missing[@]}"; do echo "  - $v" >&2; done
fi
