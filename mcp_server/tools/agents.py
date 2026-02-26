"""
MCP tools exposing AI Stack tables to Claude agents.
Covers: contact_observations, action_plans,
        orchestrator_log, action_queue, customer_goals.
"""

from app.db import get_cursor


def register_agent_tools(mcp):

    @mcp.tool()
    def get_latest_observations(contact_id: int) -> dict:
        """
        Return the most recent Observer agent results (sentiment, intent, engagement)
        for a contact. Used by Advisor agents to read upstream analysis.
        (AI Stack Layer 1 output — table: contact_observations)
        """
        with get_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT id, contact_id, run_at,
                       sentiment, sentiment_confidence, sentiment_summary,
                       intent, intent_signals, intent_confidence,
                       engagement_score, engagement_trend, last_touch_hours_ago
                FROM contact_observations
                WHERE contact_id = %s
                ORDER BY run_at DESC LIMIT 1
                """,
                (contact_id,),
            )
            row = cur.fetchone()
            if not row:
                return {"error": f"No observations found for contact {contact_id}"}
            return dict(row)

    @mcp.tool()
    def get_latest_action_plan(contact_id: int) -> dict:
        """
        Return the most recent Advisor agent action plan
        (stage, channel, offer, escalation) for a contact.
        Used by the Orchestrator agent.
        (AI Stack Layer 2 output — table: action_plans)
        """
        with get_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT id, contact_id, observation_id, run_at,
                       recommended_stage, stage_confidence, stage_reason,
                       recommended_channel, channel_timing, channel_reason,
                       offer_type, suggested_copy, offer_reason,
                       should_escalate, escalation_urgency, escalation_reason
                FROM action_plans
                WHERE contact_id = %s
                ORDER BY run_at DESC LIMIT 1
                """,
                (contact_id,),
            )
            row = cur.fetchone()
            if not row:
                return {"error": f"No action plan found for contact {contact_id}"}
            return dict(row)

    @mcp.tool()
    def get_active_goal(contact_id: int) -> dict:
        """
        Return the active customer goal for a contact (goal type, deadline, status).
        Returns empty dict if no active goal exists.
        """
        with get_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT id, contact_id, goal, deadline, status, converted,
                       progress_notes, created_at, updated_at
                FROM customer_goals
                WHERE contact_id = %s AND status = 'active'
                ORDER BY created_at DESC LIMIT 1
                """,
                (contact_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else {}

    @mcp.tool()
    def get_orchestrator_history(contact_id: int, limit: int = 10) -> list:
        """
        Return recent Orchestrator decisions for a contact — what action was chosen,
        what channel, and the reasoning. Useful for auditing and for agents
        that need to know what was already attempted.
        """
        with get_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT id, run_at, chosen_action, chosen_channel,
                       reasoning, guardrails_applied
                FROM orchestrator_log
                WHERE contact_id = %s
                ORDER BY run_at DESC LIMIT %s
                """,
                (contact_id, limit),
            )
            return [dict(r) for r in cur.fetchall()]

    @mcp.tool()
    def get_pending_actions(limit: int = 50) -> list:
        """
        Return pending items in the action queue — actions approved by the
        Orchestrator that are waiting for n8n executors to process.
        """
        with get_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT aq.id, aq.contact_id, aq.action_type, aq.payload,
                       aq.status, aq.created_at,
                       c.email, c.phone, c.first_name, c.last_name
                FROM action_queue aq
                JOIN contacts c ON c.id = aq.contact_id
                WHERE aq.status = 'pending'
                ORDER BY aq.created_at ASC LIMIT %s
                """,
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]

    @mcp.tool()
    def get_ai_stack_summary(days: int = 7) -> dict:
        """
        Return a summary of AI Stack activity over the last N days:
        how many Observer runs, Advisor plans made, Orchestrator decisions,
        actions queued, goals achieved, and escalations triggered.
        """
        with get_cursor(commit=False) as cur:
            cur.execute(
                "SELECT COUNT(*) AS c FROM contact_observations WHERE run_at >= NOW() - (%s || ' days')::INTERVAL",
                (days,),
            )
            observer_runs = cur.fetchone()["c"]

            cur.execute(
                "SELECT COUNT(*) AS c FROM action_plans WHERE run_at >= NOW() - (%s || ' days')::INTERVAL",
                (days,),
            )
            advisor_runs = cur.fetchone()["c"]

            cur.execute(
                "SELECT COUNT(*) AS c FROM orchestrator_log WHERE run_at >= NOW() - (%s || ' days')::INTERVAL",
                (days,),
            )
            orch_runs = cur.fetchone()["c"]

            cur.execute(
                "SELECT COUNT(*) AS c FROM action_queue WHERE created_at >= NOW() - (%s || ' days')::INTERVAL",
                (days,),
            )
            actions_queued = cur.fetchone()["c"]

            cur.execute(
                """
                SELECT COUNT(*) AS c FROM action_plans
                WHERE run_at >= NOW() - (%s || ' days')::INTERVAL AND should_escalate = TRUE
                """,
                (days,),
            )
            escalations = cur.fetchone()["c"]

            cur.execute(
                "SELECT COUNT(*) AS c FROM customer_goals WHERE status = 'achieved' AND updated_at >= NOW() - (%s || ' days')::INTERVAL",
                (days,),
            )
            goals_achieved = cur.fetchone()["c"]

        return {
            "period_days": days,
            "observer_runs": observer_runs,
            "advisor_runs": advisor_runs,
            "orchestrator_runs": orch_runs,
            "actions_queued": actions_queued,
            "escalations_triggered": escalations,
            "goals_achieved": goals_achieved,
        }
