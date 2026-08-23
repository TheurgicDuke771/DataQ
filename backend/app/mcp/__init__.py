"""FastMCP server (Week 7; expanded to 19 tools in the #529 Tier-1 batch) — curated
tools mounted at ``/mcp``.
"""

from backend.app.mcp.server import build_mcp_app

__all__ = ["build_mcp_app"]
