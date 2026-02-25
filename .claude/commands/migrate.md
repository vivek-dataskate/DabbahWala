---
description: Create a new numbered database migration for DabbahWala. Use when the user says "create migration", "add migration", "new migration", "add table", "alter table", or describes a schema change.
---

# DabbahWala Database Migration

Create the next sequential migration file in `migrations/`.

## Workflow

1. **Get the next number** — read `CLAUDE.md` in the project root. Find the line:
   ```
   - Next available migration number: **NNN**
   ```
   Use that number (zero-padded to 3 digits, e.g. `056`).

2. **Determine a short snake_case name** from the user's description (e.g. `add_contact_tags`, `weekly_menu_schedule`, `fix_rollup_index`).

3. **Create the file** at `migrations/NNN_name.sql`.

4. **Write the SQL** following these rules:
   - Always use `CREATE TABLE IF NOT EXISTS`
   - Always use `CREATE INDEX IF NOT EXISTS`
   - Always use `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` (Postgres 9.6+)
   - Target schema: `dabbahwala` (all tables live here)
   - Add a comment header:
     ```sql
     -- Migration NNN: short description
     -- Created: YYYY-MM-DD
     ```

5. **Update CLAUDE.md** — increment the next migration number by 1.

6. **Show** the full migration file content to the user for review before committing.

## Example migration file

```sql
-- Migration 056: add contact tags table
-- Created: 2026-02-25

CREATE TABLE IF NOT EXISTS dabbahwala.contact_tags (
    id          SERIAL PRIMARY KEY,
    contact_id  INTEGER NOT NULL REFERENCES dabbahwala.contacts(id) ON DELETE CASCADE,
    tag         VARCHAR(64) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (contact_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_contact_tags_contact_id ON dabbahwala.contact_tags(contact_id);
```

## Notes

- Migrations run automatically on Render deploy via `scripts/render_build.sh`
- They also run via `POST /admin/migrate/{num}` endpoint (requires `ADMIN_SECRET`)
- Never modify existing migration files — always create a new one
