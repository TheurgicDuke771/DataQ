"""Severity post-processing — derive a result's tier from check thresholds.

The single home for the ADR-0005 tier derivation + the ADR-0012 `metric_value`
extraction, kept pure (no DB, no GX) so the semantics live in one place: if we
ever switch from banding the *unexpected fraction* to banding the *raw observed
value* (ADR 0016 approach B), only this module changes.

Model (ADR 0016, approach A):
- `metric_value` = the GX **unexpected percent** (0-100): how badly the rule was
  violated. 0 = clean, higher = worse. It is the SQL-aggregatable badness scalar
  (ADR 0012) and the quantity the thresholds band.
- When a check has thresholds, they are the user's severity policy and they
  fully determine the tier — overriding GX's binary success (e.g. a check with
  `mostly=0.99` that GX fails at 0.5% unexpected resolves to `pass` if the user's
  `warn` threshold is 1%). The stored `metric_value` keeps this transparent.
- No thresholds set, or no metric to band (aggregate checks like row_count that
  produce no unexpected fraction) → binary `pass`/`fail` from GX success
  (ADR 0005 binary fallback).

Custom-SQL checks (ADR 0019, #1202): `UnexpectedRowsExpectation` isn't a column
map expectation, so it never reports `unexpected_percent` — but it *does* report
the unexpected **row count** via `observed_value` (0 on success, the count on
failure). That count is read as the bandable metric instead, deliberately as a
raw count and NOT a percentage of the batch:

- a percentage would need a second aggregate query (the batch's total row count)
  issued by the runner — `severity.py` is pure (no DB/GX access) and ADR 0019
  §Consequences already scoped this as "additive... no schema change", which
  reads as no new runner query either;
- more importantly, the usual argument for a percentage — comparability across
  tables of different sizes — doesn't apply to how this metric is consumed: the
  severity thresholds and the anomaly baseline (#593) are always evaluated
  per-CHECK, against that same check's own history on the same query/table,
  never against a different check's metric. A raw count trends and baselines
  exactly as well as a percentage would for that one check, at zero extra query
  cost.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from backend.app.datasources.base import CheckOutcome
from backend.app.services.custom_sql import CUSTOM_SQL_EXPECTATION_TYPE

# GX key carrying the violated-row fraction (0-100), copied into sample_failures
# by the runner. This is the badness scalar the thresholds band.
_UNEXPECTED_PERCENT_KEY = "unexpected_percent"

# GX result key carrying the unexpected row COUNT for a custom-SQL check
# (`UnexpectedRowsExpectation` has no `unexpected_percent`) — the fallback
# badness scalar for that one expectation type only (#1202).
_OBSERVED_VALUE_KEY = "observed_value"


def extract_metric(outcome: CheckOutcome) -> Decimal | None:
    """The numeric badness scalar for a check, or None if it has none.

    A *monitor* (freshness/volume, ADR 0012) computes its metric directly, so its
    ``outcome.metric_value`` is preferred when set. Otherwise this reads the GX
    unexpected-percent from the outcome's sample detail; a custom-SQL check (ADR
    0019, #1202) carries no unexpected-percent, so it falls through to the
    unexpected row COUNT in ``observed_value`` instead (see the module docstring
    for why count, not percentage). Computed at run time and persisted to
    `results.metric_value`, so it survives the later sample-failures retention
    purge (the durable scalar the dashboard trends). Uses ``Decimal(str(...))``
    so a float like ``0.5`` lands as exact ``0.5`` in the NUMERIC column rather
    than its binary expansion.
    """
    raw: Any
    if outcome.metric_value is not None:
        raw = outcome.metric_value
    else:
        sample: dict[str, Any] | None = outcome.sample_failures
        if sample and sample.get(_UNEXPECTED_PERCENT_KEY) is not None:
            raw = sample[_UNEXPECTED_PERCENT_KEY]
        elif outcome.expectation_type == CUSTOM_SQL_EXPECTATION_TYPE:
            observed = outcome.observed_value
            observed_count = observed.get(_OBSERVED_VALUE_KEY) if observed else None
            if observed_count is None:
                return None
            raw = observed_count
        else:
            return None
    try:
        metric = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None
    # GX can emit NaN/inf — e.g. unexpected_percent on an empty table is 0/0
    # (the same reason run_service runs a NaN->null sanitizer). A non-finite
    # Decimal slips past the parse but would make every threshold comparison
    # False (silently 'pass') or raise mid-run; treat it as "no bandable
    # metric" so derive_status falls back to binary pass/fail.
    return metric if metric.is_finite() else None


def resolve_status(
    outcome: CheckOutcome,
    *,
    warn_threshold: Decimal | None,
    fail_threshold: Decimal | None,
    critical_threshold: Decimal | None,
) -> tuple[str, Decimal | None]:
    """Resolve a check outcome to its persisted ``(status, metric_value)``.

    The single decision both run-result persistence (`run_service`) and the
    check-editor dry-run (`dryrun_service`) share, so a preview can never disagree
    with the run it previews:

    * a check the runner could not *evaluate* (`outcome.errored`, #122) is the
      operational ``error`` status — no severity tier, no metric to band;
    * a check whose *precondition* wasn't met (`outcome.skipped`, #593 — an
      anomaly monitor still short of `min_points` history) is the operational
      ``skip`` status, also untiered and unbanded. It lives here rather than in
      the caller so a dry-run preview can never disagree with the run it
      previews, which is this function's whole reason to exist; vs.
    * an evaluated check, whose unexpected-% metric is banded into a tier
      (ADR 0005 / 0016).

    Both operational statuses persist ``metric_value`` as NULL **on purpose**: a
    cold-start z-score would be a number computed from too little history, and
    trending it (or baselining on it) would launder a non-measurement into data.
    """
    if outcome.errored:
        return "error", None
    if outcome.skipped:
        return "skip", None
    metric = extract_metric(outcome)
    status = derive_status(
        success=outcome.success,
        metric_value=metric,
        warn_threshold=warn_threshold,
        fail_threshold=fail_threshold,
        critical_threshold=critical_threshold,
    )
    return status, metric


def derive_status(
    *,
    success: bool,
    metric_value: Decimal | None,
    warn_threshold: Decimal | None,
    fail_threshold: Decimal | None,
    critical_threshold: Decimal | None,
) -> str:
    """Resolve a result's tier (`pass`/`warn`/`fail`/`critical`), ADR 0005.

    Higher `metric_value` is worse; thresholds are ordered (warn ≤ fail ≤
    critical) and any unset threshold is skipped (treated as +∞). Falls back to
    binary pass/fail when the check carries no thresholds, or carries thresholds
    but produced no bandable metric.

    Authoring-time ordering/sign validation lives in
    `check_service.validate_threshold_ordering` (#568), not here — this function
    stays pure and has no DB access to enforce it against. Its own tolerance for
    a bad set that predates that guard (or is written directly, bypassing it):
    the cascade below checks `critical` FIRST, then `fail`, then `warn`, so an
    inverted set (e.g. warn=90/fail=50/critical=10) doesn't crash — it just
    degrades to a surprising-but-defined band (a metric of 60 hits `critical`
    here even though a human reading "critical=10" alone wouldn't expect it).
    """
    no_thresholds = warn_threshold is None and fail_threshold is None and critical_threshold is None
    if no_thresholds or metric_value is None:
        return "pass" if success else "fail"

    if critical_threshold is not None and metric_value >= critical_threshold:
        return "critical"
    if fail_threshold is not None and metric_value >= fail_threshold:
        return "fail"
    if warn_threshold is not None and metric_value >= warn_threshold:
        return "warn"
    return "pass"
