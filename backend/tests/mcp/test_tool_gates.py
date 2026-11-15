"""Every MCP tool declares its gate and its classification once, at registration (#1838).

The gate is what the server enforces and what the RBAC sweeps probe; the annotations are
what an MCP client reads to decide whether to ask before calling. Both come from one
`classified_tool` call, so these tests check the two agree and that nothing bypasses it.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

import pytest
from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from backend.app.mcp import server
from backend.app.mcp import tool_gates as tg

# The hand-written table this refactor replaced, as it stood on main when the gates moved
# onto the tools. Frozen on purpose: the move must not change any tool's authorization,
# and a later change to a gate should be a visible edit here, not a side effect.
_GATES_BEFORE_1838: dict[str, str] = {
    # ── no per-resource gate ───────────────────────────────────────────────── `list_assets` and
    # `get_asset` are here by ADR 0037's explicit decision.
    "get_adf_pipeline_status": "read",  # deprecated alias for get_pipeline_status (#1443)
    "get_asset": "read",
    "get_coverage": "read",  # workspace-wide counts, nothing named (ADR 0047 §8)
    "get_doc": "read",  # curated docs/site pages, workspace-agnostic (#1626)
    "get_health_score": "read",
    "get_pipeline_status": "read",
    "get_suite_performance": "read",
    "list_assets": "read",
    "list_connections": "read",
    "list_contracts": "read",  # visibility follows the linked suites (ADR 0052 §8)
    "list_notification_channels": "read",
    "list_seasonal_periods": "read",  # the workspace calendar, read by anyone (ADR 0048)
    "list_suites": "read",
    "trace_column_lineage": "read",  # lineage topology is workspace-visible (ADR 0037)
    # ── accessible-suite scoped, and view-gated when a suite is named ────────
    "get_near_misses": "read:suite-optional",
    "list_incidents": "read:suite-optional",
    "list_runs": "read:suite-optional",
    "list_schedules": "read:suite-optional",
    "list_trigger_bindings": "read:suite-optional",
    # ── reads gated on the suite ─────────────────────────────────────────────
    "export_suite": "suite:view",
    "get_check": "suite:view",
    "get_check_history": "suite:view",
    "get_column_policy": "suite:view",
    "get_detected_seasonal_periods": "suite:view",
    "get_notification_config": "suite:view",
    "get_run_results": "suite:view",
    "get_run_status": "suite:view",
    "get_suite_results": "suite:view",
    "list_check_versions": "suite:view",
    "list_checks": "suite:view",
    "list_suggested_rules": "suite:view",
    "list_suite_channels": "suite:view",
    # ── writes (and live probes) gated on the suite ────────────────────────── `profile_column` is
    # here, not with the reads: it persists nothing.
    "cancel_run": "suite:edit",
    "create_check": "suite:edit",
    "create_schedule": "suite:edit",
    "create_trigger_binding": "suite:edit",
    "delete_check": "suite:edit",
    "dryrun_check": "suite:edit",
    "list_columns": "suite:edit",
    "delete_schedule": "suite:edit",
    "delete_trigger_binding": "suite:edit",
    "profile_column": "suite:edit",
    "restore_check_version": "suite:edit",
    "set_column_policy": "suite:edit",
    "suggest_column_policy": "suite:edit",
    "snooze_check": "suite:edit",
    "trigger_suite_run": "suite:edit",
    "update_check": "suite:edit",
    "update_schedule": "suite:edit",
    "update_suite": "suite:edit",
    "update_trigger_binding": "suite:edit",
    # ── contract-scoped, via the linked suites (ADR 0052) ────────────────────
    "export_contract": "contract:view",
    "get_contract": "contract:view",
    # ── incident-scoped, via the incident's suite ────────────────────────────
    "ack_incident": "incident:edit",
    "get_incident": "incident:view",
    "resolve_incident": "incident:edit",
    # ── workspace-role gated: no suite to hang a resource gate on ─────────── `test_connection`
    # and `browse_connection` spend a stored credential against a remote system; `import_suite`
    # CREATES a suite.
    "browse_connection": "role:member",
    "import_suite": "role:member",
    "test_connection": "role:member",
}


#: The live-probe tools, as the docs exporter listed them by hand before #1838.
_LIVE_PROBES_BEFORE_1838 = frozenset(
    {
        "browse_connection",
        "dryrun_check",
        "list_columns",
        "profile_column",
        "suggest_column_policy",
        "test_connection",
    }
)


def _tools() -> list[Any]:
    return list(asyncio.run(server.mcp.list_tools()))


def test_moving_the_gates_onto_the_tools_changed_no_tool_s_gate() -> None:
    assert tg.GATES == _GATES_BEFORE_1838


def test_every_registered_tool_is_classified() -> None:
    tools = _tools()
    assert tg.unclassified(tools) == []
    assert {t.name for t in tools} == set(tg.GATES)


def test_the_published_split_is_35_read_only_18_changing_state_6_live_probes() -> None:
    counts = Counter(tg.classification(t.annotations) for t in _tools())
    assert counts == {"read_only": 35, "changes_state": 18, "live_probe": 6}


def test_the_live_probes_are_the_tools_that_were_documented_as_live_probes() -> None:
    probes = {t.name for t in _tools() if tg.classification(t.annotations) == "live_probe"}
    assert probes == _LIVE_PROBES_BEFORE_1838


@pytest.mark.parametrize("tool", _tools(), ids=lambda t: t.name)
def test_a_tool_s_annotations_match_its_gate(tool: Any) -> None:
    # Read-only on the wire exactly when the gate lets a Viewer through; a live probe and
    # a write are gated like writes (a Viewer is refused) and never advertised read-only.
    read_only = tg.classification(tool.annotations) == "read_only"
    assert read_only == (tg.GATES[tool.name] in tg.READ_GATES)
    assert read_only != (tg.GATES[tool.name] in tg.VIEWER_DENIED_GATES)


def test_a_tool_registered_without_classified_tool_is_reported() -> None:
    probe = FastMCP(name="probe")

    @probe.tool
    def bare() -> str:
        return "x"

    assert tg.unclassified(asyncio.run(probe.list_tools())) == ["bare"]


@pytest.mark.parametrize(
    ("gate", "annotations", "message"),
    [
        ("suite:typo", tg.READ_ONLY, "unknown MCP gate"),
        ("suite:edit", tg.READ_ONLY, "cannot carry the gate"),
        ("suite:view", tg.changes_state(destructive=False), "cannot carry the gate"),
        ("read", tg.LIVE_PROBE, "cannot carry the gate"),
        ("suite:edit", ToolAnnotations(readOnlyHint=False), "annotations must be"),
    ],
)
def test_classified_tool_refuses_a_gate_and_classification_that_disagree(
    gate: str, annotations: Any, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        tg.classified_tool(FastMCP(name="probe"), gate, annotations)
