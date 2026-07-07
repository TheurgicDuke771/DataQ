"""Check dry-run — execute one ad-hoc check against live data, persist nothing.

The "preview before saving" path for the check editor: build the datasource
runner for the suite's connection, run a single `CheckSpec` against the suite's
run target, and map the outcome to a preview (severity tier + the
SQL-aggregatable metric + observed/expected), **without** creating a `Run` or
`Result`. Reuses the severity derivation (ADR 0005/0016) and JSON sanitisation
that the persisted run path uses.

The runner and the target are resolved exactly like the worker run path
(`build_check_runner` registry + `run_target`), so dry-run works on every
datasource that has a `CheckRunner` — Snowflake, Unity Catalog, and flat files
(ADLS / S3 / local) — with no per-type branching here (#532). Only
`expectation` checks are previewable (ADR 0012); other kinds are a 422.

Synchronous + blocking (datasource connect + GX): the API runs it in a threadpool.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from backend.app.core.errors import DataQError
from backend.app.core.jsonsafe import sanitize_json
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.datasources.base import CheckSpec
from backend.app.datasources.flatfile import BatchNotFoundError
from backend.app.datasources.registry import (
    UnsupportedConnectionTypeError,
    build_check_runner,
)
from backend.app.db.models import Connection
from backend.app.services import run_target
from backend.app.services.custom_sql import validate_custom_sql_check
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
    status: str  # pass | warn | fail | critical | error (#122 — unevaluable check)
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
) -> DryRunOutcome:
    """Run one check against the suite's run ``target`` and return a preview.

    ``target`` is the suite's run target (#215); it is resolved and materialized
    the same way a persisted run does, so the preview runs against exactly what a
    saved run would.

    Clean 422s (not 500s): a non-`expectation` kind, a targetless suite, an
    orchestration-provider connection (no runner), a malformed target, or a
    non-read-only custom-SQL query (ADR 0019). A flat-file *batch* target whose
    file hasn't landed yet is `DryRunNoDataError` (422). `DryRunFailedError`
    (502) if the run can't execute (no credential, unreachable datasource, bad
    expectation). The adapter exception is never echoed — it can carry
    DSN/credential fragments.
    """
    if kind != _EXPECTATION_KIND:
        raise DryRunUnsupportedError(
            f"dry-run supports only 'expectation' checks; got {kind!r}", detail={"kind": kind}
        )
    # Resolve the target the same way the run path does. Raises
    # SuiteTargetInvalidError (422) for a targetless suite, a malformed target, or
    # an orchestration-provider connection (never a datasource) — so the old
    # per-type _SUPPORTED_TYPES gate is unnecessary.
    resolved = run_target.resolve_target(connection.type, target)
    # Dry-run is the one path that *executes* the query before save, so the
    # custom-SQL read-only guardrail (ADR 0019) must apply here too — outside the
    # try, so a bad query is a clean 422, not a 502. No-op for other expectations.
    validate_custom_sql_check(
        expectation_type=expectation_type,
        config=config,
        connection_type=connection.type,
    )

    try:
        runner = build_check_runner(
            conn_type=connection.type,
            config=connection.config,
            secret_ref=connection.secret_ref,
            secret_store=secret_store,
            catalog=resolved.catalog,
        )
    except UnsupportedConnectionTypeError as exc:
        # Defensive: resolve_target already rejects non-datasource types, so this
        # is only reachable if the runner registry drifts from the adapter set.
        raise DryRunUnsupportedError(
            f"dry-run is not supported for {connection.type!r} connections",
            detail={"type": connection.type},
        ) from exc
    except Exception as exc:
        # The builders resolve the secret eagerly — a missing/unreadable
        # credential fails here, and is a datasource-side 502 (as it was before
        # #532, when build + run shared one guard), never an opaque 500.
        log.warning(
            "dry_run_failed", connection_type=connection.type, error_type=type(exc).__name__
        )
        raise DryRunFailedError("dry run could not connect to the datasource") from exc

    # Materialize a flat-file batch target to a concrete file (lists the store) —
    # a no-op for SQL / UC / literal flat-file targets. Batch-not-found is "no data
    # yet", a clean 422; a bad credential / unreachable store while listing is a 502.
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
        raise DryRunFailedError("dry run could not list the datasource store") from exc

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
            "dry run could not execute against the datasource", detail={"table": table}
        ) from exc

    status, metric = resolve_status(
        check_outcome,
        warn_threshold=warn_threshold,
        fail_threshold=fail_threshold,
        critical_threshold=critical_threshold,
    )
    # Preview exactly what a persisted run would record: an unevaluable check
    # (#122) is 'error', not a misleading 'fail' tag, and surfaces the GX message.
    if check_outcome.errored:
        observed = {"error": check_outcome.error_message} if check_outcome.error_message else None
    else:
        observed = sanitize_json(check_outcome.observed_value)
    return DryRunOutcome(
        status=status,
        metric_value=metric,
        observed_value=observed,
        expected_value=sanitize_json(check_outcome.expected_value),
    )
