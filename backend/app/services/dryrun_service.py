"""Check dry-run — execute one ad-hoc check against live data, persist nothing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from backend.app.core.errors import DataQError
from backend.app.core.jsonsafe import sanitize_json
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.datasources.base import CheckOutcome, CheckSpec
from backend.app.datasources.flatfile import BatchNotFoundError
from backend.app.datasources.monitors import ANOMALY, SCHEMA_DRIFT
from backend.app.datasources.registry import (
    UnsupportedConnectionTypeError,
    build_check_runner,
    owned_runner,
)
from backend.app.datasources.sql import strip_statement_echo
from backend.app.db.models import GX_ENGINE, Connection
from backend.app.services import run_target
from backend.app.services.check_service import (
    reject_thresholds_on_unbanded,
    validate_engine,
    validate_engine_compatibility,
    validate_expectation_check,
    validate_threshold_ordering,
)
from backend.app.services.custom_sql import is_custom_sql, validate_custom_sql_check
from backend.app.services.failure_classifier import safe_failure_reason
from backend.app.services.severity import resolve_status

log = get_logger(__name__)

_EXPECTATION_KIND = "expectation"


class DryRunUnsupportedError(DataQError):
    status_code = 422
    code = "dry_run_unsupported"


class DryRunNoDataError(DataQError):
    status_code = 422
    code = "dry_run_no_data"


class DryRunFailedError(DataQError):
    status_code = 502
    code = "dry_run_failed"


@dataclass(frozen=True)
class DryRunOutcome:
    # pass | warn | fail | critical, plus the operational statuses (#122/#593): `error`
    # (unevaluable) and `skip` (precondition unmet — an anomaly preview, which has no check row and
    # so can never have learned a baseline).
    status: str
    metric_value: Decimal | None
    observed_value: dict[str, Any] | None
    expected_value: dict[str, Any] | None


def dry_run_check(
    connection: Connection,
    *,
    kind: str,
    expectation_type: str,
    config: dict[str, Any],
    warn_threshold: Decimal | None,
    fail_threshold: Decimal | None,
    critical_threshold: Decimal | None,
    target: dict[str, Any] | None,
    secret_store: SecretStore,
    engine: str = GX_ENGINE,
) -> DryRunOutcome:
    """Run one check against the suite's run ``target`` and return a preview."""
    # #568: a preview must never accept a threshold set that a save would reject — same shared
    # validator create_check/update_check use.
    validate_threshold_ordering(
        warn_threshold=warn_threshold,
        fail_threshold=fail_threshold,
        critical_threshold=critical_threshold,
    )
    # #1530: a save would 422 on an engine the connection doesn't offer or can't evaluate this
    # kind/type/config with — a preview must reject the same way, not fall through to the GX
    # runner and 502 blaming the datasource.
    validate_engine(engine, connection_type=connection.type)
    validate_engine_compatibility(
        engine,
        kind=kind,
        expectation_type=expectation_type,
        config=config,
        warn_threshold=warn_threshold,
        fail_threshold=fail_threshold,
        critical_threshold=critical_threshold,
    )
    if engine != GX_ENGINE:
        return _dry_run_native(
            connection,
            engine=engine,
            kind=kind,
            expectation_type=expectation_type,
            config=config,
            warn_threshold=warn_threshold,
            fail_threshold=fail_threshold,
            critical_threshold=critical_threshold,
            target=target,
            secret_store=secret_store,
        )
    if kind == SCHEMA_DRIFT:
        return _dry_run_schema_drift(
            connection, config=config, target=target, secret_store=secret_store
        )
    if kind == ANOMALY:
        return _dry_run_anomaly(connection, config=config, target=target, secret_store=secret_store)
    if kind != _EXPECTATION_KIND:
        raise DryRunUnsupportedError(
            f"dry-run supports only 'expectation', 'schema_drift' and 'anomaly' checks; "
            f"got {kind!r}",
            detail={"kind": kind},
        )
    # Resolve the target the same way the run path does.
    resolved = run_target.resolve_target(connection.type, target)
    # Dry-run is the one path that *executes* the query before save, so the custom-SQL read-only
    # guardrail (ADR 0019) must apply here too — outside the try, so a bad query is a clean 422.
    validate_custom_sql_check(
        expectation_type=expectation_type,
        config=config,
        connection_type=connection.type,
    )
    if not is_custom_sql(expectation_type):
        # #1510, by the same rule as the threshold check above: a preview must not accept what a
        # save would reject. This is also the one author-time door that EXECUTES the expectation
        # against live data with the stored credential, so the vetted set has to hold here too.
        validate_expectation_check(expectation_type, config)
        reject_thresholds_on_unbanded(
            expectation_type,
            warn_threshold=warn_threshold,
            fail_threshold=fail_threshold,
            critical_threshold=critical_threshold,
        )

    try:
        runner = build_check_runner(
            conn_type=connection.type,
            config=connection.config,
            secret_ref=connection.secret_ref,
            secret_store=secret_store,
            catalog=resolved.catalog,
            # The suite target's row cap (#595).
            sampling=resolved.sampling,
        )
    except UnsupportedConnectionTypeError as exc:
        # Defensive: resolve_target already rejects non-datasource types, so this
        # is only reachable if the runner registry drifts from the adapter set.
        raise DryRunUnsupportedError(
            f"dry-run is not supported for {connection.type!r} connections",
            detail={"type": connection.type},
        ) from exc
    except Exception as exc:
        # The builders resolve the secret eagerly — a missing/unreadable credential fails here, and
        # is a datasource-side 502 (as it was before #532, when build + run shared one guard).
        log.warning(
            "dry_run_failed", connection_type=connection.type, error_type=type(exc).__name__
        )
        raise DryRunFailedError(
            "dry run could not connect to the datasource",
            detail={"reason": safe_failure_reason(exc)},
        ) from exc

    # The runner exists from here — `owned_runner` releases its shared engine
    # pool (#427) on every exit path of the dry run.
    with owned_runner(runner):
        # Materialize a flat-file batch target to a concrete file (lists the store) — a no-op for
        # SQL / UC / literal flat-file targets.
        try:
            table = run_target.materialize_path(
                connection.type,
                connection.config,
                resolved,
                secret_ref=connection.secret_ref,
                secret_store=secret_store,
            )
        except BatchNotFoundError as exc:
            raise DryRunNoDataError(
                "no file has landed for the suite's batch target yet — dry-run needs live data",
                detail={"connection_type": connection.type},
            ) from exc
        except DataQError:
            raise  # a SuiteTargetInvalidError (422) from a malformed batch spec — keep it
        except Exception as exc:
            log.warning(
                "dry_run_failed", connection_type=connection.type, error_type=type(exc).__name__
            )
            raise DryRunFailedError(
                "dry run could not list the datasource store",
                detail={"reason": safe_failure_reason(exc)},
            ) from exc

        try:
            outcome = runner.run_checks(
                table=table,
                schema=resolved.schema,
                checks=[CheckSpec(expectation_type=expectation_type, kwargs=dict(config))],
            )
            # One outcome per spec; index inside the guard so a malformed/empty
            # runner result is a clean 502, not an uncaught IndexError → 500.
            check_outcome = outcome.checks[0]
        except Exception as exc:
            log.warning(
                "dry_run_failed", connection_type=connection.type, error_type=type(exc).__name__
            )
            raise DryRunFailedError(
                "dry run could not execute against the datasource",
                # The SAME policy the persisted run path uses (#595): a DataQ-authored
                # `SafeMonitorError` — the scan-cap refusal naming the target, the cap and the knob.
                detail={"table": table, "reason": safe_failure_reason(exc)},
            ) from exc

        status, metric = resolve_status(
            check_outcome,
            warn_threshold=warn_threshold,
            fail_threshold=fail_threshold,
            critical_threshold=critical_threshold,
        )
        # Preview exactly what a persisted run would record: an unevaluable check (#122) is 'error',
        # not a misleading 'fail' tag, and surfaces the GX message.
        if check_outcome.errored:
            error_message = strip_statement_echo(check_outcome.error_message)
            observed = {"error": error_message} if error_message else None
        else:
            observed = sanitize_json(check_outcome.observed_value)
        return DryRunOutcome(
            status=status,
            metric_value=metric,
            observed_value=observed,
            expected_value=sanitize_json(check_outcome.expected_value),
        )


def _dry_run_native(
    connection: Connection,
    *,
    engine: str,
    kind: str,
    expectation_type: str,
    config: dict[str, Any],
    warn_threshold: Decimal | None,
    fail_threshold: Decimal | None,
    critical_threshold: Decimal | None,
    target: dict[str, Any] | None,
    secret_store: SecretStore,
) -> DryRunOutcome:
    """Preview a platform-native (ADR 0036) check via the connection's own
    ``run_native_check`` — the same partition `run_service._run_outcome_phases`
    uses for a persisted run, so a preview never routes a dmf/dqx check through
    the GX runner (#1530).
    """
    resolved = run_target.resolve_target(connection.type, target)
    try:
        runner = build_check_runner(
            conn_type=connection.type,
            config=connection.config,
            secret_ref=connection.secret_ref,
            secret_store=secret_store,
            catalog=resolved.catalog,
            sampling=resolved.sampling,
        )
    except UnsupportedConnectionTypeError as exc:
        raise DryRunUnsupportedError(
            f"dry-run is not supported for {connection.type!r} connections",
            detail={"type": connection.type},
        ) from exc
    except Exception as exc:
        log.warning(
            "dry_run_failed", connection_type=connection.type, error_type=type(exc).__name__
        )
        raise DryRunFailedError(
            "dry run could not connect to the datasource",
            detail={"reason": safe_failure_reason(exc)},
        ) from exc

    with owned_runner(runner):
        native_run = getattr(runner, "run_native_check", None)
        advertised = frozenset(getattr(runner, "supported_native_engines", frozenset()))
        if engine not in advertised or not callable(native_run):
            raise DryRunUnsupportedError(
                f"engine {engine!r} is not available on this connection — the connection "
                "cannot run this platform-native engine (ADR 0036)",
                detail={"engine": engine, "connection_type": connection.type},
            )
        try:
            table = run_target.materialize_path(
                connection.type,
                connection.config,
                resolved,
                secret_ref=connection.secret_ref,
                secret_store=secret_store,
            )
        except BatchNotFoundError as exc:
            raise DryRunNoDataError(
                "no file has landed for the suite's batch target yet — dry-run needs live data",
                detail={"connection_type": connection.type},
            ) from exc
        except DataQError:
            raise
        except Exception as exc:
            log.warning(
                "dry_run_failed", connection_type=connection.type, error_type=type(exc).__name__
            )
            raise DryRunFailedError(
                "dry run could not list the datasource store",
                detail={"reason": safe_failure_reason(exc)},
            ) from exc

        try:
            check_outcome = cast(
                CheckOutcome,
                native_run(
                    kind=kind,
                    expectation_type=expectation_type,
                    config=dict(config),
                    table=table,
                    schema=resolved.schema,
                ),
            )
        except Exception as exc:
            log.warning(
                "dry_run_failed", connection_type=connection.type, error_type=type(exc).__name__
            )
            raise DryRunFailedError(
                "dry run could not execute against the datasource",
                detail={"table": table, "reason": safe_failure_reason(exc)},
            ) from exc

        status, metric = resolve_status(
            check_outcome,
            warn_threshold=warn_threshold,
            fail_threshold=fail_threshold,
            critical_threshold=critical_threshold,
        )
        if check_outcome.errored:
            error_message = strip_statement_echo(check_outcome.error_message)
            observed = {"error": error_message} if error_message else None
        else:
            observed = sanitize_json(check_outcome.observed_value)
        return DryRunOutcome(
            status=status,
            metric_value=metric,
            observed_value=observed,
            expected_value=sanitize_json(check_outcome.expected_value),
        )


def _dry_run_schema_drift(
    connection: Connection,
    *,
    config: dict[str, Any],
    target: dict[str, Any] | None,
    secret_store: SecretStore,
) -> DryRunOutcome:
    """Preview a schema_drift check (#592): introspect the target's live column
    snapshot and report it. The baseline capture/diff itself only happens on
    persisted runs — dry-run has no check row to hold a baseline against, and
    must never write one — so the preview shows WHAT would be baselined.
    """
    from backend.app.datasources.monitors import MonitorConfigError, validate_monitor_config
    from backend.app.services import schema_drift as schema_drift_service

    try:
        validate_monitor_config(SCHEMA_DRIFT, config)
    except MonitorConfigError as exc:
        raise DryRunUnsupportedError(str(exc)[:500], detail={"kind": SCHEMA_DRIFT}) from exc
    resolved = run_target.resolve_target(connection.type, target)
    try:
        table = run_target.materialize_path(
            connection.type,
            connection.config,
            resolved,
            secret_ref=connection.secret_ref,
            secret_store=secret_store,
        )
    except BatchNotFoundError as exc:
        raise DryRunNoDataError(
            "no file has landed for the suite's batch target yet — dry-run needs live data",
            detail={"connection_type": connection.type},
        ) from exc
    except DataQError:
        raise  # a SuiteTargetInvalidError (422) from a malformed batch spec — keep it
    except Exception as exc:
        log.warning(
            "dry_run_failed", connection_type=connection.type, error_type=type(exc).__name__
        )
        raise DryRunFailedError(
            "dry run could not list the datasource store",
            detail={"reason": safe_failure_reason(exc)},
        ) from exc
    try:
        columns = schema_drift_service.introspect_columns(
            connection,
            table=table,
            schema=resolved.schema,
            catalog=resolved.catalog,
            secret_store=secret_store,
        )
    except schema_drift_service.SchemaIntrospectionError as exc:
        raise DryRunFailedError(str(exc), detail={"table": table}) from exc
    ignore = {str(name).lower() for name in (config.get("ignore_columns") or ())}
    considered = [c for c in columns if c["name"].lower() not in ignore]
    return DryRunOutcome(
        status="pass",
        metric_value=None,
        observed_value={
            "columns": considered,
            "columns_checked": len(considered),
            "preview": (
                "baseline is captured on the first persisted run; later runs diff against it"
            ),
        },
        expected_value={"monitor": SCHEMA_DRIFT},
    )


def _dry_run_anomaly(
    connection: Connection,
    *,
    config: dict[str, Any],
    target: dict[str, Any] | None,
    secret_store: SecretStore,
) -> DryRunOutcome:
    """Preview an anomaly check (#593): take the live measurement, score it against
    the history the check would have, and report the outcome — writing nothing.
    """
    from backend.app.datasources.monitors import (
        ANOMALY,
        MonitorConfigError,
        anomaly_params,
        monitor_outcome,
    )
    from backend.app.services import anomaly as anomaly_service

    try:
        params = anomaly_params(config)
    except MonitorConfigError as exc:
        raise DryRunUnsupportedError(str(exc)[:500], detail={"kind": ANOMALY}) from exc
    resolved = run_target.resolve_target(connection.type, target)
    now = datetime.now(UTC)
    try:
        value = anomaly_service.measure_metric(
            connection,
            table=resolved.table,
            schema=resolved.schema,
            catalog=resolved.catalog,
            params=params,
            secret_store=secret_store,
            now=now,
        )
    except MonitorConfigError as exc:
        # DataQ-authored and safe to echo (a bad column, a non-SQL datasource, an
        # empty table) — the actionable half of a failed preview.
        raise DryRunFailedError(str(exc), detail={"table": resolved.table}) from exc
    except Exception as exc:
        log.warning(
            "dry_run_failed", connection_type=connection.type, error_type=type(exc).__name__
        )
        raise DryRunFailedError(
            "dry run could not measure the anomaly target metric",
            detail={"table": resolved.table, "reason": safe_failure_reason(exc)},
        ) from exc
    payload = anomaly_service.build_score_payload(value, [], params)
    payload["dry_run"] = True
    payload["preview"] = (
        "the baseline is learned from persisted runs; this check stays skipped until it "
        f"has {params.min_points} observations"
    )
    outcome = monitor_outcome(ANOMALY, scalar=payload, config=config, now=now)
    # No thresholds: the preview's status comes from the outcome's own operational state (`skip`).
    status, metric = resolve_status(
        outcome, warn_threshold=None, fail_threshold=None, critical_threshold=None
    )
    return DryRunOutcome(
        status=status,
        metric_value=metric,
        observed_value=sanitize_json(outcome.observed_value),
        expected_value=sanitize_json(outcome.expected_value),
    )
