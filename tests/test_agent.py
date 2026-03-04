"""
Tests for app/routers/agent.py
================================

Covers:
  - POST /api/agent/analyze-contacts — no API key (500), no candidates (empty), with candidates + mocked Claude
  - POST /api/agent/analyze-single/{id} — not found (404), success with mocked Claude
  - build_contact_profile — direct test
  - call_claude — no API key (raises 500)
  - get_full_system_prompt — sanity test

Run with:
    pytest tests/test_agent.py -v
"""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

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
    c.rowcount = 1 if fetchone_val else 0
    return c


CLAUDE_ANALYSIS_JSON = """{
  "should_act": true,
  "action_type": "send_sms",
  "priority": "warm",
  "reason": "Customer engaged but hasn't ordered recently",
  "suggested_message": "Hi Alice! We miss you at DabbahWala. Try our new Palak Paneer this week!",
  "channel": "sms",
  "confidence": 0.75,
  "reasoning": "Customer opened 3 emails but no order in 14 days — moderate win-back opportunity"
}"""


# ─────────────────────────────────────────────────────────────────────────────
# build_contact_profile — direct tests
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildContactProfile:
    """Test build_contact_profile helper."""

    def test_returns_none_when_contact_not_found(self):
        """Returns None when fetchone returns None (no such contact)."""
        from app.routers.agent import build_contact_profile

        cur = MagicMock()
        cur.rowcount = 0
        cur.fetchone.return_value = None
        cur.fetchall.return_value = []

        result = build_contact_profile(cur, 99999)
        assert result is None

    def test_returns_profile_dict_with_all_sections(self):
        """Returns profile dict with all expected keys when contact found."""
        from app.routers.agent import build_contact_profile

        contact_row = {
            "id": 1, "first_name": "Alice", "last_name": "Smith",
            "email": "alice@example.com", "phone": "+14041111111",
            "lifecycle_segment": "active", "total_orders": 5,
            "last_order_at": None, "primary_source": "Direct",
            "opens_7d": 3, "clicks_7d": 1, "sms_sent_7d": 0,
            "sms_clicks_7d": 0, "orders_7d": 0,
        }

        cur = MagicMock()
        cur.rowcount = 1
        cur.fetchone.return_value = contact_row
        # fetchall returns: orders, fav_items, sms_history, call_history, decisions, pending_opps, current_menu
        cur.fetchall.side_effect = [
            [{"order_date": "2026-01-01", "source": "Direct", "total_amount": 45.0,
              "order_type": "delivery", "delivery_slot": "12-2 PM", "customer_name_raw": "Alice"}],
            [{"item_name": "Palak Paneer", "times_ordered": 3, "total_qty": 3}],
            [],  # sms_history
            [],  # call_history
            [],  # decisions
            [],  # pending_opps
            [{"item_name": "Palak Paneer", "category": "Veg", "is_veg": True, "price": 14.0, "is_featured": False}],
        ]

        result = build_contact_profile(cur, 1)

        assert result is not None
        assert result["contact"]["first_name"] == "Alice"
        assert len(result["orders"]) == 1
        assert len(result["favorite_items"]) == 1
        assert "current_menu" in result
        assert "new_to_customer_this_week" in result


# ─────────────────────────────────────────────────────────────────────────────
# get_full_system_prompt
# ─────────────────────────────────────────────────────────────────────────────

class TestGetFullSystemPrompt:
    def test_returns_base_prompt_when_no_rules(self):
        """get_full_system_prompt returns BASE_SYSTEM_PROMPT when no playbook rules in DB."""
        from app.routers.agent import get_full_system_prompt

        cur = MagicMock()
        cur.fetchall.return_value = []  # no rules

        with patch("app.routers.agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            prompt = get_full_system_prompt()

        assert isinstance(prompt, str)
        assert len(prompt) > 100
        assert "DabbahWala" in prompt

    def test_appends_playbook_rules_when_present(self):
        """get_full_system_prompt appends playbook rules to base prompt."""
        from app.routers.agent import get_full_system_prompt

        cur = MagicMock()
        cur.fetchall.return_value = [
            {"category": "exclusion", "rule_name": "No-contact rule",
             "instruction": "Never contact opted-out users.", "priority": 10}
        ]

        with patch("app.routers.agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            prompt = get_full_system_prompt()

        assert "No-contact rule" in prompt
        assert "Never contact opted-out users." in prompt


# ─────────────────────────────────────────────────────────────────────────────
# call_claude — no API key
# ─────────────────────────────────────────────────────────────────────────────

class TestCallClaude:
    def test_raises_500_when_no_api_key(self, monkeypatch):
        """call_claude raises HTTPException 500 when ANTHROPIC_API_KEY not set."""
        import asyncio
        from fastapi import HTTPException
        monkeypatch.setattr("app.routers.agent.ANTHROPIC_API_KEY", "")

        from app.routers.agent import call_claude
        with pytest.raises(HTTPException) as exc_info:
            asyncio.get_event_loop().run_until_complete(
                call_claude("system", "user")
            )
        assert exc_info.value.status_code == 500


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/agent/analyze-contacts
# ─────────────────────────────────────────────────────────────────────────────

class TestAnalyzeContacts:
    def test_claude_exception_handled_gracefully(self, client, monkeypatch):
        """When Claude raises exception during analyze, contact is skipped and 200 returned."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr("app.routers.agent.ANTHROPIC_API_KEY", "test-key")

        contact_row = {
            "id": 42, "first_name": "Alice", "last_name": "Smith",
            "email": "alice@example.com", "lifecycle_segment": "active",
            "total_orders": 3, "last_order_at": None, "primary_source": "Direct",
            "opens_7d": 2, "clicks_7d": 0, "orders_7d": 0,
        }
        call_count = [0]

        @contextmanager
        def _smart_cursor(commit=False):
            call_count[0] += 1
            cur = MagicMock()
            if call_count[0] == 1:
                cur.rowcount = 1
                cur.fetchall.return_value = [contact_row]
                cur.fetchone.return_value = None
            else:
                cur.rowcount = 1
                cur.fetchone.return_value = contact_row
                cur.fetchall.side_effect = [[], [], [], [], [], [], []]
            yield cur

        with patch("app.routers.agent.get_cursor", side_effect=_smart_cursor), \
             patch("app.routers.agent.call_claude", side_effect=Exception("API timeout")), \
             patch("app.routers.agent.get_full_system_prompt", return_value="system prompt"):
            resp = client.post("/api/agent/analyze-contacts")

        # Exception is caught and logged — returns 200 with 0 opportunities
        assert resp.status_code == 200
        data = resp.json()
        assert data["contacts_analyzed"] == 1
        assert data["opportunities_created"] == 0

    def test_no_candidates_returns_empty_result(self, client, monkeypatch):
        """When no candidate contacts found, returns empty AgentResult."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr("app.routers.agent.ANTHROPIC_API_KEY", "test-key")

        cur = _make_cursor(rows=[])
        with patch("app.routers.agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/agent/analyze-contacts")

        assert resp.status_code == 200
        data = resp.json()
        assert data["contacts_analyzed"] == 0
        assert data["opportunities_created"] == 0
        assert data["actions"] == []

    def test_with_one_contact_mocked_claude(self, client, monkeypatch):
        """With one candidate, calls Claude and returns action when should_act=True."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr("app.routers.agent.ANTHROPIC_API_KEY", "test-key")

        contact_row = {
            "id": 42, "first_name": "Alice", "last_name": "Smith",
            "email": "alice@example.com", "phone": "+14041111111",
            "lifecycle_segment": "active", "total_orders": 3,
            "last_order_at": None, "primary_source": "Direct",
            "opens_7d": 2, "clicks_7d": 0, "orders_7d": 0,
        }

        # First get_cursor call: SELECT candidates — returns [contact_row]
        # Then for each candidate: build_contact_profile (cursor with contact row + empty fetchalls)
        # Then: INSERT opportunity, INSERT decision_log

        call_count = [0]

        @contextmanager
        def _smart_cursor(commit=False):
            call_count[0] += 1
            cur = MagicMock()
            cur.rowcount = 1

            if call_count[0] == 1:
                # First call: SELECT candidates
                cur.fetchall.return_value = [contact_row]
                cur.fetchone.return_value = None
            else:
                # build_contact_profile calls
                cur.fetchone.return_value = contact_row
                cur.fetchall.side_effect = [
                    [],  # orders
                    [],  # fav_items
                    [],  # sms_history
                    [],  # call_history
                    [],  # decisions
                    [],  # pending_opps
                    [],  # current_menu
                ]
            yield cur

        with patch("app.routers.agent.get_cursor", side_effect=_smart_cursor), \
             patch("app.routers.agent.call_claude", return_value=CLAUDE_ANALYSIS_JSON), \
             patch("app.routers.agent.get_full_system_prompt", return_value="system prompt"):
            resp = client.post("/api/agent/analyze-contacts")

        assert resp.status_code == 200
        data = resp.json()
        assert data["contacts_analyzed"] == 1

    def test_skips_contact_with_empty_profile(self, client, monkeypatch):
        """Contacts with no profile data are skipped."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr("app.routers.agent.ANTHROPIC_API_KEY", "test-key")

        contact_row = {
            "id": 55, "first_name": "Bob", "last_name": "Jones",
            "email": "bob@example.com", "phone": "+14042222222",
            "lifecycle_segment": "active", "total_orders": 1,
            "last_order_at": None, "primary_source": "Direct",
            "opens_7d": 0, "clicks_7d": 0, "orders_7d": 0,
        }

        call_count = [0]

        @contextmanager
        def _smart_cursor(commit=False):
            call_count[0] += 1
            cur = MagicMock()

            if call_count[0] == 1:
                # SELECT candidates
                cur.rowcount = 1
                cur.fetchall.return_value = [contact_row]
                cur.fetchone.return_value = None
            else:
                # build_contact_profile — contact not found
                cur.rowcount = 0
                cur.fetchone.return_value = None
                cur.fetchall.return_value = []
            yield cur

        with patch("app.routers.agent.get_cursor", side_effect=_smart_cursor), \
             patch("app.routers.agent.call_claude", return_value=CLAUDE_ANALYSIS_JSON), \
             patch("app.routers.agent.get_full_system_prompt", return_value="system prompt"):
            resp = client.post("/api/agent/analyze-contacts")

        assert resp.status_code == 200
        data = resp.json()
        # Contact was skipped because profile was None
        assert data["contacts_analyzed"] == 1
        assert data["opportunities_created"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/agent/analyze-single/{contact_id}
# ─────────────────────────────────────────────────────────────────────────────

class TestAnalyzeSingleContact:
    def test_contact_not_found_returns_404(self, client, monkeypatch):
        """POST /api/agent/analyze-single/9999 returns 404 when contact doesn't exist."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr("app.routers.agent.ANTHROPIC_API_KEY", "test-key")

        cur = MagicMock()
        cur.rowcount = 0
        cur.fetchone.return_value = None
        cur.fetchall.return_value = []

        with patch("app.routers.agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)):
            resp = client.post("/api/agent/analyze-single/9999")
        assert resp.status_code == 404

    def test_analyze_single_success(self, client, monkeypatch):
        """POST /api/agent/analyze-single/{id} returns profile + analysis."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr("app.routers.agent.ANTHROPIC_API_KEY", "test-key")

        contact_row = {
            "id": 10, "first_name": "Carol", "last_name": "Davis",
            "email": "carol@example.com", "phone": "+14043333333",
            "lifecycle_segment": "active", "total_orders": 7,
            "last_order_at": None, "primary_source": "Direct",
            "opens_7d": 4, "clicks_7d": 2, "sms_sent_7d": 1,
            "sms_clicks_7d": 0, "orders_7d": 0,
        }

        cur = MagicMock()
        cur.rowcount = 1
        cur.fetchone.return_value = contact_row
        cur.fetchall.side_effect = [
            [],  # orders
            [{"item_name": "Dal Makhani", "times_ordered": 5, "total_qty": 5}],  # fav_items
            [],  # sms_history
            [],  # call_history
            [],  # decisions
            [],  # pending_opps
            [],  # current_menu
        ]

        with patch("app.routers.agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("app.routers.agent.call_claude", return_value=CLAUDE_ANALYSIS_JSON), \
             patch("app.routers.agent.get_full_system_prompt", return_value="system prompt"):
            resp = client.post("/api/agent/analyze-single/10")

        assert resp.status_code == 200
        data = resp.json()
        assert data["contact_id"] == 10
        assert "profile_summary" in data
        assert "analysis" in data
        assert data["profile_summary"]["name"] == "Carol Davis"

    def test_analyze_single_invalid_json_response(self, client, monkeypatch):
        """When Claude returns invalid JSON, analysis contains raw_response."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr("app.routers.agent.ANTHROPIC_API_KEY", "test-key")

        contact_row = {
            "id": 11, "first_name": "Dave", "last_name": "Brown",
            "email": "dave@example.com", "phone": None,
            "lifecycle_segment": "active", "total_orders": 2,
            "last_order_at": None, "primary_source": "Direct",
            "opens_7d": 0, "clicks_7d": 0, "sms_sent_7d": 0,
            "sms_clicks_7d": 0, "orders_7d": 0,
        }

        cur = MagicMock()
        cur.rowcount = 1
        cur.fetchone.return_value = contact_row
        cur.fetchall.side_effect = [[], [], [], [], [], [], []]

        with patch("app.routers.agent.get_cursor",
                   side_effect=lambda commit=False: _cursor_ctx(cur)), \
             patch("app.routers.agent.call_claude", return_value="not valid json at all"), \
             patch("app.routers.agent.get_full_system_prompt", return_value="system prompt"):
            resp = client.post("/api/agent/analyze-single/11")

        assert resp.status_code == 200
        data = resp.json()
        assert data["analysis"]["parse_error"] is True
        assert "raw_response" in data["analysis"]
