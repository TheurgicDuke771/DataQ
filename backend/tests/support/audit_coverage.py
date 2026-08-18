"""Every mutating `/api/v1` route's audit disposition — one table, driven off the
REAL route table (ADR 0041 §2.8, #1318).

An explicit `audit_service` call at each mutation is the right mechanism — it is
the only one that can distinguish a principal's deliberate act from a machine
write, which is decision 1 of the ADR — but a new endpoint that forgets the call
is invisible. So this file declares the disposition of every mutating route, and
`tests/api/test_audit_coverage.py` compares that declaration against
`app.openapi()`.

**Enumerating what FastAPI actually serves, rather than an audit registry, is the
load-bearing part.** ADR 0039's orphan-secret sweep shipped an introspection guard
that iterated *the models already registered with it*, making a new model
invisible to the very check meant to catch it — a tautology. A route appears in
the served surface whether or not anyone remembered the audit, so a new endpoint
lands here as a *failing test* demanding a decision, which is the point.

**What this table does and does not prove.** It proves a decision was *taken* for
every mutating route, and it catches a route added with none. It does not, by
itself, prove an `AUDITED` route really writes an event — that is what the
per-entity tests in `tests/api/test_audit_events.py` do, by exercising the route
and asserting the row. Both halves are needed and neither substitutes for the
other; saying so here means the limit is recorded rather than assumed away.

The exemption reasons are deliberately written out rather than grouped under a
heading. "Not a config mutation" is a judgement, and a judgement a reader cannot
audit is indistinguishable from an oversight.
"""

from __future__ import annotations

from typing import Final

#: Routes that MUST record an audit event, mapped to the `action` they write.
#: The value is the action string, so this table doubles as the vocabulary — a
#: verb invented at a call site and not declared here shows up as a mismatch.
AUDITED: Final[dict[tuple[str, str], str]] = {
    # ── Connections. `reauth` is called out in ADR 0020 as a known unrecorded
    # hole: a credential rotation left no trace of any kind. It records THAT the
    # credential rotated and WHICH pointer, never a before/after of the value.
    ("POST", "/api/v1/connections"): "connection.create",
    ("PATCH", "/api/v1/connections/{connection_id}"): "connection.update",
    ("DELETE", "/api/v1/connections/{connection_id}"): "connection.delete",
    ("POST", "/api/v1/connections/{connection_id}/reauth"): "connection.reauth",
    # ── Suites. Deletion cascades checks → runs → results and is irrecoverable
    # (#540); the audit event is the only surviving record of what was destroyed,
    # which is the whole reason `entity_id` carries no foreign key.
    ("POST", "/api/v1/suites"): "suite.create",
    ("PATCH", "/api/v1/suites/{suite_id}"): "suite.update",
    ("DELETE", "/api/v1/suites/{suite_id}"): "suite.delete",
    ("POST", "/api/v1/suites/import"): "suite.import",
    # ── The redaction policy. A change here changes what personal data the
    # product will surface, so it is among the highest-value events in the table.
    ("PUT", "/api/v1/suites/{suite_id}/column-policy"): "suite.column_policy_update",
    # ── Checks.
    ("POST", "/api/v1/suites/{suite_id}/checks"): "check.create",
    ("PATCH", "/api/v1/suites/{suite_id}/checks/{check_id}"): "check.update",
    ("DELETE", "/api/v1/suites/{suite_id}/checks/{check_id}"): "check.delete",
    ("POST", "/api/v1/suites/{suite_id}/checks/{check_id}/snooze"): "check.snooze",
    ("DELETE", "/api/v1/suites/{suite_id}/checks/{check_id}/snooze"): "check.unsnooze",
    (
        "POST",
        "/api/v1/suites/{suite_id}/checks/{check_id}/versions/{version_no}/restore",
    ): "check.restore",
    ("POST", "/api/v1/suites/{suite_id}/checks/{check_id}/rebaseline"): "check.rebaseline",
    # ── Shares (ADR 0027 grants). ADR 0041 calls these the highest-value rows in
    # the table and notes they were completely un-recorded: granting or revoking
    # the finest-grained permission in the product left no trace at all.
    ("POST", "/api/v1/suites/{suite_id}/shares"): "share.grant",
    ("PATCH", "/api/v1/suites/{suite_id}/shares/{user_id}"): "share.update",
    ("DELETE", "/api/v1/suites/{suite_id}/shares/{user_id}"): "share.revoke",
    # ── Notifications, schedules, trigger bindings.
    ("PUT", "/api/v1/suites/{suite_id}/notifications"): "suite_notification.update",
    ("DELETE", "/api/v1/suites/{suite_id}/notifications"): "suite_notification.delete",
    ("POST", "/api/v1/schedules"): "schedule.create",
    ("PATCH", "/api/v1/schedules/{schedule_id}"): "schedule.update",
    ("DELETE", "/api/v1/schedules/{schedule_id}"): "schedule.delete",
    ("POST", "/api/v1/trigger-bindings"): "trigger_binding.create",
    ("PATCH", "/api/v1/trigger-bindings/{binding_id}"): "trigger_binding.update",
    ("DELETE", "/api/v1/trigger-bindings/{binding_id}"): "trigger_binding.delete",
    # ── Credentials and identity. An api_key event records the mint/revoke and
    # NEVER the token or its hash (ADR 0041 §2.5).
    ("POST", "/api/v1/me/api-keys"): "api_key.mint",
    ("DELETE", "/api/v1/me/api-keys/{key_id}"): "api_key.revoke",
    ("PATCH", "/api/v1/me"): "user.profile_update",
    # ── The privilege change. ADR 0041 §2.5 lists this as "prospective — ADR 0033
    # unbuilt", which was true when the ADR was written and is not now: `users.role`
    # shipped with #740-#742, and the change emits a structured LOG line with no
    # durable row. ADR 0033 §7 requires the durable record, so this is not optional.
    ("PATCH", "/api/v1/admin/users/{user_id}/role"): "user.role_change",
    # ── Asset metadata (owner/description only — the inventory-sync columns are
    # machine writes and are not audited).
    ("PATCH", "/api/v1/assets/{asset_id}"): "asset.update",
    # ── Incident lifecycle. Both verbs are deliberate acts by a principal about
    # the incident; neither changes the data or re-runs anything.
    ("POST", "/api/v1/incidents/{incident_id}/ack"): "incident.acknowledge",
    ("POST", "/api/v1/incidents/{incident_id}/resolve"): "incident.resolve",
    # ── The third door. This endpoint creates a Connection AND a Suite under a
    # different name, which is exactly how it escaped the ADR-0033 RBAC gates
    # (#1396 review): reasoning that put controls on the resources' own routes was
    # blind to a sibling endpoint creating the same resources. Auditing the
    # resources rather than the route would have had the same blind spot, so it is
    # named here explicitly.
    ("POST", "/api/v1/_probe/snowflake-suite"): "probe.provision",
}

#: Routes that must NOT record a config event, each with the reason. A route is
#: exempt for one of three reasons and the reason is stated per route, not per
#: group, because "not a config mutation" is a judgement and an unexplained
#: judgement is indistinguishable from an oversight.
EXEMPT: Final[dict[tuple[str, str], str]] = {
    # 1. Nothing is persisted. These open a connection or evaluate an expression
    #    and return; there is no configuration afterwards that differs from before.
    ("POST", "/api/v1/connections/test"): (
        "tests an unsaved draft — structurally cannot persist (#1116)"
    ),
    ("POST", "/api/v1/connections/{connection_id}/test"): (
        "an outbound reachability probe; changes no configuration"
    ),
    ("POST", "/api/v1/suites/{suite_id}/checks/dryrun"): (
        "evaluates a check without saving it — the point of a dry run"
    ),
    ("POST", "/api/v1/suites/{suite_id}/profile"): (
        "reads column statistics from the warehouse; writes no configuration"
    ),
    ("POST", "/api/v1/suites/{suite_id}/column-policy/suggest"): (
        "proposes a policy; applying it is the audited PUT above"
    ),
    ("POST", "/api/v1/admin/auth-email/test"): (
        "an SMTP pre-flight send; changes no configuration"
    ),
    # 2. Run lifecycle, not configuration. A run is machine-written state; the
    #    deliberate act is the schedule/binding/suite that produced it, and those
    #    ARE audited. Auditing every run would bury the config events in noise —
    #    ADR 0041 §2.1's central exclusion.
    ("POST", "/api/v1/suites/{suite_id}/run"): (
        "starts a run — run lifecycle, not configuration (ADR 0041 §2.1)"
    ),
    ("POST", "/api/v1/runs/{run_id}/cancel"): (
        "stops a run — run lifecycle, not configuration (ADR 0041 §2.1)"
    ),
    # 3. Session lifecycle, explicitly deferred out of phase 1 by ADR 0041 §2.5
    #    ("sign-in/sign-out session lifecycle is deliberately out of phase 1").
    #    Recorded as DEFERRED rather than exempt-forever: these are acts by a
    #    principal, and a sign-in trail is a real audit requirement — it is simply
    #    not this phase's.
    ("POST", "/api/v1/auth/otp/request"): (
        "session lifecycle — deferred out of phase 1 by ADR 0041 §2.5, not exempt forever"
    ),
    ("POST", "/api/v1/auth/otp/verify"): (
        "session lifecycle — deferred out of phase 1 by ADR 0041 §2.5, not exempt forever"
    ),
    ("POST", "/api/v1/auth/logout"): (
        "session lifecycle — deferred out of phase 1 by ADR 0041 §2.5, not exempt forever"
    ),
    # 4. The principal is an orchestrator reporting machine state into
    #    `pipeline_runs`. ADR 0041 §2.1 excludes machine writes; the webhook has
    #    an `actor_kind` precisely so a FUTURE deliberate act by one is
    #    representable, but reporting a pipeline outcome is not that.
    ("POST", "/api/v1/orchestration/events/adf"): (
        "an orchestrator reporting a pipeline outcome — a machine write (ADR 0041 §2.1)"
    ),
    ("POST", "/api/v1/orchestration/events/airflow"): (
        "an orchestrator reporting a pipeline outcome — a machine write (ADR 0041 §2.1)"
    ),
    ("POST", "/api/v1/orchestration/events/dbt"): (
        "an orchestrator reporting a pipeline outcome — a machine write (ADR 0041 §2.1)"
    ),
}
