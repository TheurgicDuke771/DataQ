"""Each MCP tool's authorization gate and its read-only / state-changing / live-probe
classification, declared once at registration.

`classified_tool` is the only way `server.py` registers a tool. One call records the
tool's gate in `GATES` and attaches the MCP `ToolAnnotations` a client can act on without
parsing prose. The RBAC sweeps, the published tool reference and the published counts all
read these two things; nothing else restates them.

What the three classifications put on the wire:

===============  ============  ===============  =============
classification   readOnlyHint  destructiveHint  openWorldHint
===============  ============  ===============  =============
read-only        true          (not sent)       false
changes state    false         per tool         false
live probe       false         false            true
===============  ============  ===============  =============

A **live probe** opens a remote datasource with a stored credential and may run a billed
query. It is deliberately not marked read-only, even where it stores nothing in DataQ: a
client that runs read-only tools without asking must still ask before one of these, and
some of them do record what they saw. That is the same reason they are gated like writes.

`destructiveHint` is true for a tool that removes or overwrites stored state, and false
for one that only adds (the MCP definition). `idempotentHint` is not set on any tool, so
a client assumes the protocol default, which is that repeating a call may have a further
effect.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, Literal, TypeVar

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

Classification = Literal["read_only", "changes_state", "live_probe"]

_F = TypeVar("_F", bound=Callable[..., Any])

#: Tool name -> its declared authorization gate, filled as `server.py` registers each tool.
GATES: dict[str, str] = {}

#: Gates a read-only tool carries. Who may call it is still decided per gate.
READ_GATES = frozenset(
    {"read", "read:suite-optional", "suite:view", "incident:view", "contract:view"}
)

#: Gates that refuse a Viewer. Every state-changing tool and every live probe carries one.
WRITE_GATES = frozenset({"suite:edit", "incident:edit", "role:member", "role:admin"})

#: Every value that may appear in `GATES`. A typo'd gate would otherwise silently
#: match no sweep and be enforced by nothing.
KNOWN_GATES = READ_GATES | WRITE_GATES

#: Gates whose tools must refuse a **Viewer**.
VIEWER_DENIED_GATES = WRITE_GATES

#: Gates whose tools must refuse a **Member** — admin-only capabilities.
MEMBER_DENIED_GATES = frozenset({"role:admin"})

#: Gates whose tools must refuse a user with **no share on the named suite**.
OUTSIDER_DENIED_GATES = frozenset(
    {
        "suite:view",
        "suite:edit",
        "incident:view",
        "incident:edit",
        "read:suite-optional",
        "contract:view",
    }
)

#: gate -> who can call a tool carrying it, as the published reference words it.
GATE_AUDIENCE: dict[str, str] = {
    "read": "any signed-in user",
    "read:suite-optional": "any signed-in user; suite-scoped when a suite is named",
    "suite:view": "`view` on the suite",
    "suite:edit": "`edit` on the suite",
    "incident:view": "`view` on the incident's suite",
    "incident:edit": "`edit` on the incident's suite",
    "contract:view": "`view` on one of the contract's suites (or a workspace admin)",
    "role:member": "workspace Member or Admin",
    "role:admin": "workspace Admin",
}

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
LIVE_PROBE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)


def changes_state(*, destructive: bool) -> ToolAnnotations:
    """A tool that writes to DataQ. ``destructive`` is required, never defaulted: the
    protocol default is true, and "additive only" is a claim to make on purpose.
    """
    return ToolAnnotations(readOnlyHint=False, destructiveHint=destructive, openWorldHint=False)


def classification(annotations: ToolAnnotations | None) -> Classification | None:
    """Which of the three groups these annotations declare, or ``None`` when they declare
    none: absent, or a combination `classified_tool` never produces.
    """
    if annotations is None:
        return None
    read_only, open_world = annotations.readOnlyHint, annotations.openWorldHint
    if read_only is True and open_world is False:
        return "read_only"
    if read_only is False and open_world is False and annotations.destructiveHint is not None:
        return "changes_state"
    if read_only is False and open_world is True and annotations.destructiveHint is False:
        return "live_probe"
    return None


def classified_tool(mcp: FastMCP, gate: str, annotations: ToolAnnotations) -> Callable[[_F], _F]:
    """Register a tool with its gate and its classification.

    Refuses, at import, a gate nobody enforces, annotations outside the three groups, and
    the one mismatch that would be an authorization hole: a tool that is not read-only
    behind a gate a Viewer passes.
    """
    kind = classification(annotations)
    if gate not in KNOWN_GATES:
        raise ValueError(f"unknown MCP gate {gate!r}")
    if kind is None:
        raise ValueError("annotations must be READ_ONLY, LIVE_PROBE or changes_state(...)")
    if (kind == "read_only") != (gate in READ_GATES):
        raise ValueError(f"a {kind} tool cannot carry the gate {gate!r}")

    def register(fn: _F) -> _F:
        mcp.tool(annotations=annotations)(fn)
        GATES[fn.__name__] = gate
        return fn

    return register


def unclassified(tools: Iterable[Any]) -> list[str]:
    """Names of registered tools that bypassed `classified_tool`: no recorded gate, or
    annotations that declare no classification. Empty for a correct server.
    """
    return sorted(
        t.name for t in tools if t.name not in GATES or classification(t.annotations) is None
    )


def tools_with_gate(*gates: str) -> list[str]:
    """Tool names carrying any of `gates`, sorted for stable parametrize ids."""
    return sorted(name for name, gate in GATES.items() if gate in gates)
