---
description: Review the current branch diff and update SYSTEM.md, FEATURES.md, and CLAUDE.md to reflect any changes. Run this before opening a PR. Triggered by "/update-docs".
---

# Update Docs

Review everything changed on this branch and update the documentation to match.

## Steps

### 1. Get the diff

```bash
git diff main...HEAD --stat
git diff main...HEAD -- '*.py' '*.sql' '*.json' '*.yaml' '*.yml' '*.sh'
```

### 2. Check each doc against the diff

Work through this table. For each row where the diff touched the left column, update the right column.

| If the diff touched… | Update… |
|----------------------|---------|
| `migrations/*.sql` — new table, column, index, or stored proc | **SYSTEM.md** § Database Schema — add/update the table or function row |
| `app/routers/*.py` — new endpoint or changed route | **SYSTEM.md** § API Layer — update the router's endpoint list |
| `routers/agents.py` — agent pipeline logic, guardrails, layers | **SYSTEM.md** § Claude AI Agent Pipeline |
| `routers/intelligence.py` — signal types, phases | **SYSTEM.md** § Intelligence Cycle |
| `n8n/*.json` — workflow added, schedule changed, purpose changed | **SYSTEM.md** § n8n Workflow Layer — update the workflow row |
| `mcp_server/tools/*.py` — new or changed MCP tool | **SYSTEM.md** § MCP Server + **FEATURES.md** § Claude Desktop MCP |
| Any feature file end-to-end (router + migration + n8n + external service) | **FEATURES.md** — update the relevant feature section's assets table |
| New feature with no existing section | **FEATURES.md** — add a new numbered section |
| Migration number consumed | **CLAUDE.md** § Database Migrations — increment "Next available migration number" |
| n8n workflow added or removed | **CLAUDE.md** § n8n Workflow Status — update workflow count and date |

### 3. Apply the updates

Use the Edit tool to make targeted, minimal changes — update only the rows/sections that are actually affected. Do not rewrite sections that didn't change.

### 4. Stage and commit if anything changed

```bash
git add SYSTEM.md FEATURES.md CLAUDE.md
git -c commit.gpgsign=false commit -m "docs: update SYSTEM/FEATURES/CLAUDE to reflect branch changes"
```

If nothing needed updating, say so — no empty commit.

### 5. Report back

List each file changed and the specific section(s) updated, or confirm docs were already up to date.
