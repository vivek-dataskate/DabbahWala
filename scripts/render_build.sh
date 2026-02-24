#!/bin/bash
# render_build.sh — Render build phase: install deps + run migrations
set -uo pipefail

echo "=== Installing Python dependencies ==="
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "=== Installing Playwright Chromium browser ==="
playwright install chromium --with-deps || echo "WARNING: Playwright browser install failed (non-fatal)"

echo ""
echo "=== Running database migrations ==="

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL not set — skipping migrations"
    exit 0
fi

# Render uses postgres:// but psycopg2/psql needs postgresql://
export DATABASE_URL="${DATABASE_URL/postgres:\/\//postgresql:\/\/}"

# Confirm psql is available
if ! command -v psql &>/dev/null; then
    echo "ERROR: psql not found in PATH — cannot run migrations"
    exit 1
fi
echo "psql version: $(psql --version)"

# Confirm connectivity
echo "Checking DB connectivity..."
if ! psql "$DATABASE_URL" -c "SELECT 1;" &>/dev/null; then
    echo "ERROR: Cannot connect to database"
    exit 1
fi
echo "DB connection OK"

# Create migration tracking table
echo "Ensuring schema_migrations table exists..."
psql "$DATABASE_URL" <<'SQL'
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
SQL

MIGRATIONS_DIR="$(dirname "$0")/../migrations"
APPLIED=0
SKIPPED=0
FAILED=0

echo ""
echo "--- Migration run starting ---"

for migration in $(ls "$MIGRATIONS_DIR"/*.sql | sort); do
    filename=$(basename "$migration")

    # Check if already applied
    already=$(psql "$DATABASE_URL" -tAc \
        "SELECT COUNT(*) FROM schema_migrations WHERE filename = '$filename';")

    if [ "${already:-0}" -gt 0 ]; then
        echo "  SKIP   $filename (already applied)"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    echo "  APPLY  $filename ..."
    # Capture output; show it regardless of success/failure
    output=$(psql "$DATABASE_URL" -f "$migration" -v ON_ERROR_STOP=1 2>&1)
    exit_code=$?

    if [ $exit_code -eq 0 ]; then
        # Record success
        psql "$DATABASE_URL" -c \
            "INSERT INTO schema_migrations (filename) VALUES ('$filename') ON CONFLICT DO NOTHING;" &>/dev/null
        echo "    OK"
        [ -n "$output" ] && echo "$output" | sed 's/^/    | /'
        APPLIED=$((APPLIED + 1))
    else
        echo "    FAILED (exit $exit_code)"
        echo "$output" | sed 's/^/    | /'
        FAILED=$((FAILED + 1))
        # Don't abort the whole build for a single migration failure —
        # log it clearly so Render's build log shows exactly what broke.
    fi
done

echo ""
echo "--- Migration summary ---"
echo "  Applied : $APPLIED"
echo "  Skipped : $SKIPPED"
echo "  Failed  : $FAILED"

if [ $FAILED -gt 0 ]; then
    echo "WARNING: $FAILED migration(s) failed — see details above"
    # Exit non-zero so Render marks the deploy as failed
    exit 1
fi

echo "=== All migrations applied successfully ==="
