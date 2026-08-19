"""The read-side coverage guard — G1 / #431.

Finds every caller of `run_service`'s redactors (the seam every path surfacing a
failing-row sample or `observed_value` must go through) and asserts each is
declared in `tests/support/access_coverage` as audited or explicitly exempt.

Scans the real source rather than a registry, for the reason ADR 0039's
orphan-secret sweep made concrete: a guard that iterates its own registrations
cannot see the thing it was built to catch.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

from backend.tests.support.access_coverage import AUDITED, EXEMPT

_APP: Final[Path] = Path(__file__).resolve().parents[2] / "app"

#: The redaction seam. A path surfacing sample rows or an observed value must go
#: through one of these — that is the #226/#415/#1115 rule, and it is what makes
#: this scan meaningful rather than a keyword search.
_REDACTORS: Final[frozenset[str]] = frozenset(
    {
        "redact_sample_failures",
        "redact_sample_failures_with_state",
        "redact_observed_value",
        # The live-probe seam (#1419/#1479) is a SECOND door onto regulated data
        # — the profiler returns real cell values and never touches the three
        # redactors above, so without this name the profiler routes would be
        # invisible to this guard. That is precisely the blind spot the module
        # docstring warns about ("a future path that hand-rolls its own masking"),
        # so the fix is to widen the seam definition rather than to accept it.
        "mask_profile_columns",
        # The dry-run doors call this wrapper rather than `redact_observed_value`
        # directly (it adds the scalar case, #1482). Without the name here the
        # scan stops seeing them and the reverse guard — "a declaration naming a
        # site that no longer exists" — fires, which is how this was caught.
        "redact_probe_observed_value",
    }
)

#: `run_service` defines the redactors; auditing their own definitions would be
#: circular. `audit_service` is the recorder, not a consumer.
_SKIP_MODULES: Final[frozenset[str]] = frozenset(
    {
        "services/run_service.py",
        "services/audit_service.py",
        # Defines `mask_profile_columns`; auditing its own definition is circular,
        # exactly as for the redactors above.
        "services/live_probe.py",
    }
)


def _redactor_callers() -> set[str]:
    """`module::enclosing_function` for every call of a redactor in the app."""
    found: set[str] = set()
    for path in sorted(p for p in _APP.rglob("*.py") if "__pycache__" not in p.parts):
        rel = str(path.relative_to(_APP))
        if rel in _SKIP_MODULES:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call):
                    continue
                name = inner.func.attr if isinstance(inner.func, ast.Attribute) else None
                if isinstance(inner.func, ast.Name):
                    name = inner.func.id
                if name in _REDACTORS:
                    found.add(f"{rel}::{node.name}")
    return found


def test_the_scan_actually_finds_redactor_callers() -> None:
    """The guard's own guard.

    Every assertion below is vacuously true against a scan that finds nothing —
    a changed import shape, a matcher that never matches, an empty file list. The
    floor is deliberately loose so that ADDING a caller does not fail this test;
    the coverage test below is the one that should fail, with a message saying
    what to do.
    """
    assert len(_redactor_callers()) >= 3


def test_every_regulated_data_read_has_a_declared_disposition() -> None:
    """A new path that surfaces failing rows must be audited or exempted."""
    declared = set(AUDITED) | set(EXEMPT)
    undeclared = _redactor_callers() - declared
    assert not undeclared, (
        "these call sites surface regulated data with no declared audit "
        f"disposition: {sorted(undeclared)} — add each to AUDITED (with the "
        "`entity.verb` action it records) or EXEMPT (with the reason it is not a "
        "principal's read of stored results) in "
        "backend/tests/support/access_coverage.py. G1 / #431."
    )


def test_the_declaration_carries_no_call_sites_that_no_longer_exist() -> None:
    """A stale row is worse than a missing one: it makes the table look complete
    while describing a surface that has moved."""
    stale = (set(AUDITED) | set(EXEMPT)) - _redactor_callers()
    assert not stale, (
        f"these declared call sites no longer call a redactor: {sorted(stale)} — "
        "remove or rename them in backend/tests/support/access_coverage.py"
    )


def test_no_call_site_is_both_audited_and_exempt() -> None:
    both = set(AUDITED) & set(EXEMPT)
    assert not both, f"declared both AUDITED and EXEMPT: {sorted(both)}"


def test_every_exemption_states_a_reason() -> None:
    """An unexplained exemption is indistinguishable from an oversight — which is
    the whole thing this guard exists to make impossible."""
    thin = {site: reason for site, reason in EXEMPT.items() if len(reason.strip()) < 25}
    assert not thin, f"exemptions with no substantive reason: {sorted(thin)}"
