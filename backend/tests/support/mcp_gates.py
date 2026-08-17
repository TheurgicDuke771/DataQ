"""The declared authorization gate of every MCP tool — one table, four sweeps.

The #741 tripwire used to be a bare set of tool *names* with the gating rationale
in a comment beside each group. That caught a new tool being added, which is what
it existed for, but it could not catch a tool being added with the **wrong** gate:
a comment saying "suite-scoped writes" is not something a test can check, and a
name added to the wrong comment group looks identical to one added to the right
one.

So the gate is declared as data instead, and `test_role_enforcement.py` *drives*
enforcement from it — adding a row is adding a test, not just a manifest entry.

**Every gate value below drives at least one sweep**, deliberately. An earlier
draft only drove the Viewer-denied rows, which meant a tool declared `suite:view`
with no gate at all passed both tests — the table's own claim about it was
unverified, in a file whose entire purpose is to make such claims checkable.

`GATES` values:

- ``read`` — genuinely no per-resource gate, and nothing to enforce. Either
  workspace-scoped (`list_connections`, mirroring its REST route) or scoped
  wholly by an accessible-suite subquery with no id to name. A Viewer is
  *supposed* to reach these. This is the one value with no sweep, because there
  is no denial to assert; `test_read_gated_tools_really_take_no_suite_id` pins
  the claim that they have no suite argument to gate on, so a tool cannot be
  parked here to escape the sweeps.
- ``read:suite-optional`` — scoped by an accessible-suite subquery, AND
  `require_permission(minimum="view")` when the optional `suite_id` argument is
  passed. Both halves matter: without the subquery the list leaks, without the
  up-front gate a named-but-inaccessible suite returns `[]`, which reads as
  "nothing is wired up" (#828) instead of a denial.
- ``suite:view`` — `require_permission(minimum="view")`.
- ``suite:edit`` — `require_permission(minimum="edit")`, which
  `suite_authz._cap_for_viewer` feeds, so a Viewer is refused even holding a
  legacy `edit` share.
- ``role:member`` / ``role:admin`` — `server._require_role`, the coarse ADR 0033
  axis, for capabilities with no suite to hang a resource gate on. Kept as two
  distinct values rather than one "role gate" bucket: collapsing them would let a
  tool declared `role:admin` but implemented with `minimum="member"` pass every
  test, handing an admin capability to every Member.
"""

from __future__ import annotations

#: Tool name → its declared authorization gate. Keep alphabetical within a group.
GATES: dict[str, str] = {
    # ── no per-resource gate ─────────────────────────────────────────────────
    "get_adf_pipeline_status": "read",
    "get_health_score": "read",
    "get_suite_performance": "read",
    "list_connections": "read",
    "list_suites": "read",
    # ── accessible-suite scoped, and view-gated when a suite is named ────────
    "list_runs": "read:suite-optional",
    "list_schedules": "read:suite-optional",
    "list_trigger_bindings": "read:suite-optional",
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
    "delete_check": "suite:edit",
    "dryrun_check": "suite:edit",
    "profile_column": "suite:edit",
    "snooze_check": "suite:edit",
    "trigger_suite_run": "suite:edit",
    "update_check": "suite:edit",
}

#: Gates whose tools must refuse a **Viewer**.
VIEWER_DENIED_GATES = frozenset({"suite:edit", "role:member", "role:admin"})

#: Gates whose tools must refuse a **Member** — admin-only capabilities. Separate
#: from the Viewer set precisely so `role:admin` cannot be satisfied by a
#: `minimum="member"` implementation.
MEMBER_DENIED_GATES = frozenset({"role:admin"})

#: Gates whose tools must refuse a user with **no share on the named suite**.
#: Covers `read:suite-optional` too: its up-front gate is the half that turns a
#: misleading empty list into an honest denial.
OUTSIDER_DENIED_GATES = frozenset({"suite:view", "suite:edit", "read:suite-optional"})

#: Every value that may appear in `GATES`. A typo'd gate would otherwise silently
#: match no sweep and be enforced by nothing.
KNOWN_GATES = frozenset(
    {"read", "read:suite-optional", "suite:view", "suite:edit", "role:member", "role:admin"}
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
