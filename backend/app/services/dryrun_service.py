"""Check dry-run — execute one ad-hoc check against live data, persist nothing.

The "preview before saving" path for the check editor: build the datasource
runner for the suite's connection, run a single `CheckSpec`, and map the outcome
to a preview (severity tier + the SQL-aggregatable metric + observed/expected),
**without** creating a `Run` or `Result`. Reuses the severity derivation
(ADR 0005/0016) and JSON sanitisation that the persisted run path uses.

v1 limits: only `expectation` checks (ADR 0012) and only Snowflake connections
have a `CheckRunner` (the connection-type runner dispatch generalises in Week 5,
ADR 0011). Both are 422s — a client can't dry-run what there's no runner for.

Synchronous + blocking (Snowflake connect + GX): the API runs it in a threadpool.
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
from backend.app.datasources.snowflake import build_snowflake_runner
from backend.app.db.models import Connection
from backend.app.services.severity import derive_status, extract_metric

log = get_logger(__name__)

_EXPECTATION_KIND = "expectation"
# v1: only Snowflake has a CheckRunner. Other types get a clear 422 until the
# connection-type runner dispatch generalises (Week 5).
_SUPPORTED_TYPES = {"snowflake"}


class DryRunUnsupportedError(DataQError):
    status_code = 422
    code = "dry_run_unsupported"


class DryRunFailedError(DataQError):
    status_code = 502
    code = "dry_run_failed"


@dataclass(frozen=True)
class DryRunOutcome:
    status: str  # pass | warn | fail | critical
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
    table: str,
    schema: str | None,
    secret_store: SecretStore,
) -> DryRunOutcome:
    """Run one check against the connection's `table` and return a preview.

    Raises `DryRunUnsupportedError` (422) for a non-`expectation` kind or a
    connection type with no runner, and `DryRunFailedError` (502) if the run
    can't execute (no credential, unreachable warehouse, bad expectation). The
    adapter exception is never echoed — it can carry DSN/credential fragments.
    """
    if kind != _EXPECTATION_KIND:
        raise DryRunUnsupportedError(
            f"dry-run supports only 'expectation' checks; got {kind!r}", detail={"kind": kind}
        )
    if connection.type not in _SUPPORTED_TYPES:
        raise DryRunUnsupportedError(
            f"dry-run is not supported for {connection.type!r} connections in v1",
            detail={"type": connection.type, "supported": sorted(_SUPPORTED_TYPES)},
        )

    try:
        runner = build_snowflake_runner(
            config=connection.config,
            secret_ref=connection.secret_ref,
            secret_store=secret_store,
        )
        outcome = runner.run_checks(
            table=table,
            schema=schema,
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

    metric = extract_metric(check_outcome)
    status = derive_status(
        success=check_outcome.success,
        metric_value=metric,
        warn_threshold=warn_threshold,
        fail_threshold=fail_threshold,
        critical_threshold=critical_threshold,
    )
    return DryRunOutcome(
        status=status,
        metric_value=metric,
        observed_value=sanitize_json(check_outcome.observed_value),
        expected_value=sanitize_json(check_outcome.expected_value),
    )
