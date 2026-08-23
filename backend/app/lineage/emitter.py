"""The gated OpenLineage client + pure ``RunEvent`` builders (ADR 0034, #758)."""

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

# Advanced, library-owned transport config (a transport dict / config file).
_ADVANCED_TRANSPORT_ENV_VARS = ("OPENLINEAGE__TRANSPORT__TYPE", "OPENLINEAGE_CONFIG")

# Run status → terminal OpenLineage RunState.
_TERMINAL_STATES = {"succeeded": "COMPLETE", "failed": "FAIL", "cancelled": "ABORT"}
# Failing result tiers → OpenLineage assertion severity, derived from the #657 single source
# (``FAILING_TIERS``) so a future tier can't silently drop its severity here.
_SEVERITY_MAP = {tier: ("warn" if tier == "warn" else "error") for tier in FAILING_TIERS}

# Lock-guarded cached singleton.
_client: OpenLineageClient | None = None
_client_configured = False
_warned = False
_client_lock = threading.Lock()

_event_v2_module: Any = None


def _ol_event_v2() -> Any:
    """Memoized import of ``openlineage.client.event_v2``."""
    global _event_v2_module
    if _event_v2_module is None:
        from openlineage.client import event_v2

        _event_v2_module = event_v2
    return _event_v2_module


def is_emission_configured() -> bool:
    """True iff a transport is configured AND emission isn't explicitly disabled."""
    settings = get_settings()
    if settings.openlineage_disabled:
        return False
    if settings.openlineage_url:
        return True
    return any(os.environ.get(var) for var in _ADVANCED_TRANSPORT_ENV_VARS)


def _build_client() -> OpenLineageClient:
    """Construct the client — a URL gets a bounded-timeout HTTP transport; the
    advanced path lets the library resolve its own transport from the env.
    """
    from openlineage.client import OpenLineageClient, OpenLineageClientOptions

    settings = get_settings()
    if settings.openlineage_url:
        return OpenLineageClient(
            url=settings.openlineage_url,
            options=OpenLineageClientOptions(timeout=_EMIT_TIMEOUT_SECONDS),
        )
    return OpenLineageClient()


def get_openlineage_client() -> OpenLineageClient | None:
    """The cached ``OpenLineageClient``, or ``None`` when emission is unconfigured."""
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
    """Reset the cached client so the next call re-evaluates config."""
    global _client, _client_configured, _warned
    with _client_lock:
        _client = None
        _client_configured = False
        _warned = False


# ─────────────────────────────── event builders ────────────────────────────────


def _event_time(run: Run, *, start: bool) -> str:
    """A tz-aware ISO timestamp for the event."""
    moment = run.started_at if start else (run.finished_at or run.started_at)
    moment = moment or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.isoformat()


def _severity_for(status: str) -> str | None:
    """OpenLineage assertion severity for a result status (or None to omit)."""
    return _SEVERITY_MAP.get(status)


def _build_assertions(checks: list[Check], results: list[Result]) -> Any:
    """A ``DataQualityAssertionsDatasetFacet`` — one assertion per (check, result)."""
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
    """A ``DataQualityMetricsInputDatasetFacet`` from the first ``volume`` monitor."""
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
    """The event's input datasets — the target asset, when the asset row exists."""
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
    a ``DocumentationJobFacet`` for consumer display.
    """
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
    """A terminal ``RunEvent`` (COMPLETE / FAIL / ABORT) with the DQ facets."""
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
