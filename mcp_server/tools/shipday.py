"""MCP tools for Shipday delivery data — read-only, ad-hoc analysis."""

import json
import os

import httpx

BASE_URL = "https://api.shipday.com"


def _headers() -> dict:
    api_key = os.getenv("SHIPDAY_API_KEY", "")
    if not api_key:
        raise RuntimeError("SHIPDAY_API_KEY env var not set")
    return {"Authorization": f"Basic {api_key}", "Content-Type": "application/json"}


def register_shipday_tools(mcp):
    @mcp.tool()
    def get_shipday_order(order_number: str) -> str:
        """Get full details for a single Shipday order by order number.

        Args:
            order_number: The Shipday order number (e.g. DW-1234)
        """
        with httpx.Client(timeout=15) as client:
            r = client.get(f"{BASE_URL}/orders/{order_number}", headers=_headers())
            if r.status_code == 404:
                return json.dumps({"found": False, "order_number": order_number})
            r.raise_for_status()
        return json.dumps(r.json(), indent=2, default=str)

    @mcp.tool()
    def list_shipday_orders(from_date: str = "", to_date: str = "") -> str:
        """List Shipday orders, optionally filtered by date range.

        Args:
            from_date: Start date in YYYY-MM-DD format (optional)
            to_date:   End date in YYYY-MM-DD format (optional)
        """
        params = {}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        with httpx.Client(timeout=20) as client:
            r = client.get(f"{BASE_URL}/orders", headers=_headers(), params=params)
            r.raise_for_status()
        return json.dumps(r.json(), indent=2, default=str)

    @mcp.tool()
    def get_shipday_carriers() -> str:
        """List all drivers/carriers configured in Shipday."""
        with httpx.Client(timeout=15) as client:
            r = client.get(f"{BASE_URL}/carriers", headers=_headers())
            r.raise_for_status()
        return json.dumps(r.json(), indent=2, default=str)

    @mcp.tool()
    def get_shipday_order_tracking(order_number: str) -> str:
        """Get real-time tracking status and driver location for a Shipday order.

        Args:
            order_number: The Shipday order number
        """
        with httpx.Client(timeout=15) as client:
            r = client.get(f"{BASE_URL}/orders/{order_number}/tracking", headers=_headers())
            if r.status_code == 404:
                return json.dumps({"found": False, "order_number": order_number})
            r.raise_for_status()
        return json.dumps(r.json(), indent=2, default=str)
