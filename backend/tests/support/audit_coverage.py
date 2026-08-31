"""Every mutating `/api/v1` route's audit disposition — one table, driven off the
REAL route table (ADR 0041 §2.8, #1318).
"""

from __future__ import annotations

from typing import Final

#: Routes that MUST record an audit event, mapped to the `action` they write.
AUDITED: Final[dict[tuple[str, str], str]] = {
    # ── Connections.
    ("POST", "/api/v1/connections"): "connection.create",
    ("PATCH", "/api/v1/connections/{connection_id}"): "connection.update",
    ("DELETE", "/api/v1/connections/{connection_id}"): "connection.delete",
    ("POST", "/api/v1/connections/{connection_id}/reauth"): "connection.reauth",
    # ── Suites.
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
    # ── Shares (ADR 0027 grants).
    ("POST", "/api/v1/suites/{suite_id}/shares"): "share.grant",
    ("PATCH", "/api/v1/suites/{suite_id}/shares/{user_id}"): "share.update",
    ("DELETE", "/api/v1/suites/{suite_id}/shares/{user_id}"): "share.revoke",
    # ── Notifications, schedules, trigger bindings.
    ("PUT", "/api/v1/suites/{suite_id}/notifications"): "suite_notification.update",
    ("DELETE", "/api/v1/suites/{suite_id}/notifications"): "suite_notification.delete",
    # ── Reusable notification channels (#1514).
    ("POST", "/api/v1/notification-channels"): "notification_channel.create",
    ("PATCH", "/api/v1/notification-channels/{channel_id}"): "notification_channel.update",
    ("DELETE", "/api/v1/notification-channels/{channel_id}"): "notification_channel.delete",
    (
        "PUT",
        "/api/v1/suites/{suite_id}/notification-channels/{channel_id}",
    ): "suite_notification_channel.link",
    (
        "DELETE",
        "/api/v1/suites/{suite_id}/notification-channels/{channel_id}",
    ): "suite_notification_channel.unlink",
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
    # ── The privilege change.
    ("PATCH", "/api/v1/admin/users/{user_id}/role"): "user.role_change",
    # ── Asset metadata (owner/description only — the inventory-sync columns are
    # machine writes and are not audited).
    ("PATCH", "/api/v1/assets/{asset_id}"): "asset.update",
    # ── Incident lifecycle. Both verbs are deliberate acts by a principal about
    # the incident; neither changes the data or re-runs anything.
    ("POST", "/api/v1/incidents/{incident_id}/ack"): "incident.acknowledge",
    ("POST", "/api/v1/incidents/{incident_id}/resolve"): "incident.resolve",
    # ── The third door.
    ("POST", "/api/v1/_probe/snowflake-suite"): "probe.provision",
    # ── Data-subject-rights erasure (G2 / #432) — a real mutation over regulated
    # data, so it belongs in AUDITED like any other admin write.
    ("POST", "/api/v1/admin/data-subject-requests/erase"): "data_subject_request.erase",
    # ── The outbound-LLM provider config (ADR 0042) — what model the workspace's
    # data-adjacent context is sent to is among the highest-value config events.
    ("PUT", "/api/v1/admin/llm"): "llm_setting.update",
}

#: Routes that must NOT record a config event, each with the reason.
EXEMPT: Final[dict[tuple[str, str], str]] = {
    # 1. Nothing is persisted. These open a connection or evaluate an expression
    #    and return; there is no configuration afterwards that differs from before.
    ("POST", "/api/v1/connections/test"): (
        "tests an unsaved draft — structurally cannot persist (#1116)"
    ),
    ("POST", "/api/v1/admin/llm/test"): (
        "live-probes an unsaved LLM config draft — structurally cannot persist (ADR 0042)"
    ),
    ("POST", "/api/v1/llm/sql_generation"): (
        "an operational LLM invocation, not configuration — the llm_invocations row "
        "IS the durable record (requester, timing, tokens; ADR 0042)"
    ),
    ("POST", "/api/v1/llm/check_suggestions"): (
        "an operational LLM invocation, not configuration — the llm_invocations row "
        "IS the durable record (requester, timing, tokens; ADR 0042, #1513)"
    ),
    ("POST", "/api/v1/llm/rca_narrative"): (
        "an operational LLM invocation, not configuration — the llm_invocations row "
        "IS the durable record (requester, timing, tokens; ADR 0042, #1633); it also "
        "saves nothing to the suite it narrates, unlike the two above"
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
    # 2.
    ("POST", "/api/v1/suites/{suite_id}/run"): (
        "starts a run — run lifecycle, not configuration (ADR 0041 §2.1)"
    ),
    ("POST", "/api/v1/runs/{run_id}/cancel"): (
        "stops a run — run lifecycle, not configuration (ADR 0041 §2.1)"
    ),
    # 3.
    ("POST", "/api/v1/auth/otp/request"): (
        "session lifecycle — deferred out of phase 1 by ADR 0041 §2.5, not exempt forever"
    ),
    ("POST", "/api/v1/auth/otp/verify"): (
        "session lifecycle — deferred out of phase 1 by ADR 0041 §2.5, not exempt forever"
    ),
    ("POST", "/api/v1/auth/logout"): (
        "session lifecycle — deferred out of phase 1 by ADR 0041 §2.5, not exempt forever"
    ),
    # 4.
    ("POST", "/api/v1/orchestration/events/adf"): (
        "an orchestrator reporting a pipeline outcome — a machine write (ADR 0041 §2.1)"
    ),
    ("POST", "/api/v1/orchestration/events/airflow"): (
        "an orchestrator reporting a pipeline outcome — a machine write (ADR 0041 §2.1)"
    ),
    ("POST", "/api/v1/orchestration/events/dbt"): (
        "an orchestrator reporting a pipeline outcome — a machine write (ADR 0041 §2.1)"
    ),
    # 5. A read, not a config mutation — POST only because it takes a body. Records
    #    an `action_class='access'` event (audit_service.record_access) instead,
    #    same as run_results.read.
    ("POST", "/api/v1/admin/data-subject-requests/export"): (
        "reads regulated data across suites; records an access event, not a config one"
    ),
}
