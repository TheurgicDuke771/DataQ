"""The declared authorization gate of every MCP tool — one table, four sweeps."""

from __future__ import annotations

#: Tool name → its declared authorization gate. Keep alphabetical within a group.
GATES: dict[str, str] = {
    # ── no per-resource gate ───────────────────────────────────────────────── `list_assets` and
    # `get_asset` are here by ADR 0037's explicit decision.
    "get_adf_pipeline_status": "read",  # deprecated alias for get_pipeline_status (#1443)
    "get_asset": "read",
    "get_doc": "read",  # curated docs/site pages, workspace-agnostic (#1626)
    "get_health_score": "read",
    "get_pipeline_status": "read",
    "get_suite_performance": "read",
    "list_assets": "read",
    "list_connections": "read",
    "list_notification_channels": "read",
    "list_suites": "read",
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
    "get_notification_config": "suite:view",
    "get_run_results": "suite:view",
    "get_run_status": "suite:view",
    "get_suite_results": "suite:view",
    "list_check_versions": "suite:view",
    "list_checks": "suite:view",
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
    # ── incident-scoped, via the incident's suite ────────────────────────────
    "ack_incident": "incident:edit",
    "get_incident": "incident:view",
    "resolve_incident": "incident:edit",
    # ── workspace-role gated: no suite to hang a resource gate on ─────────── `test_connection`
    # spends a stored credential against a remote system; `import_suite` CREATES a suite.
    "import_suite": "role:member",
    "test_connection": "role:member",
}

#: Gates whose tools must refuse a **Viewer**.
VIEWER_DENIED_GATES = frozenset({"suite:edit", "incident:edit", "role:member", "role:admin"})

#: Gates whose tools must refuse a **Member** — admin-only capabilities.
MEMBER_DENIED_GATES = frozenset({"role:admin"})

#: Gates whose tools must refuse a user with **no share on the named suite**.
OUTSIDER_DENIED_GATES = frozenset(
    {"suite:view", "suite:edit", "incident:view", "incident:edit", "read:suite-optional"}
)

#: Every value that may appear in `GATES`. A typo'd gate would otherwise silently
#: match no sweep and be enforced by nothing.
KNOWN_GATES = frozenset(
    {
        "read",
        "read:suite-optional",
        "suite:view",
        "suite:edit",
        "incident:view",
        "incident:edit",
        "role:member",
        "role:admin",
    }
)


def tools_with_gate(*gates: str) -> list[str]:
    """Tool names carrying any of `gates`, sorted for stable parametrize ids."""
    return sorted(name for name, gate in GATES.items() if gate in gates)


def viewer_denied_tools() -> list[str]:
    return tools_with_gate(*VIEWER_DENIED_GATES)


def member_denied_tools() -> list[str]:
    return tools_with_gate(*MEMBER_DENIED_GATES)


def outsider_denied_tools() -> list[str]:
    return tools_with_gate(*OUTSIDER_DENIED_GATES)
