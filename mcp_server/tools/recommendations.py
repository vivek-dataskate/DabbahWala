"""MCP tools for recommendations — reactivation targets, content strategy."""

import json

from app.db import get_cursor


def register_recommendations_tools(mcp):
    @mcp.tool()
    def suggest_reactivation_targets(limit: int = 20) -> str:
        """Find contacts most likely to reactivate based on engagement history and order patterns.
        Prioritizes contacts who had recent engagement but haven't ordered.

        Args:
            limit: Max contacts to return (default 20)
        """
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT * FROM suggest_reactivation_targets(%s)", (limit,))
            rows = cur.fetchall()
            result = [{k: str(v) if v is not None else None for k, v in dict(r).items()} for r in rows]
            return json.dumps({"count": len(result), "targets": result}, indent=2)

    @mcp.tool()
    def recommend_content_strategy(contact_id: int) -> str:
        """Analyze a contact's full history and suggest content/messaging strategy.
        Returns engagement data, communication history, and delivery feedback for agent analysis.

        Args:
            contact_id: The contact ID to analyze
        """
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT get_content_strategy_data(%s)", (contact_id,))
            result = cur.fetchone()["get_content_strategy_data"]
            return json.dumps(result, indent=2)
