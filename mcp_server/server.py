"""
DabbahWala MCP Server — exposes Postgres marketing data as tools for Claude agents.

Run with:
    python -m mcp_server.server

Or configure in Claude Desktop's MCP settings.
"""

from mcp.server.fastmcp import FastMCP

from mcp_server.tools.agents import register_agent_tools
from mcp_server.tools.analytics import register_analytics_tools
from mcp_server.tools.communications import register_communications_tools
from mcp_server.tools.contacts import register_contacts_tools
from mcp_server.tools.instantly import register_instantly_tools
from mcp_server.tools.opportunities import register_opportunities_tools
from mcp_server.tools.recommendations import register_recommendations_tools
from mcp_server.tools.shipday import register_shipday_tools

mcp = FastMCP("DabbahWala Marketing")

# Register all tool groups
register_contacts_tools(mcp)
register_analytics_tools(mcp)
register_communications_tools(mcp)
register_recommendations_tools(mcp)
register_opportunities_tools(mcp)
register_agent_tools(mcp)
register_instantly_tools(mcp)
register_shipday_tools(mcp)

if __name__ == "__main__":
    mcp.run()
