# DabbahWala — Session Plan (2026-02-26)

## Current State
- **628 tests passing**, 62 skipped (live API), 0 failing
- **49% total code coverage**
- Branch: `claude/continue-logging-tests-b9aPf`

---

## Remaining Work

### 1. Increase Code Coverage (Priority: HIGH)

| File | Current | Target | Key Missing Lines |
|------|---------|--------|-------------------|
| `app/routers/query.py` | 26% | 70% | All tier handlers (lines 86-1764) |
| `app/routers/intelligence.py` | 45% | 80% | 5-phase pipeline (lines 63-412) |
| `app/routers/prospects.py` | 35% | 75% | CSV parse, contact upsert (lines 43-412) |
| `app/routers/orders.py` | 40% | 75% | Import pipeline (lines 60-708) |
| `app/routers/campaigns.py` | 44% | 75% | Tracker, sync, template (lines 99-1154) |
| `app/routers/chatbot.py` | 48% | 75% | Doc embed, Q&A pipeline (lines 97-765) |
| `app/routers/daily_orders.py` | 78% | 90% | CSV helpers (lines 42-761) |
| `app/routers/sms.py` | 67% | 85% | Inbound handler, queue (lines 16-254) |
| `app/routers/menu.py` | 62% | 85% | Sync endpoints (lines 63-344) |
| `app/routers/schedules.py` | 69% | 85% | Schedule CRUD (lines 54-208) |
| `app/routers/goal_agent.py` | 81% | 90% | Error branches (lines 88-951) |
| `app/routers/agents.py` | 64% | 80% | Sweep phases (lines 112-2441) |

### 2. Dead Code — Delete 0% Files (Priority: MEDIUM)

These files are superseded by merged versions and have 0% coverage:
- `app/routers/shipday_historical.py` → superseded by `orders.py`
- `app/routers/telnyx.py` → superseded by `sms.py`
- `app/routers/airtable_menu.py` → superseded by `menu.py`
- Also check/delete: `app/services/llm_service.py` (0% if present)

After deleting: remove from `app/main.py` imports.

### 3. Add Logging to Routers Missing It (Priority: MEDIUM)

Files with no or minimal `logger.info/warning/error` calls:
- `app/routers/agent.py` (23% cov, likely missing logging)
- `app/routers/query.py` (large file, needs per-handler logging)
- `app/routers/reports.py` (small but should log calls)
- `app/routers/config.py` (33% cov)

Pattern: `logger = logging.getLogger(__name__)` at top, then log at start/end of each endpoint.

### 4. Infrastructure (Priority: LOW)

- **`scripts/run_tests.sh`** — ordered test runner with sections: unit → n8n-local → live (skipped by default)
- **`pytest.ini`** — register markers: `n8n_live`, `external_connectivity`, `live_dw_api`

### 5. Commit & Push (Ongoing)

Commit after each completed section above. Push to `claude/continue-logging-tests-b9aPf`.
