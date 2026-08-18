"""Every surface that can return regulated data to a caller, and whether it is
audited — G1 / #431, `action_class='access'`.

The mutation side has `audit_coverage.py`, whose guard enumerates FastAPI's route
table so a new endpoint arrives as a failing test. **The read side cannot be
enumerated the same way**, and saying why matters more than the table itself: a
mutating route is identifiable by its HTTP verb, but a *read* of regulated data is
identifiable only by what it returns. `GET /runs/{id}` exposes failing rows and
`GET /suites` does not, and nothing in the route table distinguishes them.

So the enumeration is anchored on the **redaction seam** instead. Every path that
can surface a failing-row sample or an `observed_value` must call one of
`run_service`'s redactors to do it — that is the standing rule the sample-PII work
established (#226/#415/#1115), and it is enforced by the fact that the raw column
never leaves the ORM row any other way. `test_access_coverage.py` therefore finds
every caller of those redactors and asserts each is declared here.

That is a weaker guarantee than the route-table guard and it is written down
rather than glossed: a future path that reads `sample_failures` and hand-rolls its
own masking would be invisible to it. What makes that acceptable is that such a
path would already be a redaction bug — a second, unreviewed masking
implementation on the product's PII seam — and the #415 work exists precisely to
keep the redactors the only door.
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
    # The per-result renderer `_run_results_payload` delegates to. Declared
    # separately because the scan sees the redactor call HERE — and pointing the
    # declaration at the caller instead would be a claim about a line the guard
    # never checks.
    "mcp/server.py::_redacted_sample": "run_results.read",
}

#: Redactor call sites that do NOT record an access event, each with the reason.
#: Stated per site rather than per group: "not a read of regulated data" is a
#: judgement, and an unexplained judgement is indistinguishable from an oversight.
EXEMPT: Final[dict[str, str]] = {
    # Not a read at all — the WRITE path, redacting on the way into an alert.
    # The recipient is a webhook or mailbox configured by an admin, not a
    # principal making a request, so there is no actor to attribute and no
    # access decision being exercised. Auditing it would record the product
    # talking to itself.
    "alerting/builder.py::build_run_report": (
        "the alert-delivery write path, not a principal's read — no actor to attribute"
    ),
    # A live evaluation the caller explicitly asked for, whose values are
    # transient and never persisted. It is the same class of act as running a
    # check, and it reads the WAREHOUSE rather than a stored result — so it is
    # outside G1's subject (reads of results/samples DataQ has retained). Worth
    # revisiting if the scope of the audit widens from "what we stored" to
    # "everything we ever showed".
    "mcp/server.py::dryrun_check": (
        "a live warehouse evaluation the caller initiated; reads no stored result"
    ),
}
