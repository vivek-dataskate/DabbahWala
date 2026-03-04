"""
Tests for app/routers/goal_agent.py
=====================================

Router prefix: /api/goal-agent

Covers:
  - POST /run
  - POST /hypothesize
  - POST /experiment
  - POST /measure
  - POST /harvest
  - GET  /experiments
  - GET  /signals
  - GET  /runs

All DB calls are mocked via patch on app.routers.goal_agent.get_cursor.
All Claude calls are mocked via patch on anthropic.Anthropic.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cursor(rows=None, fetchone_val=None):
    c = MagicMock()
    c.fetchall.return_value = rows or []
    c.fetchone.return_value = fetchone_val
    return c


@contextmanager
def _cursor_ctx(cur):
    yield cur


def _make_claude_mock(tool_input: dict):
    """Return a mock anthropic.Anthropic instance whose messages.create returns a tool_use block."""
    block = MagicMock()
    block.type = "tool_use"
    block.input = tool_input

    response = MagicMock()
    response.content = [block]
    response.stop_reason = "tool_use"
    response.usage = MagicMock()
    response.usage.input_tokens = 200
    response.usage.output_tokens = 100

    client_instance = MagicMock()
    client_instance.messages.create.return_value = response
    return client_instance


# ---------------------------------------------------------------------------
# TestGoalAgentHypothesizeOnly
# ---------------------------------------------------------------------------

class TestHypothesizeOnly:
    """Tests for POST /hypothesize."""

    def test_hypothesize_stores_hypotheses(self, client, monkeypatch):
        """POST /hypothesize calls Claude tool and stores returned hypotheses in DB; returns GoalAgentResult."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        hypothesis_input = {
            "hypotheses": [
                {
                    "hypothesis": "Lapsed 2x orderers respond better to nostalgia than discount",
                    "experiment_type": "cohort_message",
                    "cohort_description": "Contacts with 2+ orders, last order 21-35 days ago",
                    "cohort_filter": {"min_orders": 2, "days_since_last_order_min": 21},
                    "message_template": "Hi {first_name}, it's been a few weeks — DabbahWala misses you!",
                    "success_threshold": 0.10,
                    "rationale": "Nostalgia is untested for this cohort.",
                }
            ],
            "overall_reasoning": "Targeting lapsed loyal customers is highest-leverage.",
        }

        # _get_system_snapshot: fetchall x5, fetchone x4
        # _save_experiments INSERT RETURNING id: fetchone x1
        # _log_run INSERT: fetchone x1 (no result)
        cur = MagicMock()
        cur.fetchall.side_effect = [
            [{"lifecycle_segment": "active", "cnt": 100}],  # segments
            [],   # recent_experiments
            [],   # active_signals
            [],   # top_items
            [],   # orders_by_dow
        ]
        cur.fetchone.side_effect = [
            {"orders_30d": 50, "ordering_customers_30d": 40,
             "avg_order_value": 35.0, "total_contacts": 500},  # order_stats
            {"ordered_7d": 25},    # ordered_7d
            {"lapsed_count": 80},  # lapsed_count
            {"never_ordered": 120},  # never_ordered
            {"id": 10},            # INSERT goal_experiments RETURNING id
            None,                  # _log_run
        ]

        claude_mock = _make_claude_mock(hypothesis_input)

        with patch("app.routers.goal_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("anthropic.Anthropic", return_value=claude_mock):
            resp = client.post("/api/goal-agent/hypothesize")

        assert resp.status_code == 200
        data = resp.json()
        assert data["phase"] == "hypothesize"
        assert data["experiments_created"] >= 1
        assert "timestamp" in data

    def test_hypothesize_returns_zero_when_claude_returns_nothing(self, client, monkeypatch):
        """If Claude returns empty content, hypothesize phase returns 0 experiments_created."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        cur = MagicMock()
        cur.fetchall.side_effect = [
            [{"lifecycle_segment": "active", "cnt": 50}],
            [], [], [], [],
        ]
        cur.fetchone.side_effect = [
            {"orders_30d": 10, "ordering_customers_30d": 8,
             "avg_order_value": 30.0, "total_contacts": 200},
            {"ordered_7d": 5},
            {"lapsed_count": 30},
            {"never_ordered": 60},
            None,  # _log_run
        ]

        # Claude returns empty content — no tool_use block
        empty_resp = MagicMock()
        empty_resp.content = []
        mock_client_instance = MagicMock()
        mock_client_instance.messages.create.return_value = empty_resp

        with patch("app.routers.goal_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("anthropic.Anthropic", return_value=mock_client_instance):
            resp = client.post("/api/goal-agent/hypothesize")

        assert resp.status_code == 200
        assert resp.json()["experiments_created"] == 0

    def test_hypothesize_no_api_key_returns_500(self, client, monkeypatch):
        """POST /hypothesize without ANTHROPIC_API_KEY returns 500."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        cur = _make_cursor(rows=[], fetchone_val=None)

        with patch("app.routers.goal_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/goal-agent/hypothesize")

        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# TestExperimentOnly
# ---------------------------------------------------------------------------

class TestExperimentOnly:
    """Tests for POST /experiment."""

    def test_experiment_enrolls_contacts(self, client, monkeypatch):
        """POST /experiment picks pending experiments, builds cohort SQL, creates action_queue rows."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        pending_exp = {
            "id": 5,
            "hypothesis": "Lapsed 2x orderers respond to nostalgia",
            "experiment_type": "cohort_message",
            "cohort_description": "2+ orders, 21-35 days lapsed",
            "cohort_filter": {"min_orders": 2},
            "message_template": "Hi {first_name}, we miss you!",
            "success_threshold": 0.10,
        }
        cohort_build_input = {
            "cohort_sql": "SELECT id AS contact_id, first_name, phone FROM contacts WHERE total_orders >= 2 LIMIT 20",
            "message_template": "Hi {first_name}, we miss you at DabbahWala!",
            "cohort_logic_explanation": "Contacts with 2+ orders.",
        }
        contact_rows = [
            {"contact_id": 1, "first_name": "Alice", "phone": "+14041111111"},
            {"contact_id": 2, "first_name": "Bob", "phone": "+14042222222"},
        ]

        cur = MagicMock()
        cur.fetchall.side_effect = [
            [],               # _get_recently_contacted_contact_ids
            [],               # _get_active_experiment_contact_ids
            [pending_exp],    # _get_pending_experiments
            contact_rows,     # _execute_cohort_sql
        ]
        cur.fetchone.side_effect = [
            {"id": 201},   # action_queue INSERT RETURNING id for contact 1
            {"id": 202},   # action_queue INSERT RETURNING id for contact 2
            None,          # _log_run
        ]

        claude_mock = _make_claude_mock(cohort_build_input)

        with patch("app.routers.goal_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("anthropic.Anthropic", return_value=claude_mock):
            resp = client.post("/api/goal-agent/experiment")

        assert resp.status_code == 200
        data = resp.json()
        assert data["phase"] == "experiment"
        assert data["experiments_started"] >= 1
        assert data["contacts_enrolled"] >= 1

    def test_experiment_no_pending_returns_zero(self, client, monkeypatch):
        """POST /experiment with no pending experiments returns 0 started and 0 enrolled."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        cur = MagicMock()
        cur.fetchall.side_effect = [
            [],  # _get_recently_contacted_contact_ids
            [],  # _get_active_experiment_contact_ids
            [],  # _get_pending_experiments — empty
        ]
        cur.fetchone.return_value = None  # _log_run

        mock_client_instance = MagicMock()

        with patch("app.routers.goal_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("anthropic.Anthropic", return_value=mock_client_instance):
            resp = client.post("/api/goal-agent/experiment")

        assert resp.status_code == 200
        data = resp.json()
        assert data["experiments_started"] == 0
        assert data["contacts_enrolled"] == 0


# ---------------------------------------------------------------------------
# TestMeasureExperiments
# ---------------------------------------------------------------------------

class TestMeasureExperiments:
    """Tests for POST /measure."""

    def test_measure_no_due_experiments(self, client, monkeypatch):
        """POST /measure with no experiments due returns experiments_concluded:0."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        cur = _make_cursor(rows=[])

        with patch("app.routers.goal_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/goal-agent/measure")

        assert resp.status_code == 200
        data = resp.json()
        assert data["experiments_concluded"] == 0

    def test_measure_with_due_experiment(self, client, monkeypatch):
        """POST /measure with one due experiment → experiments_concluded:1."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        phase_result = {"experiments_concluded": 1, "orders_attributed": 3}
        log_cur = _make_cursor()

        with patch("app.routers.goal_agent._phase_measure", return_value=phase_result), \
             patch("app.routers.goal_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(log_cur)), \
             patch("anthropic.Anthropic"):
            resp = client.post("/api/goal-agent/measure")

        assert resp.status_code == 200
        data = resp.json()
        assert data["experiments_concluded"] == 1
        assert data["orders_attributed"] == 3


# ---------------------------------------------------------------------------
# TestHarvestOnly
# ---------------------------------------------------------------------------

class TestHarvestOnly:
    """Tests for POST /harvest."""

    def test_harvest_creates_signals(self, client, monkeypatch):
        """POST /harvest turns proven experiments into discovered_signals entries."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        proven_exp = {
            "id": 3,
            "hypothesis": "Lapsed 2x orderers respond to nostalgia",
            "experiment_type": "cohort_message",
            "cohort_description": "2+ orders, lapsed 21-35 days",
            "cohort_sql": "SELECT id AS contact_id FROM contacts WHERE total_orders >= 2 LIMIT 20",
            "result_conversion_rate": 0.15,
            "result_sample_size": 20,
            "conclusion_notes": "Conversion rate exceeded threshold.",
        }

        signal_input = {
            "signal_name": "lapsed_2x_orderer_21d",
            "signal_description": "Contacts with 2+ orders who haven't ordered in 21+ days.",
            "detection_sql": "SELECT id AS contact_id FROM contacts WHERE total_orders >= 2 AND last_order_at < now() - interval '21 days'",
        }

        cur = MagicMock()
        cur.fetchall.return_value = [proven_exp]   # _get_proven_experiments_without_signal
        cur.fetchone.side_effect = [
            {"id": 501},   # discovered_signals INSERT RETURNING id
            None,          # _log_run
        ]

        claude_mock = _make_claude_mock(signal_input)

        with patch("app.routers.goal_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("anthropic.Anthropic", return_value=claude_mock):
            resp = client.post("/api/goal-agent/harvest")

        assert resp.status_code == 200
        data = resp.json()
        assert data["phase"] == "harvest"
        assert data["signals_discovered"] >= 1

    def test_harvest_no_proven_experiments(self, client, monkeypatch):
        """POST /harvest with no proven experiments returns 0 signals_discovered."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        cur = MagicMock()
        cur.fetchall.return_value = []   # no proven experiments without signal
        cur.fetchone.return_value = None

        mock_client_instance = MagicMock()

        with patch("app.routers.goal_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("anthropic.Anthropic", return_value=mock_client_instance):
            resp = client.post("/api/goal-agent/harvest")

        assert resp.status_code == 200
        assert resp.json()["signals_discovered"] == 0


# ---------------------------------------------------------------------------
# TestGoalAgentRun (full cycle)
# ---------------------------------------------------------------------------

class TestGoalAgentRun:
    """Tests for POST /run (full four-phase cycle)."""

    def test_run_no_anthropic_key(self, client, monkeypatch):
        """POST /run without ANTHROPIC_API_KEY returns 500."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        cur = _make_cursor(rows=[], fetchone_val=None)

        with patch("app.routers.goal_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/goal-agent/run")

        assert resp.status_code == 500

    def test_run_calls_all_four_phases(self, client, monkeypatch):
        """POST /run executes hypothesize + experiment + measure + harvest in sequence."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        phase_results = {
            "hypothesize": {"experiments_created": 2},
            "experiment": {"experiments_started": 1, "contacts_enrolled": 15},
            "measure": {"experiments_concluded": 0, "orders_attributed": 0},
            "harvest": {"signals_discovered": 0},
        }

        log_cur = _make_cursor()

        with patch("app.routers.goal_agent._phase_hypothesize",
                   return_value=phase_results["hypothesize"]), \
             patch("app.routers.goal_agent._phase_experiment",
                   return_value=phase_results["experiment"]), \
             patch("app.routers.goal_agent._phase_measure",
                   return_value=phase_results["measure"]), \
             patch("app.routers.goal_agent._phase_harvest",
                   return_value=phase_results["harvest"]), \
             patch("app.routers.goal_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(log_cur)), \
             patch("anthropic.Anthropic"):
            resp = client.post("/api/goal-agent/run")

        assert resp.status_code == 200
        data = resp.json()
        assert data["phase"] == "full"
        assert "timestamp" in data
        # All four phases must appear in details
        assert "hypothesize" in data["details"]
        assert "experiment" in data["details"]
        assert "measure" in data["details"]
        assert "harvest" in data["details"]
        assert data["experiments_created"] == 2
        assert data["experiments_started"] == 1
        assert data["contacts_enrolled"] == 15


# ---------------------------------------------------------------------------
# TestGetExperiments
# ---------------------------------------------------------------------------

class TestGetExperiments:
    """Tests for GET /experiments."""

    def test_get_experiments_empty(self, client):
        """GET /experiments with no rows returns count:0 and empty list."""
        cur = _make_cursor(rows=[])

        with patch("app.routers.goal_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/goal-agent/experiments")

        assert resp.status_code == 200
        data = resp.json()
        assert data["experiments"] == []
        assert data["count"] == 0

    def test_get_experiments_with_status_filter(self, client):
        """GET /experiments?status=pending returns only pending experiments."""
        row = {
            "id": 1,
            "hypothesis": "Nostalgia SMS reactivates lapsed customers",
            "experiment_type": "cohort_message",
            "status": "pending",
            "cohort_description": "lapsed 21-35 days",
            "enrolled_count": 0,
            "result_conversion_rate": None,
            "conclusion": None,
            "started_at": None,
            "concluded_at": None,
            "created_at": None,
        }
        cur = _make_cursor(rows=[row])

        with patch("app.routers.goal_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/goal-agent/experiments?status=pending")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["experiments"][0]["status"] == "pending"

    def test_get_experiments_multiple_rows(self, client):
        """GET /experiments returns all rows when no filter is applied."""
        rows = [
            {
                "id": 1, "hypothesis": "Test A", "experiment_type": "cohort_message",
                "status": "concluded", "cohort_description": "lapsed",
                "enrolled_count": 15, "result_conversion_rate": 0.12,
                "conclusion": "proven", "started_at": None,
                "concluded_at": None, "created_at": None,
            },
            {
                "id": 2, "hypothesis": "Test B", "experiment_type": "timing_test",
                "status": "running", "cohort_description": "new customers",
                "enrolled_count": 10, "result_conversion_rate": None,
                "conclusion": None, "started_at": None,
                "concluded_at": None, "created_at": None,
            },
        ]
        cur = _make_cursor(rows=rows)

        with patch("app.routers.goal_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/goal-agent/experiments")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert len(data["experiments"]) == 2


# ---------------------------------------------------------------------------
# TestGetSignals
# ---------------------------------------------------------------------------

class TestGetSignals:
    """Tests for GET /signals."""

    def test_list_signals_returns_discovered(self, client):
        """GET /signals returns all discovered signals ordered by confidence."""
        rows = [
            {
                "id": 1, "signal_name": "lapsed_2x_orderer_21d",
                "signal_description": "Customers with 2+ orders, lapsed 21+ days",
                "confidence": 0.15, "activation_count": 3,
                "is_active": True, "created_at": None,
            }
        ]
        cur = _make_cursor(rows=rows)

        with patch("app.routers.goal_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/goal-agent/signals")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["signals"][0]["signal_name"] == "lapsed_2x_orderer_21d"
        assert data["signals"][0]["confidence"] == 0.15

    def test_list_signals_empty(self, client):
        """GET /signals returns empty list when no signals discovered yet."""
        cur = _make_cursor(rows=[])

        with patch("app.routers.goal_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/goal-agent/signals")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["signals"] == []


# ---------------------------------------------------------------------------
# TestGetRuns
# ---------------------------------------------------------------------------

class TestGetRuns:
    """Tests for GET /runs."""

    def test_get_runs_empty(self, client):
        """GET /runs with no rows returns count:0 and empty list."""
        cur = _make_cursor(rows=[])

        with patch("app.routers.goal_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/goal-agent/runs")

        assert resp.status_code == 200
        data = resp.json()
        assert data["runs"] == []
        assert data["count"] == 0

    def test_get_runs_returns_audit_log(self, client):
        """GET /runs returns goal agent run audit log with expected fields."""
        rows = [
            {
                "id": 1, "run_type": "full",
                "experiments_created": 3, "experiments_started": 2,
                "contacts_enrolled": 40, "experiments_concluded": 1,
                "signals_discovered": 1, "orders_attributed": 4,
                "created_at": None,
            }
        ]
        cur = _make_cursor(rows=rows)

        with patch("app.routers.goal_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/goal-agent/runs")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["runs"][0]["run_type"] == "full"
        assert data["runs"][0]["experiments_created"] == 3


# ---------------------------------------------------------------------------
# Direct helper tests — cover uncovered branches
# ---------------------------------------------------------------------------

class TestToolCallException:
    """Test _tool_call exception handler path."""

    def test_tool_call_exception_returns_empty_dict(self):
        """_tool_call logs error and returns {} when Claude raises."""
        from app.routers.goal_agent import _tool_call

        client = MagicMock()
        client.messages.create.side_effect = Exception("API timeout")

        tool = {
            "name": "test_tool",
            "description": "test",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        }
        result = _tool_call(client, "system prompt", "user prompt", tool)
        assert result == {}


class TestExecuteCohortSql:
    """Test _execute_cohort_sql branches."""

    def test_unsafe_sql_blocked_returns_empty(self):
        """_execute_cohort_sql returns [] when SQL contains INSERT."""
        from app.routers.goal_agent import _execute_cohort_sql
        result = _execute_cohort_sql("INSERT INTO contacts (first_name) VALUES ('x')")
        assert result == []

    def test_unsafe_delete_sql_blocked(self):
        """_execute_cohort_sql returns [] when SQL contains DELETE."""
        from app.routers.goal_agent import _execute_cohort_sql
        result = _execute_cohort_sql("DELETE FROM contacts WHERE id = 1")
        assert result == []

    def test_cursor_exception_returns_empty(self):
        """_execute_cohort_sql returns [] when cursor raises exception."""
        from app.routers.goal_agent import _execute_cohort_sql

        with patch("app.routers.goal_agent.get_cursor", side_effect=Exception("DB error")):
            result = _execute_cohort_sql("SELECT id FROM contacts")
        assert result == []

    def test_valid_sql_returns_rows(self):
        """_execute_cohort_sql returns rows from cursor for valid SQL."""
        from contextlib import contextmanager
        from app.routers.goal_agent import _execute_cohort_sql

        cur = MagicMock()
        cur.fetchall.return_value = [
            {"contact_id": 1, "first_name": "Alice", "phone": "+14041111111"},
        ]

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.goal_agent.get_cursor", side_effect=_mock_cursor):
            result = _execute_cohort_sql("SELECT id AS contact_id FROM contacts LIMIT 10")

        assert len(result) == 1
        assert result[0]["contact_id"] == 1


class TestEnqueueExperimentActions:
    """Test _enqueue_experiment_actions branches."""

    def test_skips_contact_with_no_phone(self):
        """Contact without phone is skipped — no cursor calls made."""
        from app.routers.goal_agent import _enqueue_experiment_actions
        from contextlib import contextmanager

        cur = MagicMock()

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        contacts = [{"contact_id": 1, "first_name": "Alice", "phone": ""}]
        with patch("app.routers.goal_agent.get_cursor", side_effect=_mock_cursor):
            count = _enqueue_experiment_actions(99, contacts, "Hi {first_name}!")

        assert count == 0

    def test_enqueue_exception_skips_contact(self):
        """Exception during enqueue is swallowed; enrolled stays 0."""
        from app.routers.goal_agent import _enqueue_experiment_actions

        with patch("app.routers.goal_agent.get_cursor", side_effect=Exception("DB error")):
            contacts = [{"contact_id": 2, "first_name": "Bob", "phone": "+14042222222"}]
            count = _enqueue_experiment_actions(99, contacts, "Hi {first_name}!")

        assert count == 0


class TestCountExperimentConversions:
    """Test _count_experiment_conversions helper."""

    def test_returns_conversion_stats(self):
        """_count_experiment_conversions returns dict with total_enrolled, converted, etc."""
        from contextlib import contextmanager
        from app.routers.goal_agent import _count_experiment_conversions

        cur = MagicMock()
        cur.fetchone.return_value = {
            "total_enrolled": 20,
            "converted": 5,
            "not_yet_checked": 15,
        }

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.goal_agent.get_cursor", side_effect=_mock_cursor):
            result = _count_experiment_conversions(42)

        assert result["total_enrolled"] == 20
        assert result["converted"] == 5


class TestCheckAndMarkConversions:
    """Test _check_and_mark_conversions helper."""

    def test_no_unchecked_contacts_returns_zero(self):
        """With no unchecked contacts, _check_and_mark_conversions returns 0."""
        from contextlib import contextmanager
        from app.routers.goal_agent import _check_and_mark_conversions

        cur = MagicMock()
        cur.fetchall.return_value = []  # no unchecked contacts

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.goal_agent.get_cursor", side_effect=_mock_cursor):
            result = _check_and_mark_conversions(99)

        assert result == 0

    def test_converts_contacts_who_ordered(self):
        """Contact who ordered after enrollment is marked converted."""
        from contextlib import contextmanager
        from app.routers.goal_agent import _check_and_mark_conversions
        from datetime import date

        cur = MagicMock()
        # fetchall: unchecked contacts
        cur.fetchall.return_value = [
            {"id": 101, "contact_id": 5, "enrolled_at": date(2026, 1, 1)}
        ]
        # fetchone call sequence: 1st for SELECT FROM orders (found), 2nd for UPDATE (no result)
        cur.fetchone.side_effect = [{"id": 999}, None]

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.goal_agent.get_cursor", side_effect=_mock_cursor):
            result = _check_and_mark_conversions(99)

        assert result == 1


class TestSaveDiscoveredSignal:
    """Test _save_discovered_signal helper."""

    def test_unsafe_signal_sql_blocked(self):
        """Signal with INSERT in detection_sql returns None."""
        from app.routers.goal_agent import _save_discovered_signal

        signal = {
            "signal_name": "bad_signal",
            "signal_description": "bad",
            "detection_sql": "INSERT INTO contacts VALUES (1)",
        }
        result = _save_discovered_signal(1, signal, 0.5)
        assert result is None

    def test_exception_returns_none(self):
        """Exception during INSERT returns None."""
        from app.routers.goal_agent import _save_discovered_signal

        signal = {
            "signal_name": "good_signal",
            "signal_description": "good signal",
            "detection_sql": "SELECT id FROM contacts",
        }
        with patch("app.routers.goal_agent.get_cursor", side_effect=Exception("DB error")):
            result = _save_discovered_signal(1, signal, 0.8)

        assert result is None

    def test_saves_signal_successfully(self):
        """Valid signal returns the new signal_id."""
        from contextlib import contextmanager
        from app.routers.goal_agent import _save_discovered_signal

        cur = MagicMock()
        cur.fetchone.return_value = {"id": 77}

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        signal = {
            "signal_name": "lapsed_2x_signal",
            "signal_description": "2+ orders, 21d lapsed",
            "detection_sql": "SELECT id FROM contacts WHERE total_orders >= 2",
        }
        with patch("app.routers.goal_agent.get_cursor", side_effect=_mock_cursor):
            result = _save_discovered_signal(5, signal, 0.75)

        assert result == 77


class TestPhaseExperimentNoCohortSql:
    """Test _phase_experiment branch when cohort SQL is missing."""

    def test_experiment_skips_when_no_cohort_sql(self, client, monkeypatch):
        """When Claude returns no cohort_sql, experiment is skipped."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        pending_exp = {
            "id": 7,
            "hypothesis": "test",
            "experiment_type": "cohort_message",
            "cohort_description": "test group",
            "cohort_filter": {},
            "message_template": "",
            "success_threshold": 0.1,
        }

        # Claude returns no cohort_sql
        cohort_build_input = {"cohort_sql": "", "message_template": ""}
        claude_mock = _make_claude_mock(cohort_build_input)

        cur = MagicMock()
        cur.fetchall.side_effect = [
            [],              # recently contacted
            [],              # active experiment contacts
            [pending_exp],   # pending experiments
        ]
        cur.fetchone.return_value = None

        with patch("app.routers.goal_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("anthropic.Anthropic", return_value=claude_mock):
            resp = client.post("/api/goal-agent/experiment")

        assert resp.status_code == 200
        data = resp.json()
        assert data["experiments_started"] == 0
        assert data["contacts_enrolled"] == 0


class TestSaveExperimentsDuplicatePath:
    """Test _save_experiments when INSERT returns None (duplicate)."""

    def test_duplicate_hypothesis_is_skipped(self):
        """When INSERT returns no row (duplicate hash), hypothesis is not added to ids."""
        from contextlib import contextmanager
        from app.routers.goal_agent import _save_experiments

        cur = MagicMock()
        # First call: INSERT returns None (duplicate)
        cur.fetchone.return_value = None

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        hypotheses = [
            {
                "hypothesis": "Test hypothesis",
                "experiment_type": "cohort_message",
                "cohort_description": "test group",
                "cohort_filter": {},
                "message_template": "Hi {first_name}!",
                "success_threshold": 0.1,
                "rationale": "test",
            }
        ]

        with patch("app.routers.goal_agent.get_cursor", side_effect=_mock_cursor):
            ids = _save_experiments(hypotheses)

        assert ids == []


class TestRunConclusionAgent:
    """Test _run_conclusion_agent direct call."""

    def test_run_conclusion_agent_returns_conclusion(self):
        """_run_conclusion_agent calls _tool_call and returns conclusion dict."""
        from app.routers.goal_agent import _run_conclusion_agent

        client = MagicMock()
        block = MagicMock()
        block.type = "tool_use"
        block.input = {"conclusion": "proven", "conclusion_notes": "Conversion exceeded threshold."}
        response = MagicMock()
        response.content = [block]
        client.messages.create.return_value = response

        experiment = {
            "hypothesis": "SMS reactivates lapsed users",
            "experiment_type": "cohort_message",
            "success_threshold": 0.10,
        }
        stats = {"total_enrolled": 20, "converted": 5, "not_yet_checked": 0}

        result = _run_conclusion_agent(client, experiment, stats, baseline_rate=0.05)
        assert result["conclusion"] == "proven"
        assert "threshold" in result["conclusion_notes"].lower()


class TestConcludeExperiment:
    """Test _conclude_experiment direct call."""

    def test_conclude_experiment_updates_db(self):
        """_conclude_experiment executes UPDATE on goal_experiments."""
        from contextlib import contextmanager
        from app.routers.goal_agent import _conclude_experiment

        cur = MagicMock()

        @contextmanager
        def _mock_cursor(commit=False):
            yield cur

        with patch("app.routers.goal_agent.get_cursor", side_effect=_mock_cursor):
            _conclude_experiment(
                experiment_id=10,
                conclusion="proven",
                notes="Exceeded threshold.",
                conversions=3,
                sample=20,
            )

        cur.execute.assert_called_once()
        call_sql = cur.execute.call_args[0][0]
        assert "goal_experiments" in call_sql


class TestPhaseMeasureWithExperiment:
    """Test _phase_measure endpoint when there are ready experiments."""

    def test_measure_with_real_experiment_flow(self, client, monkeypatch):
        """POST /measure concludes an experiment when measure data is present."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        ready_exp = {
            "id": 11,
            "hypothesis": "Nostalgia SMS works",
            "experiment_type": "cohort_message",
            "success_threshold": 0.10,
            "enrolled_count": 20,
        }

        # snapshot fetchall x5, fetchone x4, then _check_and_mark_conversions (fetchall=unchecked,fetchone=has_order,fetchone=None-for-update)
        # then _count_experiment_conversions (fetchone), then _conclude_experiment (no return)
        cur = MagicMock()
        cur.fetchall.side_effect = [
            [ready_exp],                    # _get_experiments_ready_to_measure
            [],                             # _check_and_mark_conversions: unchecked contacts
            # _get_system_snapshot:
            [{"lifecycle_segment": "active", "cnt": 50}],  # segments
            [],  # recent_experiments
            [],  # active_signals
            [],  # top_items
            [],  # orders_by_dow
        ]
        cur.fetchone.side_effect = [
            # _get_system_snapshot fetchone calls:
            {"orders_30d": 10, "ordering_customers_30d": 8,
             "avg_order_value": 30.0, "total_contacts": 200},  # order_stats
            {"ordered_7d": 5},
            {"lapsed_count": 30},
            {"never_ordered": 60},
            # _count_experiment_conversions:
            {"total_enrolled": 20, "converted": 3, "not_yet_checked": 0},
            # _conclude_experiment (no fetchone needed)
            None,  # _log_run
        ]

        claude_mock = _make_claude_mock({"conclusion": "inconclusive", "conclusion_notes": "Sample too small."})

        with patch("app.routers.goal_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("anthropic.Anthropic", return_value=claude_mock):
            resp = client.post("/api/goal-agent/measure")

        assert resp.status_code == 200
        data = resp.json()
        assert data["experiments_concluded"] == 1


class TestPhaseRunException:
    """Test /run endpoint exception handling in phase loop."""

    def test_run_phase_exception_captured_in_details(self, client, monkeypatch):
        """POST /run captures phase exceptions in details without raising."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        log_cur = _make_cursor()

        with patch("app.routers.goal_agent._phase_hypothesize",
                   side_effect=Exception("Hypothesize boom")), \
             patch("app.routers.goal_agent._phase_experiment",
                   return_value={"experiments_started": 0, "contacts_enrolled": 0}), \
             patch("app.routers.goal_agent._phase_measure",
                   return_value={"experiments_concluded": 0, "orders_attributed": 0}), \
             patch("app.routers.goal_agent._phase_harvest",
                   return_value={"signals_discovered": 0}), \
             patch("app.routers.goal_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(log_cur)), \
             patch("anthropic.Anthropic"):
            resp = client.post("/api/goal-agent/run")

        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data["details"]["hypothesize"]
        assert data["phase"] == "full"
