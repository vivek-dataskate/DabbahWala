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
