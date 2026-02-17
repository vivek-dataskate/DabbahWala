#!/bin/bash
# run_migrations.sh — Apply all SQL migrations in order
# Usage: ./scripts/run_migrations.sh [DATABASE_URL]
# Example: ./scripts/run_migrations.sh postgresql://user:pass@localhost:5432/dabbahwala

set -euo pipefail

DB_URL="${1:-${DATABASE_URL:-}}"

if [ -z "$DB_URL" ]; then
    echo "Usage: $0 <database_url>"
    echo "Or set DATABASE_URL environment variable"
    exit 1
fi

MIGRATIONS_DIR="$(dirname "$0")/../migrations"

echo "Running migrations against: ${DB_URL%%@*}@***"
echo "---"

for migration in $(ls "$MIGRATIONS_DIR"/*.sql | sort); do
    filename=$(basename "$migration")
    echo "Applying: $filename"
    psql "$DB_URL" -f "$migration" -v ON_ERROR_STOP=1
    echo "  Done."
done

echo "---"
echo "All migrations applied successfully."
