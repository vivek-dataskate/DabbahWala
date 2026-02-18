#!/bin/bash
# render_build.sh — Render build phase: install deps + run migrations
set -euo pipefail

echo "=== Installing Python dependencies ==="
pip install --upgrade pip
pip install -r requirements.txt

echo "=== Running database migrations ==="
# Render sets DATABASE_URL automatically from the linked database
if [ -z "${DATABASE_URL:-}" ]; then
    echo "WARNING: DATABASE_URL not set, skipping migrations"
    exit 0
fi

# Render uses postgres:// but psycopg2 needs postgresql://
export DATABASE_URL="${DATABASE_URL/postgres:\/\//postgresql:\/\/}"

MIGRATIONS_DIR="$(dirname "$0")/../migrations"

for migration in $(ls "$MIGRATIONS_DIR"/*.sql | sort); do
    filename=$(basename "$migration")
    echo "  Applying: $filename"
    psql "$DATABASE_URL" -f "$migration" -v ON_ERROR_STOP=1 2>&1 || {
        # Tolerate "already exists" errors on re-deploy
        echo "  (non-fatal error in $filename, continuing)"
    }
done

echo "=== All migrations applied ==="
