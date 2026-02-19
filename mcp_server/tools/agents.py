"""
MCP tools exposing agent intelligence tables to Claude agents.
Covers: inference_results, decision_recommendations,
        orchestrator_log, action_queue, customer_goals.
"""

from app.db import get_cursor


def register_agent_tools(mcp):

    @mcp.tool()
    def get_latest_inference(contact_id: int) -> dict:
        """
        Return the most recent inference results (sentiment, intent, engagement)
        for a contact. Used by decision agents to read upstream analysis.
        """
        with get_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT id, contact_id, run_at,
                       sentiment, sentiment_confidence, sentiment_summary,
                       intent, intent_signals, intent_confidence,
                       engagement_score, engagement_trend, last_touch_hours_ago
                FROM inference_results
                WHERE contact_id = %s
                ORDER BY run_at DESC LIMIT 1
                """,
                (contact_id,),
            )
            row = cur.fetchone()
            if not row:
                return {"error": f"No inference results found for contact {contact_id}"}
            return dict(row)

    @mcp.tool()
    def get_latest_decision(contact_id: int) -> dict:
        """
        Return the most recent decision recommendations
        (stage, channel, offer, escalation) for a contact.
        Used by the orchestrator agent.
        """
        with get_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT id, contact_id, inference_result_id, run_at,
                       recommended_stage, stage_confidence, stage_reason,
                       recommended_channel, channel_timing, channel_reason,
                       offer_type, suggested_copy, offer_reason,
                       should_escalate, escalation_urgency, escalation_reason
                FROM decision_recommendations
                WHERE contact_id = %s
                ORDER BY run_at DESC LIMIT 1
                """,
                (contact_id,),
            )
            row = cur.fetchone()
            if not row:
                return {"error": f"No decision recommendations found for contact {contact_id}"}
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
        Return recent orchestrator decisions for a contact — what action was chosen,
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
        orchestrator that are waiting for n8n executors to process.
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
    def get_agent_cycle_summary(days: int = 7) -> dict:
        """
        Return a summary of agent cycle activity over the last N days:
        how many inference runs, decisions made, actions queued,
        goals achieved, and escalations triggered.
        """
        with get_cursor(commit=False) as cur:
            cur.execute(
                "SELECT COUNT(*) AS c FROM inference_results WHERE run_at >= NOW() - (%s || ' days')::INTERVAL",
                (days,),
            )
            inference_runs = cur.fetchone()["c"]

            cur.execute(
                "SELECT COUNT(*) AS c FROM decision_recommendations WHERE run_at >= NOW() - (%s || ' days')::INTERVAL",
                (days,),
            )
            decision_runs = cur.fetchone()["c"]

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
                SELECT COUNT(*) AS c FROM decision_recommendations
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
            "inference_runs": inference_runs,
            "decision_runs": decision_runs,
            "orchestrator_runs": orch_runs,
            "actions_queued": actions_queued,
            "escalations_triggered": escalations,
            "goals_achieved": goals_achieved,
        }
