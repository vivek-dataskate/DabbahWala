# DabbahWala — Session Plan (2026-02-26)

## Current State
- **735 tests passing**, 62 skipped (live API), 0 failing
- **61% total code coverage**
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
| `app/routers/chatbot.py` | 58% | _ensure_indexed, _do_index, _ensure_tables (startup) |
| `app/routers/agents.py` | 64% | Claude AI calls (lines 1789–2441) |
| `app/routers/schedules.py` | 69% | CRUD edge cases (lines 54–108) |
| `app/routers/daily_orders.py` | 78% | CSV format helpers (lines 66–158) |
| `app/routers/goal_agent.py` | 81% | Error branches (lines 590–932) |

### 2. Add Logging to Routers Missing It (Priority: LOW)
- `app/routers/config.py` (missing logger.info on most endpoints)

### 3. Infrastructure Done
- pytest.ini — markers registered
- scripts/run_tests.sh — ordered test runner (all 30+ files)
- Dead code removed: airtable_menu.py, shipday_historical.py, telnyx.py

### 4. Git Push (Next)
Push all commits on `claude/continue-logging-tests-b9aPf` to remote.
