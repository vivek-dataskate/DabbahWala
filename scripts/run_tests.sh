#!/bin/bash
# run_tests.sh — DabbahWala unit & integration test runner
#
# Usage:
#   ./scripts/run_tests.sh                  # all unit tests (mocked DB)
#   ./scripts/run_tests.sh --live           # include live n8n API tests
#   ./scripts/run_tests.sh --e2e            # also run the in-process E2E harness
#   ./scripts/run_tests.sh --file test_sms  # run a single test file
#   ./scripts/run_tests.sh --help
#
# Exit codes:
#   0  All tests passed
#   1  One or more tests failed
#
# Environment variables (optional):
#   N8N_API_KEY      — enable live n8n workflow verification
#   ANTHROPIC_API_KEY — enable Claude integration tests
#   DATABASE_URL     — use real Postgres (default: mocked in unit tests)
#   PORT             — FastAPI port for E2E harness (default: 8000)
#   LOG_LEVEL        — logging verbosity (default: WARNING during tests)

set -euo pipefail

# ─── Configuration ────────────────────────────────────────────────────────────

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TESTS_DIR="$REPO_ROOT/tests"
PYTHON="${PYTHON:-python3}"

# Colour codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ─── Argument parsing ─────────────────────────────────────────────────────────

RUN_LIVE=false
RUN_E2E=false
SINGLE_FILE=""
VERBOSE=false
EXTRA_PYTEST_ARGS=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --live)   RUN_LIVE=true ;;
        --e2e)    RUN_E2E=true ;;
        --verbose|-v) VERBOSE=true ;;
        --file)   SINGLE_FILE="$2"; shift ;;
        --help|-h)
            echo "Usage: $0 [--live] [--e2e] [--verbose] [--file <test_file>]"
            echo ""
            echo "  --live        Include live n8n API verification (requires N8N_API_KEY)"
            echo "  --e2e         Run in-process E2E harness via POST /api/test/run"
            echo "  --verbose     Pass -v to pytest"
            echo "  --file FILE   Run only tests/FILE.py (e.g. --file test_sms)"
            exit 0
            ;;
        *) EXTRA_PYTEST_ARGS="$EXTRA_PYTEST_ARGS $1" ;;
    esac
    shift
done

# ─── Helpers ─────────────────────────────────────────────────────────────────

log() { echo -e "${BLUE}[run_tests]${NC} $*"; }
ok()  { echo -e "${GREEN}✓${NC} $*"; }
err() { echo -e "${RED}✗${NC} $*"; }
warn(){ echo -e "${YELLOW}⚠${NC} $*"; }

check_python() {
    if ! command -v "$PYTHON" &>/dev/null; then
        err "Python not found: $PYTHON"
        exit 1
    fi
    log "Python: $($PYTHON --version)"
}

check_pytest() {
    if ! "$PYTHON" -m pytest --version &>/dev/null 2>&1; then
        err "pytest not installed. Run: pip install pytest"
        exit 1
    fi
    log "pytest: $($PYTHON -m pytest --version | head -1)"
}

# ─── Test order (dependency-aware) ───────────────────────────────────────────
#
# Tests are ordered so that foundational tests run first.  All tests are
# independent (each creates and cleans its own mock data), so order only
# affects readability of the output, not correctness.

ORDERED_TEST_FILES=(
    # Layer 1 — Infrastructure
    "test_events"
    "test_delivery"
    "test_lifecycle"
    "test_contacts"

    # Layer 2 — Data / ingestion
    "test_orders"
    "test_sms"
    "test_menu"
    "test_prospects"

    # Layer 3 — Campaign / Opportunity engine
    "test_campaigns"
    "test_opportunities"
    "test_webhooks"
    "test_intelligence"

    # Layer 4 — Agent pipeline
    "test_agents"
    "test_goal_agent"
    "test_growth_agent"
    "test_competitor_agent"
    "test_field_agent"

    # Layer 5 — Content / reporting
    "test_broadcasts"
    "test_reports"
    "test_team_content"
    "test_chatbot"

    # Layer 6 — Self-service / config
    "test_playbook"
    "test_query"
    "test_schedules"

    # Layer 7 — Shipday sync (has its own detailed unit tests)
    "test_shipday_sync"

    # Layer 8 — n8n workflow config + seam tests
    "test_n8n_workflows"
)

# ─── Main ─────────────────────────────────────────────────────────────────────

main() {
    check_python
    check_pytest

    cd "$REPO_ROOT"

    # Silence chatbot startup noise during tests
    export LOG_LEVEL="${LOG_LEVEL:-WARNING}"

    # Build pytest args
    PYTEST_ARGS="--tb=short -q"
    if [ "$VERBOSE" = true ]; then
        PYTEST_ARGS="--tb=short -v"
    fi

    # Exclude live n8n tests unless --live was passed
    if [ "$RUN_LIVE" = false ]; then
        PYTEST_ARGS="$PYTEST_ARGS -m 'not n8n_live'"
    else
        if [ -z "${N8N_API_KEY:-}" ]; then
            warn "--live requested but N8N_API_KEY is not set; live tests will be skipped"
        fi
    fi

    PYTEST_ARGS="$PYTEST_ARGS $EXTRA_PYTEST_ARGS"

    # ── Run unit tests ────────────────────────────────────────────────────────
    log "Starting DabbahWala test run"
    log "Tests directory: $TESTS_DIR"
    echo ""

    PASS=0
    FAIL=0
    SKIP=0

    if [ -n "$SINGLE_FILE" ]; then
        # Single-file mode
        FILE_PATH="$TESTS_DIR/${SINGLE_FILE%.py}.py"
        if [ ! -f "$FILE_PATH" ]; then
            err "Test file not found: $FILE_PATH"
            exit 1
        fi
        log "Running single file: $SINGLE_FILE"
        if $PYTHON -m pytest "$FILE_PATH" $PYTEST_ARGS; then
            ok "$SINGLE_FILE passed"
            PASS=1
        else
            err "$SINGLE_FILE FAILED"
            FAIL=1
        fi
    else
        # Run all files in defined order
        for test_name in "${ORDERED_TEST_FILES[@]}"; do
            FILE_PATH="$TESTS_DIR/${test_name}.py"
            if [ ! -f "$FILE_PATH" ]; then
                warn "SKIP (file not found): $test_name.py"
                SKIP=$((SKIP + 1))
                continue
            fi

            printf "  %-40s " "$test_name"
            if $PYTHON -m pytest "$FILE_PATH" $PYTEST_ARGS --no-header -q 2>&1 | tail -1 | grep -q "passed"; then
                output=$($PYTHON -m pytest "$FILE_PATH" $PYTEST_ARGS --no-header -q 2>&1 | tail -1)
                echo -e "${GREEN}✓${NC} $output"
                PASS=$((PASS + 1))
            else
                echo ""
                # Re-run with output to show failures
                $PYTHON -m pytest "$FILE_PATH" $PYTEST_ARGS --no-header 2>&1 || true
                err "$test_name FAILED"
                FAIL=$((FAIL + 1))
            fi
        done
    fi

    echo ""
    echo "─────────────────────────────────────────────────"
    echo -e "  Files:  ${GREEN}$PASS passed${NC}  ${RED}$FAIL failed${NC}  ${YELLOW}$SKIP skipped${NC}"
    echo ""

    # ── E2E harness (optional) ────────────────────────────────────────────────
    if [ "$RUN_E2E" = true ]; then
        echo ""
        log "Running E2E test harness via Python (in-process)"
        if $PYTHON -c "
import os, sys, json
os.environ.setdefault('PORT', '8000')
from app.services.test_harness_service import run_full_suite
suite = run_full_suite(triggered_by='run_tests_sh')
s = suite.summary()
print(json.dumps(s, indent=2))
sys.exit(0 if s['failed'] == 0 else 1)
"; then
            ok "E2E harness passed"
        else
            err "E2E harness FAILED"
            FAIL=$((FAIL + 1))
        fi
    fi

    # ── Final result ──────────────────────────────────────────────────────────
    if [ "$FAIL" -eq 0 ]; then
        echo -e "${GREEN}✓ All tests passed${NC}"
        exit 0
    else
        echo -e "${RED}✗ $FAIL test file(s) failed${NC}"
        exit 1
    fi
}

main "$@"
