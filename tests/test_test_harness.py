"""
Tests for app/routers/test_harness.py
=======================================

Covers:
  - _check_auth: no secret set (allows), wrong secret (403), right secret (allows)
  - POST /api/test/run — mocked run_full_suite
  - GET  /api/test/run/{group_id} — mocked group functions, invalid group 404, failures → 207
  - GET  /api/test/results — mocked get_recent_runs
  - GET  /api/test/results/{run_id} — mocked get_run_by_id, not found → 404

Run with:
    pytest tests/test_test_harness.py -v
"""

from unittest.mock import MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# TestCheckAuth
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckAuth:
    def test_no_admin_secret_env_allows_any(self, monkeypatch):
        """_check_auth returns None when ADMIN_SECRET is not configured."""
        monkeypatch.delenv("ADMIN_SECRET", raising=False)
        from app.routers.test_harness import _check_auth
        # Should not raise
        _check_auth("wrong-secret")
        _check_auth(None)

    def test_wrong_secret_raises_403(self, monkeypatch):
        """_check_auth raises 403 when provided secret doesn't match ADMIN_SECRET."""
        from fastapi import HTTPException
        monkeypatch.setenv("ADMIN_SECRET", "correct-secret")
        from app.routers.test_harness import _check_auth
        with pytest.raises(HTTPException) as exc_info:
            _check_auth("wrong-secret")
        assert exc_info.value.status_code == 403

    def test_correct_secret_allows(self, monkeypatch):
        """_check_auth allows when provided secret matches ADMIN_SECRET."""
        monkeypatch.setenv("ADMIN_SECRET", "correct-secret")
        from app.routers.test_harness import _check_auth
        # Should not raise
        _check_auth("correct-secret")

    def test_none_secret_with_admin_set_allows(self, monkeypatch):
        """_check_auth allows when no secret provided (None) even if ADMIN_SECRET set."""
        monkeypatch.setenv("ADMIN_SECRET", "correct-secret")
        from app.routers.test_harness import _check_auth
        # None secret → falsy → check short-circuits
        _check_auth(None)


# ─────────────────────────────────────────────────────────────────────────────
# TestRunTestSuite
# ─────────────────────────────────────────────────────────────────────────────

class TestRunTestSuite:
    def test_run_returns_suite_dict(self, client):
        """POST /api/test/run runs the suite and returns to_dict() result."""
        mock_suite = MagicMock()
        mock_suite.to_dict.return_value = {
            "run_id": "abc123",
            "triggered_by": "manual",
            "passed": 10,
            "failed": 0,
            "groups": [],
        }

        with patch("app.services.test_harness_service.run_full_suite",
                   return_value=mock_suite):
            resp = client.post("/api/test/run")

        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "abc123"
        assert data["passed"] == 10

    def test_run_with_triggered_by_query(self, client):
        """POST /api/test/run?triggered_by=n8n_daily passes triggered_by param."""
        mock_suite = MagicMock()
        mock_suite.to_dict.return_value = {"run_id": "xyz", "passed": 5, "failed": 0}

        with patch("app.services.test_harness_service.run_full_suite",
                   return_value=mock_suite) as mock_run:
            resp = client.post("/api/test/run?triggered_by=n8n_daily")

        assert resp.status_code == 200
        mock_run.assert_called_once_with(triggered_by="n8n_daily")


# ─────────────────────────────────────────────────────────────────────────────
# TestRunTestGroup
# ─────────────────────────────────────────────────────────────────────────────

def _make_group_patches():
    """Return a list of all group function patches."""
    return [
        patch("app.services.test_harness_service._g1_connectivity"),
        patch("app.services.test_harness_service._g2_schema"),
        patch("app.services.test_harness_service._g3_contact_setup"),
        patch("app.services.test_harness_service._g4_events"),
        patch("app.services.test_harness_service._g5_telnyx_sms"),
        patch("app.services.test_harness_service._g6_agent_pipeline"),
        patch("app.services.test_harness_service._g7_intelligence"),
        patch("app.services.test_harness_service._g8_instantly"),
        patch("app.services.test_harness_service._g9_airtable"),
        patch("app.services.test_harness_service._g10_action_queue"),
        patch("app.services.test_harness_service._g11_orders"),
        patch("app.services.test_harness_service._g12_reports"),
        patch("app.services.test_harness_service._g13_query_chatbot"),
        patch("app.services.test_harness_service._g14_cleanup"),
        patch("app.services.test_harness_service._g15_competitor_agent"),
    ]


def _mock_suite(passed, failed):
    """Create a fully configured mock TestSuite."""
    mock_s = MagicMock()
    mock_s.completed_at = None
    mock_s.duration_seconds = None
    mock_s.summary.return_value = {"passed": passed, "failed": failed, "total": passed + failed}
    mock_s.to_dict.return_value = {"run_id": f"t-{passed}-{failed}", "groups": []}
    return mock_s


class TestRunTestGroup:
    def test_invalid_group_id_returns_404(self, client):
        """GET /api/test/run/99 with invalid group_id returns 404."""
        resp = client.get("/api/test/run/99")
        assert resp.status_code == 404

    def test_valid_group_all_pass_returns_200(self, client):
        """GET /api/test/run/1 with all passing tests returns 200."""
        patches = _make_group_patches()
        for p in patches:
            p.start()
        try:
            with patch("app.services.test_harness_service.TestSuite") as MockSuiteClass:
                MockSuiteClass.return_value = _mock_suite(passed=3, failed=0)
                resp = client.get("/api/test/run/1")
        finally:
            for p in patches:
                p.stop()

        assert resp.status_code == 200
        data = resp.json()
        assert data["passed"] == 3
        assert data["failed"] == 0

    def test_valid_group_with_failures_returns_207(self, client):
        """GET /api/test/run/2 with failed tests returns 207 Multi-Status."""
        patches = _make_group_patches()
        for p in patches:
            p.start()
        try:
            with patch("app.services.test_harness_service.TestSuite") as MockSuiteClass:
                MockSuiteClass.return_value = _mock_suite(passed=2, failed=1)
                resp = client.get("/api/test/run/2")
        finally:
            for p in patches:
                p.stop()

        assert resp.status_code == 207
        data = resp.json()
        assert data["failed"] == 1

    def test_group_function_exception_swallowed(self, client):
        """GET /api/test/run/3 swallows unexpected exception from group function."""
        def _bad_group(suite):
            raise RuntimeError("Unexpected crash!")

        patches = _make_group_patches()
        # Override _g3_contact_setup patch with side_effect version
        patches[2] = patch("app.services.test_harness_service._g3_contact_setup",
                           side_effect=_bad_group)

        for p in patches:
            p.start()
        try:
            with patch("app.services.test_harness_service.TestSuite") as MockSuiteClass:
                MockSuiteClass.return_value = _mock_suite(passed=0, failed=0)
                resp = client.get("/api/test/run/3")
        finally:
            for p in patches:
                p.stop()

        # Should still return 200 (exception is swallowed)
        assert resp.status_code in (200, 207)


# ─────────────────────────────────────────────────────────────────────────────
# TestListTestRuns
# ─────────────────────────────────────────────────────────────────────────────

class TestListTestRuns:
    def test_list_runs_returns_recent(self, client):
        """GET /api/test/results returns list of recent test runs."""
        mock_runs = [{"run_id": "r1", "passed": 10, "failed": 0}]
        with patch("app.services.test_harness_service.get_recent_runs",
                   return_value=mock_runs):
            resp = client.get("/api/test/results")

        assert resp.status_code == 200
        data = resp.json()
        assert "runs" in data
        assert len(data["runs"]) == 1

    def test_list_runs_with_limit(self, client):
        """GET /api/test/results?limit=5 passes limit to service."""
        with patch("app.services.test_harness_service.get_recent_runs",
                   return_value=[]) as mock_fn:
            resp = client.get("/api/test/results?limit=5")

        assert resp.status_code == 200
        mock_fn.assert_called_once_with(limit=5)

    def test_list_runs_with_wrong_secret(self, client, monkeypatch):
        """GET /api/test/results with wrong secret returns 403."""
        monkeypatch.setenv("ADMIN_SECRET", "real-secret")
        resp = client.get("/api/test/results?secret=wrong-secret")
        assert resp.status_code == 403

    def test_list_runs_with_header_auth(self, client, monkeypatch):
        """GET /api/test/results with correct X-Admin-Secret header returns 200."""
        monkeypatch.setenv("ADMIN_SECRET", "header-secret")
        with patch("app.services.test_harness_service.get_recent_runs", return_value=[]):
            resp = client.get(
                "/api/test/results",
                headers={"X-Admin-Secret": "header-secret"},
            )
        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# TestGetTestRun
# ─────────────────────────────────────────────────────────────────────────────

class TestGetTestRun:
    def test_get_run_found(self, client):
        """GET /api/test/results/{run_id} returns run data when found."""
        mock_run = {"run_id": "abc", "passed": 5, "failed": 0, "tests": []}
        with patch("app.services.test_harness_service.get_run_by_id",
                   return_value=mock_run):
            resp = client.get("/api/test/results/abc")

        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "abc"

    def test_get_run_not_found_returns_404(self, client):
        """GET /api/test/results/{run_id} returns 404 when run doesn't exist."""
        with patch("app.services.test_harness_service.get_run_by_id", return_value=None):
            resp = client.get("/api/test/results/nonexistent-run-id")

        assert resp.status_code == 404
