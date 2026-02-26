"""
Tests for app/routers/playbook.py
===================================

Covers:
  Endpoint tests (TestClient with mocked DB)
    - GET  /api/playbook/rules             — empty, with data
    - GET  /api/playbook/rules/for-prompt  — empty, with rules
    - POST /api/playbook/rules             — create rule, missing fields → 422
    - PUT  /api/playbook/rules/{id}        — update rule
    - DELETE /api/playbook/rules/{id}      — deactivate rule
    - POST /api/playbook/sync-from-airtable — new record, empty records

Run with:
    pytest tests/test_playbook.py -v
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

@contextmanager
def _cursor_ctx(cur):
    yield cur


def _make_cursor(rows=None, fetchone_val=None):
    c = MagicMock()
    c.fetchall.return_value = rows or []
    c.fetchone.return_value = fetchone_val
    return c


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/playbook/rules
# ─────────────────────────────────────────────────────────────────────────────

class TestGetRules:
    def test_get_rules_empty(self, client):
        """GET /api/playbook/rules with no rules — 200 empty list."""
        cur = _make_cursor(rows=[])

        with patch("app.routers.playbook.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/playbook/rules")

        assert resp.status_code == 200
        data = resp.json()
        assert data == []

    def test_get_rules_with_data(self, client):
        """GET /api/playbook/rules returns active rules."""
        rows = [
            {
                "id": 1,
                "rule_name": "No Contact After 9PM",
                "category": "exclusion",
                "instruction": "Never send SMS after 9 PM local time.",
                "priority": 90,
                "is_active": True,
                "created_by": "airtable",
                "created_at": None,
            }
        ]
        cur = _make_cursor(rows=rows)

        with patch("app.routers.playbook.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/playbook/rules")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["rule_name"] == "No Contact After 9PM"


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/playbook/rules/for-prompt
# ─────────────────────────────────────────────────────────────────────────────

class TestGetRulesForPrompt:
    def test_empty_prompt(self, client):
        """GET rules/for-prompt with no active rules — empty prompt."""
        cur = _make_cursor(rows=[])

        with patch("app.routers.playbook.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/playbook/rules/for-prompt")

        assert resp.status_code == 200
        data = resp.json()
        assert data["prompt_section"] == ""
        assert data["rule_count"] == 0

    def test_with_rules(self, client):
        """GET rules/for-prompt with rules — prompt_section contains rule name."""
        rows = [
            {
                "id": 1,
                "category": "general",
                "rule_name": "Be Friendly",
                "instruction": "Always greet the customer warmly.",
                "priority": 50,
            }
        ]
        cur = _make_cursor(rows=rows)

        with patch("app.routers.playbook.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.get("/api/playbook/rules/for-prompt")

        assert resp.status_code == 200
        data = resp.json()
        assert data["rule_count"] == 1
        assert "Be Friendly" in data["prompt_section"]


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/playbook/rules
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateRule:
    def test_create_rule(self, client):
        """POST rules — creates rule, returns id and status='created'."""
        cur = _make_cursor(fetchone_val={"id": 5})

        with patch("app.routers.playbook.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/playbook/rules",
                json={
                    "rule_name": "Prioritise Lapsed",
                    "category": "priority",
                    "instruction": "Always reach lapsed customers first.",
                    "priority": 80,
                    "is_active": True,
                    "created_by": "admin",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 5
        assert data["status"] == "created"

    def test_missing_rule_name(self, client):
        """POST rules without rule_name — 422 validation error."""
        resp = client.post(
            "/api/playbook/rules",
            json={
                "category": "general",
                "instruction": "Do something.",
                "priority": 50,
                "is_active": True,
                "created_by": "admin",
            },
        )
        assert resp.status_code == 422

    def test_missing_instruction(self, client):
        """POST rules without instruction — 422 validation error."""
        resp = client.post(
            "/api/playbook/rules",
            json={
                "rule_name": "Rule Without Instruction",
                "category": "general",
                "priority": 50,
                "is_active": True,
                "created_by": "admin",
            },
        )
        assert resp.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# PUT /api/playbook/rules/{rule_id}
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdateRule:
    def test_update_rule(self, client):
        """PUT /api/playbook/rules/1 — 200 {id:1, status:'updated'}."""
        cur = _make_cursor()

        with patch("app.routers.playbook.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.put(
                "/api/playbook/rules/1",
                json={
                    "rule_name": "Updated Rule",
                    "category": "general",
                    "instruction": "Updated instruction.",
                    "priority": 60,
                    "is_active": True,
                    "created_by": "admin",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 1
        assert data["status"] == "updated"


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /api/playbook/rules/{rule_id}
# ─────────────────────────────────────────────────────────────────────────────

class TestDeleteRule:
    def test_delete_rule(self, client):
        """DELETE /api/playbook/rules/1 — soft-delete, 200 {id:1, status:'deactivated'}."""
        cur = _make_cursor()

        with patch("app.routers.playbook.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.delete("/api/playbook/rules/1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 1
        assert data["status"] == "deactivated"


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/playbook/sync-from-airtable
# ─────────────────────────────────────────────────────────────────────────────

class TestAirtableSync:
    def test_sync_new_record(self, client):
        """POST sync-from-airtable with a new record — created:1."""
        # fetchall for existing rules = [] (none), then no further fetchone needed
        cur = _make_cursor(rows=[])

        with patch("app.routers.playbook.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post(
                "/api/playbook/sync-from-airtable",
                json={
                    "records": [
                        {
                            "rule_name": "Always Follow Up",
                            "category": "general",
                            "instruction": "Follow up within 24 hours.",
                            "priority": 50,
                            "active": True,
                        }
                    ]
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 1
        assert data["synced"] == 1

    def test_sync_empty(self, client):
        """POST sync-from-airtable with no records — synced:0."""
        cur = _make_cursor(rows=[])

        with patch("app.routers.playbook.get_cursor", side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/playbook/sync-from-airtable", json={"records": []})

        assert resp.status_code == 200
        data = resp.json()
        assert data["synced"] == 0
        assert data["created"] == 0
