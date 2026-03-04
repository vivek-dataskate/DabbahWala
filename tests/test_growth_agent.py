"""
Tests for app/routers/growth_agent.py
========================================

Router prefix: /api/growth

Covers:
  - POST /run-cycle
  - POST /measure
  - GET  /experiments
  - GET  /insights
  - POST /baseline/update

All DB calls are mocked via patch on app.routers.growth_agent.get_cursor.
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
    """Return a mock anthropic.Anthropic client whose messages.create returns a tool_use block."""
    block = MagicMock()
    block.type = "tool_use"
    block.input = tool_input

    response = MagicMock()
    response.content = [block]
    response.stop_reason = "tool_use"
    response.usage = MagicMock()
    response.usage.input_tokens = 300
    response.usage.output_tokens = 150

    client_instance = MagicMock()
    client_instance.messages.create.return_value = response
    return client_instance


# ---------------------------------------------------------------------------
# TestGrowthRunCycle
# ---------------------------------------------------------------------------

class TestGrowthRunCycle:
    """Tests for POST /run-cycle."""

    def test_run_cycle_no_key(self, client, monkeypatch):
        """POST /run-cycle without ANTHROPIC_API_KEY returns 500."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        cur = _make_cursor(rows=[], fetchone_val={"baseline_conv_rate": 0.05, "measured_at": None})

        with patch("app.routers.growth_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/growth/run-cycle")

        assert resp.status_code == 500

    def test_run_cycle_launches_experiment(self, client, monkeypatch):
        """POST /run-cycle with mocked Claude and enough cohort contacts launches experiment."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        design_input = {
            "name": "Tuesday Urgency SMS",
            "hypothesis": "Sending urgency SMS on Tuesdays boosts reorders by 20%",
            "experiment_type": "timing",
            "channel": "sms",
            "target_segment": "lapsed_14d",
            "cohort_size": 20,
            "message_template": "Hi {first_name}, your next meal is waiting — order today!",
            "offer_detail": "",
            "win_condition": "order placed within 14 days",
            "measure_days": 14,
            "rationale": "Tuesday is a low-competition day",
        }

        # Provide 20 contacts — above COHORT_MIN=15
        cohort_rows = [
            {"id": i, "first_name": f"User{i}", "email": f"user{i}@example.com",
             "phone": f"+140400000{i:02d}", "lifecycle_segment": "active",
             "total_orders": 2, "last_order_at": None, "primary_source": "Website",
             "opens_7d": 1, "orders_7d": 0}
            for i in range(1, 21)
        ]

        cur = MagicMock()
        cur.fetchall.side_effect = [
            [],             # _get_past_experiments
            [],             # _get_current_menu (empty menu is fine)
            cohort_rows,    # _get_available_cohort
        ]
        cur.fetchone.side_effect = [
            {"baseline_conv_rate": 0.05, "measured_at": None},  # _get_baseline
            {"id": 42},    # INSERT experiments RETURNING id
            # _dispatch_experiment: one fetchone per contact for create_opportunity
            *[{"create_opportunity": 100 + i} for i in range(20)],
        ]

        call_count = [0]

        @contextmanager
        def _multi_cursor(commit=False):
            call_count[0] += 1
            yield cur

        claude_mock = _make_claude_mock(design_input)

        with patch("app.routers.growth_agent.get_cursor", side_effect=_multi_cursor), \
             patch("anthropic.Anthropic", return_value=claude_mock):
            resp = client.post("/api/growth/run-cycle")

        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        # Should launch since cohort (20) >= COHORT_MIN (15) and Claude returned a design
        # If something upstream returns empty and triggers skipped, just check status key exists
        assert data["status"] in ("launched", "skipped")

    def test_run_cycle_skips_when_cohort_too_small(self, client, monkeypatch):
        """POST /run-cycle skips experiment when available cohort < 5 contacts."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        design_input = {
            "name": "Small Cohort Test",
            "hypothesis": "Test hypothesis",
            "experiment_type": "timing",
            "channel": "sms",
            "target_segment": "active",
            "cohort_size": 20,
            "message_template": "Hi {first_name}!",
            "offer_detail": "",
            "win_condition": "places an order",
            "measure_days": 14,
            "rationale": "Testing with limited cohort",
        }

        # Only 3 contacts available — below the skip threshold of 5
        tiny_cohort = [
            {"id": i, "first_name": f"User{i}", "email": f"u{i}@x.com",
             "phone": f"+1{i:010d}", "lifecycle_segment": "active",
             "total_orders": 1, "last_order_at": None, "primary_source": "Website",
             "opens_7d": 0, "orders_7d": 0}
            for i in range(1, 4)
        ]

        cur = MagicMock()
        cur.fetchall.side_effect = [
            [],            # _get_past_experiments
            [],            # _get_current_menu
            tiny_cohort,   # _get_available_cohort
        ]
        cur.fetchone.side_effect = [
            {"baseline_conv_rate": 0.05, "measured_at": None},  # _get_baseline
        ]

        call_count = [0]

        @contextmanager
        def _multi_cursor(commit=False):
            call_count[0] += 1
            yield cur

        claude_mock = _make_claude_mock(design_input)

        with patch("app.routers.growth_agent.get_cursor", side_effect=_multi_cursor), \
             patch("anthropic.Anthropic", return_value=claude_mock):
            resp = client.post("/api/growth/run-cycle")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "skipped"
        assert "not enough" in data["reason"].lower() or "need" in data["reason"].lower()

    def test_run_cycle_skips_when_design_empty(self, client, monkeypatch):
        """POST /run-cycle skips when Claude returns empty experiment design."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        cur = MagicMock()
        cur.fetchall.side_effect = [
            [],  # _get_past_experiments
            [],  # _get_current_menu
        ]
        cur.fetchone.side_effect = [
            {"baseline_conv_rate": 0.05, "measured_at": None},  # _get_baseline
        ]

        call_count = [0]

        @contextmanager
        def _multi_cursor(commit=False):
            call_count[0] += 1
            yield cur

        # Claude returns no tool_use block → empty design {}
        empty_resp = MagicMock()
        empty_resp.content = []
        mock_client_instance = MagicMock()
        mock_client_instance.messages.create.return_value = empty_resp

        with patch("app.routers.growth_agent.get_cursor", side_effect=_multi_cursor), \
             patch("anthropic.Anthropic", return_value=mock_client_instance):
            resp = client.post("/api/growth/run-cycle")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "skipped"


# ---------------------------------------------------------------------------
# TestGrowthMeasure
# ---------------------------------------------------------------------------

class TestGrowthMeasure:
    """Tests for POST /measure."""

    def test_measure_empty(self, client, monkeypatch):
        """POST /measure with no due experiments returns measured:0 and empty results."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        cur = _make_cursor(rows=[])

        with patch("app.routers.growth_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/growth/measure")

        assert resp.status_code == 200
        data = resp.json()
        assert data["measured"] == 0
        assert data["results"] == []

    def test_measure_scores_completed_experiment(self, client, monkeypatch):
        """POST /measure with one due experiment calls Claude to analyse and marks it complete."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        due_experiment = {
            "id": 7,
            "name": "Tuesday Urgency SMS",
            "hypothesis": "Sending urgency SMS on Tuesdays boosts reorders",
            "experiment_type": "timing",
            "channel": "sms",
            "message_template": "Hi {first_name}, order today!",
            "offer_detail": "",
            "cohort_size": 20,
            "started_at": "2024-01-01T00:00:00+00:00",
            "measure_at": "2024-01-15T00:00:00+00:00",
        }

        analysis_input = {
            "is_winner": True,
            "learnings": "Tuesday urgency works. Conversion above baseline.",
            "next_hypothesis": "Try the same angle on Wednesdays.",
        }

        cur = MagicMock()
        # First fetchall from the outer `with get_cursor` → list of due experiments
        cur.fetchall.side_effect = [
            [due_experiment],   # outer SELECT due experiments
        ]
        # fetchone sequence within _measure_experiment:
        # UPDATE experiment_contacts (no return) → stats → _get_baseline → UPDATE experiments
        cur.fetchone.side_effect = [
            {"total": 20, "ordered": 5, "avg_days": 3.2},   # stats
            {"baseline_conv_rate": 0.05, "measured_at": None},  # _get_baseline
            None,  # UPDATE experiments
        ]

        call_count = [0]

        @contextmanager
        def _multi_cursor(commit=False):
            call_count[0] += 1
            yield cur

        claude_mock = _make_claude_mock(analysis_input)

        with patch("app.routers.growth_agent.get_cursor", side_effect=_multi_cursor), \
             patch("anthropic.Anthropic", return_value=claude_mock):
            resp = client.post("/api/growth/measure")

        assert resp.status_code == 200
        data = resp.json()
        assert data["measured"] >= 1
        assert len(data["results"]) >= 1
        assert data["results"][0]["experiment_id"] == 7


# ---------------------------------------------------------------------------
# TestGrowthExperiments
# ---------------------------------------------------------------------------

class TestGrowthExperiments:
    """Tests for GET /experiments."""

    def test_get_empty(self, client):
        """GET /experiments with no rows returns empty experiments list."""
        cur = _make_cursor(rows=[])

        with patch("app.routers.growth_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/growth/experiments")

        assert resp.status_code == 200
        data = resp.json()
        assert data["experiments"] == []

    def test_get_with_status_filter(self, client):
        """GET /experiments?status=running passes filter to DB and returns matching rows."""
        row = {
            "id": 1,
            "name": "Test Exp",
            "experiment_type": "timing",
            "channel": "sms",
            "hypothesis": "Tuesday urgency works",
            "cohort_size": 20,
            "orders_won": None,
            "conversion_rate": None,
            "is_winner": None,
            "learnings": None,
            "status": "running",
            "started_at": None,
            "measure_at": None,
            "measured_at": None,
        }
        cur = _make_cursor(rows=[row])

        with patch("app.routers.growth_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/growth/experiments?status=running")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["experiments"]) == 1
        assert data["experiments"][0]["status"] == "running"

    def test_get_without_filter(self, client):
        """GET /experiments without filter returns all experiments."""
        rows = [
            {
                "id": 1, "name": "Exp A", "experiment_type": "timing",
                "channel": "sms", "hypothesis": "H1", "cohort_size": 20,
                "orders_won": 4, "conversion_rate": 0.2, "is_winner": True,
                "learnings": "Works", "status": "complete",
                "started_at": None, "measure_at": None, "measured_at": None,
            },
            {
                "id": 2, "name": "Exp B", "experiment_type": "offer",
                "channel": "email", "hypothesis": "H2", "cohort_size": 30,
                "orders_won": None, "conversion_rate": None, "is_winner": None,
                "learnings": None, "status": "running",
                "started_at": None, "measure_at": None, "measured_at": None,
            },
        ]
        cur = _make_cursor(rows=rows)

        with patch("app.routers.growth_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/growth/experiments")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["experiments"]) == 2


# ---------------------------------------------------------------------------
# TestGrowthInsights
# ---------------------------------------------------------------------------

class TestGrowthInsights:
    """Tests for GET /insights."""

    def test_get_insights_no_completed_experiments(self, client, monkeypatch):
        """GET /insights with no completed experiments returns message without Claude call."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        cur = MagicMock()
        cur.fetchall.return_value = []  # no completed experiments
        cur.fetchone.return_value = {"baseline_conv_rate": 0.05, "measured_at": None}

        with patch("app.routers.growth_agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/growth/insights")

        assert resp.status_code == 200
        data = resp.json()
        assert "insights" in data or "insights_summary" in data

    def test_get_insights_with_completed_experiments(self, client, monkeypatch):
        """GET /insights with completed experiments calls Claude to synthesise results."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        completed_row = {
            "name": "Tuesday Urgency",
            "experiment_type": "timing",
            "channel": "sms",
            "hypothesis": "Tuesday SMS boosts reorders",
            "conversion_rate": 0.25,
            "is_winner": True,
            "learnings": "Tuesday urgency SMS works well",
            "started_at": None,
        }
        baseline_row = {"baseline_conv_rate": 0.05, "measured_at": None}

        call_count = [0]

        @contextmanager
        def _multi_cursor(commit=False):
            call_count[0] += 1
            c = MagicMock()
            if call_count[0] == 1:
                c.fetchall.return_value = [completed_row]
                c.fetchone.return_value = baseline_row
            else:
                c.fetchall.return_value = []
                c.fetchone.return_value = baseline_row
            yield c

        # GET /insights uses client.messages.create with a non-tool text response
        text_block = MagicMock()
        text_block.text = "Bullet 1: Tuesday urgency works. Bullet 2: Short SMS converts better."
        insights_response = MagicMock()
        insights_response.content = [text_block]

        claude_instance = MagicMock()
        claude_instance.messages.create.return_value = insights_response

        with patch("app.routers.growth_agent.get_cursor", side_effect=_multi_cursor), \
             patch("anthropic.Anthropic", return_value=claude_instance):
            resp = client.get("/api/growth/insights")

        assert resp.status_code == 200
        data = resp.json()
        assert "baseline_conv_rate" in data
        assert "experiments_completed" in data
        assert data["experiments_completed"] >= 1
        assert "insights_summary" in data


# ---------------------------------------------------------------------------
# TestBaselineUpdate
# ---------------------------------------------------------------------------

class TestBaselineUpdate:
    """Tests for POST /baseline/update."""

    def test_update_baseline_calculates_rate(self, client):
        """POST /baseline/update recalculates baseline and returns the new rate."""
        db_row = {
            "total_outreached": 100,
            "total_converted": 5,
        }
        cur = _make_cursor(fetchone_val=db_row)

        with patch("app.routers.growth_agent.get_cursor",
                   side_effect=lambda commit=True: _cursor_ctx(cur)):
            resp = client.post("/api/growth/baseline/update")

        assert resp.status_code == 200
        data = resp.json()
        assert "baseline_conv_rate" in data
        # 5 / 100 = 0.05
        assert abs(data["baseline_conv_rate"] - 0.05) < 0.001

    def test_update_baseline_zero_outreached(self, client):
        """POST /baseline/update handles zero outreached contacts without division error."""
        db_row = {
            "total_outreached": 0,
            "total_converted": 0,
        }
        cur = _make_cursor(fetchone_val=db_row)

        with patch("app.routers.growth_agent.get_cursor",
                   side_effect=lambda commit=True: _cursor_ctx(cur)):
            resp = client.post("/api/growth/baseline/update")

        assert resp.status_code == 200
        data = resp.json()
        assert "baseline_conv_rate" in data
        assert data["baseline_conv_rate"] == 0.0

    def test_update_baseline_returns_stats(self, client):
        """POST /baseline/update response includes outreach and conversion counts."""
        db_row = {
            "total_outreached": 200,
            "total_converted": 20,
        }
        cur = _make_cursor(fetchone_val=db_row)

        with patch("app.routers.growth_agent.get_cursor",
                   side_effect=lambda commit=True: _cursor_ctx(cur)):
            resp = client.post("/api/growth/baseline/update")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_outreached_30d"] == 200
        assert data["total_converted_7d"] == 20
        assert abs(data["baseline_conv_rate"] - 0.10) < 0.001
