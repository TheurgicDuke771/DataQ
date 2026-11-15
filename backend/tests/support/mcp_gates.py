"""The declared authorization gate of every MCP tool, for the RBAC sweeps.

The table is not kept here any more (#1838): each tool declares its gate where it is
registered (`backend.app.mcp.tool_gates.classified_tool`), and this module only reads the
registry. Importing the server is what fills it.
"""

from __future__ import annotations

from backend.app.mcp import server as _server
from backend.app.mcp.tool_gates import (
    GATES,
    KNOWN_GATES,
    MEMBER_DENIED_GATES,
    OUTSIDER_DENIED_GATES,
    READ_GATES,
    VIEWER_DENIED_GATES,
    WRITE_GATES,
    tools_with_gate,
)

#: The server whose registration filled `GATES`: importing it is what fills the registry.
MCP_SERVER = _server.mcp

__all__ = [
    "GATES",
    "KNOWN_GATES",
    "MCP_SERVER",
    "MEMBER_DENIED_GATES",
    "OUTSIDER_DENIED_GATES",
    "READ_GATES",
    "VIEWER_DENIED_GATES",
    "WRITE_GATES",
    "member_denied_tools",
    "outsider_denied_tools",
    "tools_with_gate",
    "viewer_denied_tools",
]


def viewer_denied_tools() -> list[str]:
    return tools_with_gate(*VIEWER_DENIED_GATES)


def member_denied_tools() -> list[str]:
    return tools_with_gate(*MEMBER_DENIED_GATES)


def outsider_denied_tools() -> list[str]:
    return tools_with_gate(*OUTSIDER_DENIED_GATES)
