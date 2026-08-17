"""The declared authorization gate of every MCP tool — one table, two uses.

The #741 tripwire used to be a bare set of tool *names* with the gating rationale
in a comment beside each group. That caught a new tool being added, which is what
it existed for, but it could not catch a tool being added with the **wrong** gate:
a comment saying "suite-scoped writes" is not something a test can check, and a
name added to the wrong comment group looks identical to one added to the right
one.

So the gate is declared as data instead. `test_role_enforcement.py` uses it twice:

1. as the exact-set tripwire (registry must equal this table's keys), so any new
   tool forces an explicit decision here rather than sliding in; and
2. to *drive* the enforcement tests — every tool declared `suite:edit` is entered
   with a real Viewer principal and must refuse, and every tool declared
   `role:member` is entered with a real Viewer and must refuse. Adding a row is
   therefore adding a test, not just a manifest entry.

`GATES` values:

- ``read`` — no per-resource gate. Either genuinely workspace-scoped
  (`list_connections`, mirroring its REST route) or scoped by an accessible-suite
  subquery rather than a gate call. A Viewer is *supposed* to reach these.
- ``suite:view`` — `require_permission(minimum="view")`.
- ``suite:edit`` — `require_permission(minimum="edit")`, which
  `suite_authz._cap_for_viewer` feeds, so a Viewer is refused even holding a
  legacy `edit` share.
- ``role:member`` / ``role:admin`` — `server._require_role`, the coarse ADR 0033
  axis, for capabilities with no suite to hang a resource gate on.
"""

from __future__ import annotations

#: Tool name → its declared authorization gate. Keep alphabetical within a group.
GATES: dict[str, str] = {
    # ── reads with no per-resource gate ──────────────────────────────────────
    "get_adf_pipeline_status": "read",
    "get_health_score": "read",
    "get_suite_performance": "read",
    "list_connections": "read",
    "list_runs": "read",
    "list_schedules": "read",
    "list_suites": "read",
    "list_trigger_bindings": "read",
    # ── reads gated on the suite ─────────────────────────────────────────────
    "export_suite": "suite:view",
    "get_check": "suite:view",
    "get_check_history": "suite:view",
    "get_notification_config": "suite:view",
    "get_run_results": "suite:view",
    "get_run_status": "suite:view",
    "get_suite_results": "suite:view",
    "list_checks": "suite:view",
    # ── writes (and live probes) gated on the suite ──────────────────────────
    # `profile_column` is here, not with the reads: it persists nothing, but it
    # opens a live datasource connection with stored credentials, which is why
    # its REST twin gates on `edit` and not `view`. It sat in the "read-only"
    # comment group before this table existed — exactly the miscategorisation a
    # comment cannot catch and a driven test can.
    "create_check": "suite:edit",
    "profile_column": "suite:edit",
    "trigger_suite_run": "suite:edit",
}

#: The gates that must refuse a Viewer. Derived rather than listed, so a new
#: gate value cannot be silently omitted from the enforcement sweep.
VIEWER_DENIED_GATES = frozenset({"suite:edit", "role:member", "role:admin"})


def tools_with_gate(*gates: str) -> list[str]:
    """Tool names carrying any of `gates`, sorted for stable parametrize ids."""
    return sorted(name for name, gate in GATES.items() if gate in gates)


def viewer_denied_tools() -> list[str]:
    """Every tool a Viewer must be refused by — the enforcement sweep's input."""
    return tools_with_gate(*VIEWER_DENIED_GATES)
