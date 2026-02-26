# DabbahWala — Session Plan (2026-02-26)

## Current State
- **857 tests passing**, 62 skipped (live API), 0 failing
- **67% total code coverage**
- Branch: `claude/continue-logging-tests-b9aPf`

---

## Remaining Work

### 1. Increase Code Coverage (Priority: MEDIUM)

These files still have coverage gaps — remaining uncovered code is mostly in
background tasks calling external APIs (Instantly, Shipday, Airtable) or Claude:

| File | Current | Main Uncovered Area |
|------|---------|---------------------|
| `app/routers/campaigns.py` | 47% | Instantly push sync (lines 857–1154) |
| `app/routers/orders.py` | 43% | _run_historical_sync calls Shipday API (lines 60–149) |
| `app/routers/chatbot.py` | 64% | _ensure_indexed, _do_index, _ensure_tables (startup) |
| `app/routers/agents.py` | 72% | Claude AI calls in observer/advisor/orchestrator |
| `app/routers/daily_orders.py` | 81% | Complex CSV parsing paths |

### 2. Infrastructure Done
- pytest.ini — markers registered
- scripts/run_tests.sh — ordered test runner (32 files)
- Dead code removed: airtable_menu.py, shipday_historical.py, telnyx.py
- test_config.py — 100% config.py coverage
- test_agent.py — 92% agent.py coverage

### 3. Git Push (Next)
Push all commits on `claude/continue-logging-tests-b9aPf` to remote.
