"""MCP tools for Instantly email campaign data — read-only, ad-hoc analysis."""

import json
import os

import httpx


INSTANTLY_BASE = "https://api.instantly.ai/api/v1"


def _headers() -> dict:
    key = os.getenv("INSTANTLY_API_KEY", "")
    if not key:
        raise RuntimeError("INSTANTLY_API_KEY env var not set")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def register_instantly_tools(mcp):
    @mcp.tool()
    def list_instantly_campaigns() -> str:
        """List all Instantly email campaigns with their IDs, names, and status."""
        with httpx.Client(timeout=15) as client:
            r = client.get(f"{INSTANTLY_BASE}/campaign/list", headers=_headers(),
                           params={"limit": 100, "skip": 0})
            r.raise_for_status()
            campaigns = r.json()
        result = [
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "status": c.get("status"),
                "created": c.get("timestamp_created"),
            }
            for c in (campaigns if isinstance(campaigns, list) else campaigns.get("data", []))
        ]
        return json.dumps(result, indent=2)

    @mcp.tool()
    def get_instantly_campaign_analytics(campaign_id: str, days: int = 7) -> str:
        """Get performance analytics for a specific Instantly campaign.

        Args:
            campaign_id: Instantly campaign UUID
            days: Lookback period in days (default 7)
        """
        with httpx.Client(timeout=15) as client:
            r = client.get(
                f"{INSTANTLY_BASE}/analytics/campaign/summary",
                headers=_headers(),
                params={"campaign_id": campaign_id, "days": days},
            )
            r.raise_for_status()
            data = r.json()
        return json.dumps(data, indent=2)

    @mcp.tool()
    def get_instantly_lead(email: str) -> str:
        """Look up a lead's status and campaign membership in Instantly by email.

        Args:
            email: The lead's email address
        """
        with httpx.Client(timeout=15) as client:
            r = client.get(
                f"{INSTANTLY_BASE}/lead/get",
                headers=_headers(),
                params={"email": email},
            )
            if r.status_code == 404:
                return json.dumps({"found": False, "email": email})
            r.raise_for_status()
            data = r.json()
        return json.dumps({"found": True, **data}, indent=2)

    @mcp.tool()
    def list_instantly_campaign_leads(campaign_id: str, status: str = "all", limit: int = 50) -> str:
        """List leads in a campaign, optionally filtered by status.

        Args:
            campaign_id: Instantly campaign UUID
            status: Filter by lead status — 'all', 'completed', 'interested', 'not_interested', 'out_of_office'
            limit: Max results to return (default 50, max 100)
        """
        params = {"campaign_id": campaign_id, "limit": min(limit, 100), "skip": 0}
        if status != "all":
            params["lead_status"] = status
        with httpx.Client(timeout=15) as client:
            r = client.get(f"{INSTANTLY_BASE}/lead/list", headers=_headers(), params=params)
            r.raise_for_status()
            data = r.json()
        leads = data if isinstance(data, list) else data.get("data", [])
        result = [
            {
                "email": l.get("email"),
                "first_name": l.get("first_name"),
                "status": l.get("lead_status"),
                "last_contacted": l.get("timestamp_last_contact"),
                "opened": l.get("opened"),
                "replied": l.get("replied"),
            }
            for l in leads
        ]
        return json.dumps({"campaign_id": campaign_id, "count": len(result), "leads": result}, indent=2)

    @mcp.tool()
    def get_instantly_account_summary() -> str:
        """Get overall Instantly account health — sending limits, warmup status, connected inboxes."""
        with httpx.Client(timeout=15) as client:
            r = client.get(f"{INSTANTLY_BASE}/account/list", headers=_headers(),
                           params={"limit": 100, "skip": 0})
            r.raise_for_status()
            data = r.json()
        accounts = data if isinstance(data, list) else data.get("data", [])
        result = [
            {
                "email": a.get("email"),
                "status": a.get("status"),
                "warmup_enabled": a.get("warmup_details", {}).get("warmup_enabled"),
                "daily_limit": a.get("sending_limit"),
                "provider": a.get("provider_code"),
            }
            for a in accounts
        ]
        return json.dumps({"inbox_count": len(result), "inboxes": result}, indent=2)
