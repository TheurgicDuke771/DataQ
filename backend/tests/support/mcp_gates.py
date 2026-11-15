"""The declared authorization gate of every MCP tool, for the RBAC sweeps.

The table is not kept here any more (#1838): each tool declares its gate where it is
registered (`backend.app.mcp.tool_gates.classified_tool`), and this module only reads the
registry. Importing the server is what fills it.
"""

from __future__ import annotations

import backend.app.mcp.server  # noqa: F401 — registering the tools fills `GATES`
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

__all__ = [
    "GATES",
    "KNOWN_GATES",
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
