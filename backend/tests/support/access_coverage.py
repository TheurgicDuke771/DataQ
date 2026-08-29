"""Every surface that can return regulated data to a caller, and whether it is
audited — G1 / #431, `action_class='access'`.
"""

from __future__ import annotations

from typing import Final

#: Call sites that surface regulated data and MUST record an access event,
#: mapped to the `action` they write.
AUDITED: Final[dict[str, str]] = {
    # The main read path: a run's per-check results, including failing-row
    # samples. One event per read, not per result.
    "api/v1/runs.py::_result_read": "run_results.read",
    # The same data leaving as a FILE, which is the more consequential door —
    # a download leaves the product entirely.
    "api/v1/runs.py::download_comparison_report": "comparison_report.download",
    # Both MCP result tools funnel through one payload builder, which is where
    # the event is written — a second copy is a second place to forget it.
    "mcp/server.py::_run_results_payload": "run_results.read",
    # The per-result renderer `_run_results_payload` delegates to.
    "mcp/server.py::_redacted_sample": "run_results.read",
    # ── live probes (#1419/#1479) ──────────────────────────────────────────── These read the
    # WAREHOUSE and persist nothing.
    "api/v1/checks.py::dry_run_check": "check.dryrun",
    "api/v1/suites.py::profile_columns": "column.profile",
    "mcp/server.py::profile_column": "column.profile",
    # Worker-side prompt enrichment for LLM SQL-gen (ADR 0042): EGRESS rung,
    # always masked, warehouse-tag floor applied. Its sibling `_schema_context`
    # calls no redactor (name-only egress) so it cannot appear here — its
    # column.list event is pinned by test_llm_sqlgen instead.
    "services/llm_sqlgen.py::_profile_context": "column.profile",
    "mcp/server.py::dryrun_check": "check.dryrun",
}

#: Redactor call sites that do NOT record an access event, each with the reason.
EXEMPT: Final[dict[str, str]] = {
    # Not a read at all — the WRITE path, redacting on the way into an alert.
    "alerting/builder.py::build_run_report": (
        "the alert-delivery write path, not a principal's read — no actor to attribute"
    ),
}

# NOTE — one entry was REMOVED from EXEMPT rather than edited, and the reason is worth keeping.
