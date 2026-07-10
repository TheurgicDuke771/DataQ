"""The env-gated OpenLineage client + pure ``RunEvent`` builders (ADR 0034, #758).

**Dark by default.** :func:`is_emission_configured` reads the OpenLineage transport
env vars *directly* (the ``EnvSecretStore`` precedent — no fields added to
``Settings``); with none set (or ``OPENLINEAGE_DISABLED`` truthy) the client is
never constructed, no openlineage transport is imported, and nothing is emitted.
Mirrors ``core.otel`` (env gate + lazy imports) and ``alerting.registry``
(lock-guarded cached singleton + a test-only reset).

The builders are **pure** (no I/O): they turn a loaded run graph into a
``RunEvent`` object. All openlineage imports are lazy (inside the functions) so an
unconfigured deployment never pays the import cost — matching the repo convention
(``secrets.py`` / ``otel.py``).

**PII discipline (hard rule):** this module never reads ``Result.sample_failures``.
The scalar ``metric_value`` and the assertion outcome are the only run data that
leave the process; ``observed_value`` / ``expected_value`` are deliberately
excluded from the assertion facet (some GX expectations put *actual failing column
values* in ``observed_value``, so it can carry PII — see :func:`_build_assertions`).
"""

from __future__ import annotations

import os
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from backend.app.core.logging import get_logger

if TYPE_CHECKING:  # annotations only — never imported on the dark path
    from openlineage.client import OpenLineageClient
    from openlineage.client.event_v2 import RunEvent

    from backend.app.db.models import Asset, Check, Result, Run, Suite

log = get_logger(__name__)

# Stamped on every event. The producer identifies DataQ as the emitting system;
# `dataq` is the OpenLineage job namespace, the job name is the suite's name.
_PRODUCER = "https://github.com/TheurgicDuke771/DataQ"
_JOB_NAMESPACE = "dataq"

# OpenLineage facet keys (spec-standard names).
_FACET_ASSERTIONS = "dataQualityAssertions"
_FACET_METRICS = "dataQualityMetrics"
_FACET_ERROR = "errorMessage"

# Env vars that signal "a transport is configured". Read directly from the
# environment (never Settings) so the library resolves its own transport from the
# same vars when we construct the client.
_TRANSPORT_ENV_VARS = ("OPENLINEAGE_URL", "OPENLINEAGE__TRANSPORT__TYPE", "OPENLINEAGE_CONFIG")
_TRUTHY = frozenset({"1", "true", "yes"})

# Run status → terminal OpenLineage RunState. Anything unexpected maps to OTHER
# (defensive — terminal statuses are only succeeded/failed/cancelled).
_TERMINAL_STATES = {"succeeded": "COMPLETE", "failed": "FAIL", "cancelled": "ABORT"}
# Failing result tiers → OpenLineage assertion severity. `pass`/`skip`/`error`
# carry no severity (omitted); `error` is operational, not a severity tier.
_SEVERITY_MAP = {"warn": "warn", "fail": "error", "critical": "error"}

# Lock-guarded cached singleton. `_client_configured` distinguishes "not yet
# attempted" from "attempted, cached None" (unconfigured or a bad transport).
_client: OpenLineageClient | None = None
_client_configured = False
_client_lock = threading.Lock()


def is_emission_configured() -> bool:
    """True iff a transport is configured AND emission isn't explicitly disabled.

    ``OPENLINEAGE_DISABLED`` (``1``/``true``/``yes``, case-insensitive) forces the
    dark path even with a transport set. Otherwise at least one of
    ``OPENLINEAGE_URL`` / ``OPENLINEAGE__TRANSPORT__TYPE`` / ``OPENLINEAGE_CONFIG``
    must be present for emission to be on.
    """
    if os.environ.get("OPENLINEAGE_DISABLED", "").strip().lower() in _TRUTHY:
        return False
    return any(os.environ.get(var) for var in _TRANSPORT_ENV_VARS)


def get_openlineage_client() -> OpenLineageClient | None:
    """The cached ``OpenLineageClient``, or ``None`` when emission is unconfigured.

    Built once behind a lock and cached. Unconfigured → cached ``None`` with no
    openlineage import (the dark path). Construction itself is fail-open: a bad
    user transport config logs a warning once and caches ``None`` rather than
    raising into the run path.
    """
    global _client, _client_configured
    if _client_configured:
        return _client
    with _client_lock:
        if _client_configured:
            return _client
        _client_configured = True
        if not is_emission_configured():
            _client = None
            return None
        try:
            from openlineage.client import OpenLineageClient

            _client = OpenLineageClient()
            log.info("openlineage_client_initialized")
        except Exception:
            # Bad transport config (bad URL scheme, unreadable config file, …) must
            # not fail a run — go dark and log once.
            log.warning("openlineage_client_init_failed", exc_info=True)
            _client = None
        return _client


def reset_openlineage_client_cache() -> None:
    """Test-only: clear the cached client so the next call re-evaluates the env."""
    global _client, _client_configured
    with _client_lock:
        _client = None
        _client_configured = False


# ─────────────────────────────── event builders ────────────────────────────────


def _event_time(run: Run, *, start: bool) -> str:
    """A tz-aware ISO timestamp for the event.

    START uses ``started_at``; a terminal event uses ``finished_at`` (falling back
    to ``started_at``). A missing/naive value falls back to now / is assumed UTC so
    the emitted timestamp is always tz-aware.
    """
    moment = run.started_at if start else (run.finished_at or run.started_at)
    moment = moment or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.isoformat()


def _severity_for(status: str) -> str | None:
    """OpenLineage assertion severity for a result status (or None to omit)."""
    return _SEVERITY_MAP.get(status)


def _build_assertions(checks: list[Check], results: list[Result]) -> Any:
    """A ``DataQualityAssertionsDatasetFacet`` — one assertion per (check, result).

    ``assertion`` = the check's ``expectation_type`` (falling back to its ``kind``);
    ``column`` = ``check.config["column"]`` when a string; ``success`` = the result
    passed (``status == "pass"`` — every failing tier *and* an operational ``error``
    are ``False``); ``severity`` = the mapped failing tier. Skip-status results are
    **omitted** (not evaluated). Returns ``None`` when no assertion survives so the
    facet is left off rather than emitted empty.

    Deliberately carries **no** ``actual``/``expected``: ``observed_value`` can hold
    actual failing column values for some GX expectations, so including it would
    leak data (the PII rule wins over the richer facet).
    """
    from openlineage.client.facet_v2 import data_quality_assertions_dataset as dqa

    checks_by_id = {check.id: check for check in checks}
    assertions = []
    for result in results:
        if result.status == "skip":
            continue
        check = checks_by_id.get(result.check_id)
        if check is None:
            continue
        column = check.config.get("column") if isinstance(check.config, dict) else None
        assertions.append(
            dqa.Assertion(
                assertion=check.expectation_type or check.kind,
                success=result.status == "pass",
                column=column if isinstance(column, str) else None,
                severity=_severity_for(result.status),
            )
        )
    if not assertions:
        return None
    return dqa.DataQualityAssertionsDatasetFacet(assertions=assertions)


def _build_metrics(checks: list[Check], results: list[Result]) -> Any:
    """A ``DataQualityMetricsInputDatasetFacet`` from the first ``volume`` monitor.

    An ADR 0012 volume monitor's ``metric_value`` is the *deviation %* (the
    banded scalar), not the count — the actual count lives in
    ``observed_value["row_count"]`` (monitors.py), an aggregate-only dict for
    volume, so reading this one well-known key stays within the PII rule.
    Returns ``None`` when no volume result carries a usable count. Freshness
    stays in the assertion entry only (no dedicated facet field).
    """
    from openlineage.client.facet_v2 import data_quality_metrics_input_dataset as dqm

    checks_by_id = {check.id: check for check in checks}
    for result in results:
        check = checks_by_id.get(result.check_id)
        if check is None or check.kind != "volume":
            continue
        observed = result.observed_value if isinstance(result.observed_value, dict) else {}
        row_count = observed.get("row_count")
        if not isinstance(row_count, int) or isinstance(row_count, bool):
            continue
        return dqm.DataQualityMetricsInputDatasetFacet(columnMetrics={}, rowCount=row_count)
    return None


def _input_datasets(
    asset: Asset | None,
    checks: list[Check] | None = None,
    results: list[Result] | None = None,
) -> list[Any]:
    """The event's input datasets — the target asset, when the asset row exists.

    Only when ``asset`` is present is there a dataset to name (its OpenLineage
    ``namespace``/``name``). ``checks``/``results`` (terminal only) attach the DQ
    input facets; a START event passes neither and emits a bare input dataset.
    """
    if asset is None:
        return []
    from openlineage.client.event_v2 import InputDataset

    input_facets: dict[str, Any] = {}
    if checks is not None and results is not None:
        assertions = _build_assertions(checks, results)
        if assertions is not None:
            input_facets[_FACET_ASSERTIONS] = assertions
        metrics = _build_metrics(checks, results)
        if metrics is not None:
            input_facets[_FACET_METRICS] = metrics
    return [InputDataset(namespace=asset.namespace, name=asset.name, inputFacets=input_facets)]


def _run_event(
    run: Run,
    suite: Suite,
    *,
    event_type: Any,
    event_time: str,
    inputs: list[Any],
    run_facets: dict[str, Any] | None = None,
) -> RunEvent:
    """Assemble a ``RunEvent`` from the shared job/run identity + the given parts."""
    from openlineage.client.event_v2 import Job, RunEvent
    from openlineage.client.event_v2 import Run as OLRun

    return RunEvent(
        eventTime=event_time,
        producer=_PRODUCER,
        run=OLRun(runId=str(run.id), facets=run_facets or {}),
        job=Job(namespace=_JOB_NAMESPACE, name=suite.name),
        eventType=event_type,
        inputs=inputs,
    )


def build_start_event(run: Run, suite: Suite, asset: Asset | None) -> RunEvent:
    """A ``START`` ``RunEvent`` for ``run`` (no results yet → bare input dataset)."""
    from openlineage.client.event_v2 import RunState

    return _run_event(
        run,
        suite,
        event_type=RunState.START,
        event_time=_event_time(run, start=True),
        inputs=_input_datasets(asset),
    )


def build_terminal_event(
    run: Run,
    suite: Suite,
    asset: Asset | None,
    checks: list[Check],
    results: list[Result],
) -> RunEvent:
    """A terminal ``RunEvent`` (COMPLETE / FAIL / ABORT) with the DQ facets.

    The event type maps from ``run.status``; the input dataset (when the asset
    exists) carries the assertions + volume-metrics facets. A ``failed`` run adds an
    ``ErrorMessageRunFacet`` from ``run.failure_reason`` — the classified,
    redaction-safe string, never raw exception text (and never a stack trace).
    """
    from openlineage.client.event_v2 import RunState

    event_type = getattr(RunState, _TERMINAL_STATES.get(run.status, "OTHER"))
    run_facets: dict[str, Any] = {}
    if run.status == "failed" and run.failure_reason:
        from openlineage.client.facet_v2 import error_message_run

        run_facets[_FACET_ERROR] = error_message_run.ErrorMessageRunFacet(
            message=run.failure_reason, programmingLanguage="python"
        )
    return _run_event(
        run,
        suite,
        event_type=event_type,
        event_time=_event_time(run, start=False),
        inputs=_input_datasets(asset, checks, results),
        run_facets=run_facets,
    )
