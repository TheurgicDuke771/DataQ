"""The gated OpenLineage client + pure ``RunEvent`` builders (ADR 0034, #758).

**Dark by default.** :func:`is_emission_configured` reads typed ``Settings`` fields
(``openlineage_url`` / ``openlineage_disabled``) so a value in ``.env.app`` (which
the process env never sees) still activates emission — plus the two library-owned
advanced-transport env vars for the config-file path. With nothing set (or
``OPENLINEAGE_DISABLED`` truthy) the client is never constructed, no openlineage
transport is imported, and nothing is emitted. Mirrors ``core.otel`` (gate + lazy
imports) and ``alerting.registry`` (lock-guarded cached singleton + a test reset).

The builders are **pure** (no I/O): they turn a loaded run graph into a
``RunEvent`` object. All openlineage imports are lazy (via :func:`_ol_event_v2` /
inside the functions) so an unconfigured deployment never pays the import cost —
the builders run only once a client is configured (dispatch returns first on the
dark path), matching the repo convention (``secrets.py`` / ``otel.py``).

**PII discipline (hard rule):** this module never reads ``Result.sample_failures``,
``observed_value`` (beyond the single aggregate ``row_count`` key), or
``expected_value``. Only the assertion outcome (pass/fail) + severity and a volume
``rowCount`` leave the process. The scalar ``metric_value`` is deliberately **not**
emitted (a volume monitor's ``metric_value`` is a banded deviation %, not the
count). Some GX expectations put *actual failing column values* in
``observed_value``, so it is excluded from the assertion facet — see
:func:`_build_assertions`, the single statement of this rationale.

**Fork-safety:** the cached client is reset per prefork child via a
``worker_process_init`` handler in ``worker.celery_app`` (the #405 / ``core.tracing``
prefork precedent) so a child never inherits a parent-constructed client.
"""

from __future__ import annotations

import os
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.db.models import FAILING_TIERS

if TYPE_CHECKING:  # annotations only — never imported on the dark path
    from openlineage.client import OpenLineageClient
    from openlineage.client.event_v2 import RunEvent

    from backend.app.db.models import Asset, Check, Result, Run, Suite

log = get_logger(__name__)

# Stamped on every event. The producer identifies DataQ as the emitting system;
# `dataq` is the OpenLineage job namespace, the job name is the stable suite id.
_PRODUCER = "https://github.com/TheurgicDuke771/DataQ"
_JOB_NAMESPACE = "dataq"

# OpenLineage facet keys (spec-standard names).
_FACET_ASSERTIONS = "dataQualityAssertions"
_FACET_METRICS = "dataQualityMetrics"
_FACET_ERROR = "errorMessage"
_FACET_DOCUMENTATION = "documentation"

# HTTP transport read timeout (seconds) for the constructed client — bounds a
# degraded OL receiver so an emit can't stall a run beyond this.
_EMIT_TIMEOUT_SECONDS = 5.0

# Advanced, library-owned transport config (a transport dict / config file). Kept
# in raw env — the client resolves these itself; the plain URL path goes through
# typed Settings so `.env.app` activates it too.
_ADVANCED_TRANSPORT_ENV_VARS = ("OPENLINEAGE__TRANSPORT__TYPE", "OPENLINEAGE_CONFIG")

# Run status → terminal OpenLineage RunState. A non-terminal status here means the
# run never reached a terminal state (a crash/reap after START) — map it to FAIL so
# a START is never left dangling. cancelled → ABORT, succeeded → COMPLETE.
_TERMINAL_STATES = {"succeeded": "COMPLETE", "failed": "FAIL", "cancelled": "ABORT"}
# Failing result tiers → OpenLineage assertion severity, derived from the #657
# single source (``FAILING_TIERS``) so a future tier can't silently drop its
# severity here. `warn` stays `warn`; every other failing tier is an `error`.
# `pass`/`skip`/`error` (operational) carry no severity (omitted).
_SEVERITY_MAP = {tier: ("warn" if tier == "warn" else "error") for tier in FAILING_TIERS}

# Lock-guarded cached singleton. `_client_configured` distinguishes "not yet
# attempted / retry" from "attempted, cached None" (the dark path). A construction
# failure does NOT latch (it retries next call); only "unconfigured" and "built"
# latch. `_warned` keeps the construction-failure warning to once per process.
_client: OpenLineageClient | None = None
_client_configured = False
_warned = False
_client_lock = threading.Lock()

_event_v2_module: Any = None


def _ol_event_v2() -> Any:
    """Memoized import of ``openlineage.client.event_v2``.

    Never imported on the dark path — the builders that call this run only once a
    client is configured (``dispatch`` returns before building otherwise).
    """
    global _event_v2_module
    if _event_v2_module is None:
        from openlineage.client import event_v2

        _event_v2_module = event_v2
    return _event_v2_module


def is_emission_configured() -> bool:
    """True iff a transport is configured AND emission isn't explicitly disabled.

    ``openlineage_disabled`` (from ``OPENLINEAGE_DISABLED``) forces the dark path
    even with a URL set. Otherwise emission is on when ``openlineage_url`` is set,
    or one of the library-owned advanced-transport vars
    (``OPENLINEAGE__TRANSPORT__TYPE`` / ``OPENLINEAGE_CONFIG``) is present.
    """
    settings = get_settings()
    if settings.openlineage_disabled:
        return False
    if settings.openlineage_url:
        return True
    return any(os.environ.get(var) for var in _ADVANCED_TRANSPORT_ENV_VARS)


def _build_client() -> OpenLineageClient:
    """Construct the client — a URL gets a bounded-timeout HTTP transport; the
    advanced path lets the library resolve its own transport from the env."""
    from openlineage.client import OpenLineageClient, OpenLineageClientOptions

    settings = get_settings()
    if settings.openlineage_url:
        return OpenLineageClient(
            url=settings.openlineage_url,
            options=OpenLineageClientOptions(timeout=_EMIT_TIMEOUT_SECONDS),
        )
    return OpenLineageClient()


def get_openlineage_client() -> OpenLineageClient | None:
    """The cached ``OpenLineageClient``, or ``None`` when emission is unconfigured.

    Built once behind a lock and cached. Unconfigured → cached ``None`` with no
    openlineage import (the dark path). Construction is fail-open **and does not
    latch**: a bad transport config logs a warning once (``_warned``) and returns
    ``None`` without caching it, so a transient failure self-heals on the next call
    rather than going dark for the process lifetime.
    """
    global _client, _client_configured, _warned
    if _client_configured:
        return _client
    with _client_lock:
        if _client_configured:
            return _client
        if not is_emission_configured():
            _client = None
            _client_configured = True  # dark path latches (cheap, stable)
            return None
        try:
            client = _build_client()
        except Exception:
            # Bad transport config (bad URL scheme, unreadable config file, …) must
            # not fail a run. Warn once, and DON'T latch — retry on the next call.
            if not _warned:
                log.warning("openlineage_client_init_failed", exc_info=True)
                _warned = True
            return None
        _client = client
        _client_configured = True
        log.info("openlineage_client_initialized")
        return client


def reset_openlineage_client_cache() -> None:
    """Reset the cached client so the next call re-evaluates config.

    Used by the ``worker_process_init`` fork-safety handler (so a prefork child
    never inherits a parent-built client) and by tests.
    """
    global _client, _client_configured, _warned
    with _client_lock:
        _client = None
        _client_configured = False
        _warned = False


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
    passed (``status == "pass"`` — every failing tier is ``False``); ``severity`` =
    the mapped failing tier. Operational ``skip`` (not evaluated) and ``error`` (the
    check could not run — bad connection, GX crash) are **omitted**: neither is a
    data-quality verdict, so emitting them as ``success=False`` would read
    downstream as a false "the data failed this check". Returns ``None`` when no
    assertion survives so the facet is left off rather than emitted empty.

    Carries no ``actual``/``expected`` — the module-docstring PII rule.
    """
    from openlineage.client.facet_v2 import data_quality_assertions_dataset as dqa

    checks_by_id = {check.id: check for check in checks}
    assertions = []
    for result in results:
        if result.status in ("skip", "error"):
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
    graph: tuple[list[Check], list[Result]] | None = None,
) -> list[Any]:
    """The event's input datasets — the target asset, when the asset row exists.

    Only when ``asset`` is present is there a dataset to name (its OpenLineage
    ``namespace``/``name``). ``graph`` = ``(checks, results)`` (terminal only)
    attaches the DQ input facets; a START event passes ``None`` and emits a bare
    input dataset.
    """
    if asset is None:
        return []
    input_facets: dict[str, Any] = {}
    if graph is not None:
        checks, results = graph
        assertions = _build_assertions(checks, results)
        if assertions is not None:
            input_facets[_FACET_ASSERTIONS] = assertions
        metrics = _build_metrics(checks, results)
        if metrics is not None:
            input_facets[_FACET_METRICS] = metrics
    return [
        _ol_event_v2().InputDataset(
            namespace=asset.namespace, name=asset.name, inputFacets=input_facets
        )
    ]


def _job(suite: Suite) -> Any:
    """The OpenLineage ``Job`` for a suite: the **stable, unique** ``suite.<id>`` as
    the job name (suite names are renameable and not unique — keying on them forks
    or interleaves run histories), with the human-readable ``suite.name`` carried in
    a ``DocumentationJobFacet`` for consumer display."""
    from openlineage.client.facet_v2 import documentation_job

    return _ol_event_v2().Job(
        namespace=_JOB_NAMESPACE,
        name=f"suite.{suite.id}",
        facets={
            _FACET_DOCUMENTATION: documentation_job.DocumentationJobFacet(description=suite.name)
        },
    )


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
    ev = _ol_event_v2()
    return ev.RunEvent(
        eventTime=event_time,
        producer=_PRODUCER,
        run=ev.Run(runId=str(run.id), facets=run_facets or {}),
        job=_job(suite),
        eventType=event_type,
        inputs=inputs,
    )


def build_start_event(run: Run, suite: Suite, asset: Asset | None) -> RunEvent:
    """A ``START`` ``RunEvent`` for ``run`` (no results yet → bare input dataset)."""
    return _run_event(
        run,
        suite,
        event_type=_ol_event_v2().RunState.START,
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

    The event type maps from ``run.status`` (a non-terminal status — a crashed/reaped
    run that emitted START but never reached terminal — maps to FAIL so the START is
    never left dangling). The input dataset (when the asset exists) carries the
    assertions + volume-metrics facets. A ``failed`` run adds an
    ``ErrorMessageRunFacet`` from ``run.failure_reason`` — the classified,
    redaction-safe string, never raw exception text (and never a stack trace).
    """
    event_type = getattr(_ol_event_v2().RunState, _TERMINAL_STATES.get(run.status, "FAIL"))
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
        inputs=_input_datasets(asset, (checks, results)),
        run_facets=run_facets,
    )
