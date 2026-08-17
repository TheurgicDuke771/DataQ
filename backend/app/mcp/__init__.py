"""FastMCP server (Week 7; expanded to 19 tools in the #529 Tier-1 batch) — curated
tools mounted at ``/mcp``.

LLM-facing surface over the same service layer the REST API uses: each tool is a
thin wrapper that resolves the caller, calls a service function with the *same*
per-suite authz, and returns an LLM-shaped dict. No business logic is duplicated
here (CLAUDE.md §10 — descriptions are written for natural-language selection,
not REST consumers).
"""

from backend.app.mcp.server import build_mcp_app

__all__ = ["build_mcp_app"]
