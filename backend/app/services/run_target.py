"""Resolve a suite's datasource-shaped target to the runner's (table, schema, catalog)."""

from __future__ import annotations

from typing import Any

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.datasources.base import ResolvedTarget, TargetShapeError
from backend.app.datasources.registry import resolve_target_shape
from backend.app.services.failure_classifier import classify_failure_reason

log = get_logger(__name__)


class SuiteTargetInvalidError(DataQError):
    status_code = 422
    code = "suite_target_invalid"


def resolve_target(conn_type: str, target: dict[str, Any] | None) -> ResolvedTarget:
    """Resolve ``target`` for a ``conn_type`` connection, or raise (422)."""
    if not target:
        raise SuiteTargetInvalidError(
            "suite has no target configured", detail={"connection_type": conn_type}
        )

    # The datasource-specific SHAPE lives with its adapter and runner (#727).
    try:
        return resolve_target_shape(conn_type, target)
    except TargetShapeError as exc:
        raise SuiteTargetInvalidError(str(exc), detail={"connection_type": conn_type}) from exc


def validate_target(conn_type: str, target: dict[str, Any]) -> None:
    """Write-time guard: a non-null target must resolve for its datasource."""
    resolve_target(conn_type, target)


def materialize_path(
    conn_type: str,
    config: dict[str, Any],
    resolved: ResolvedTarget,
    *,
    secret_ref: str | None,
    secret_store: SecretStore,
) -> str:
    """Run-time resolution of ``resolved`` to a concrete table/path."""
    if resolved.batch is None:
        return resolved.table
    if not secret_ref:
        raise SuiteTargetInvalidError(
            "flat-file batch target requires a connection credential to list the store",
            detail={"connection_type": conn_type},
        )
    # Lazy import: flatfile pulls in Great Expectations, which the write-time
    # validation path (suite_service) must not load just to validate a target.
    from backend.app.datasources import flatfile

    spec = resolved.batch
    return flatfile.resolve_batch_file(
        conn_type=conn_type,
        config=dict(config),
        secret=secret_store.get(secret_ref),
        prefix=spec.prefix,
        pattern=spec.pattern,
        strategy=spec.strategy,
        batch=spec.batch,
    )


# ── batch-target preview (#1193) ──────────────────────────────────── The error taxonomy lives
# here, beside the logic that raises it.


class BatchPreviewNoDataError(DataQError):
    status_code = 422
    code = "batch_preview_no_data"


class BatchPreviewInvalidError(DataQError):
    status_code = 422
    code = "batch_preview_invalid"


class BatchPreviewFailedError(DataQError):
    status_code = 502
    code = "batch_preview_failed"


def preview_batch(
    conn_type: str,
    config: dict[str, Any],
    *,
    prefix: str,
    pattern: str,
    strategy: str,
    batch: str | None,
    secret_ref: str | None,
    secret_store: SecretStore,
) -> str:
    """Resolve a batch spec against the live listing, without saving it (#1193)."""
    # Lazy import for the same reason `materialize_path` has one — and because the
    # two flat-file errors are only meaningful once we're on the listing path.
    from backend.app.datasources.flatfile import BatchListingTooLargeError, BatchNotFoundError

    target: dict[str, Any] = {"pattern": pattern, "strategy": strategy, "prefix": prefix}
    if batch is not None:
        target["batch"] = batch
    resolved = resolve_target(conn_type, target)
    try:
        return materialize_path(
            conn_type, config, resolved, secret_ref=secret_ref, secret_store=secret_store
        )
    except BatchNotFoundError as exc:
        # "no data yet" — the same meaning a run gives it (#122) — not a shape
        # problem, so it stays distinct from SuiteTargetInvalidError.
        raise BatchPreviewNoDataError(
            "no file currently matches this batch pattern",
            detail={"connection_type": conn_type},
        ) from exc
    except BatchListingTooLargeError as exc:
        # The only exception whose text is safe to echo: a fixed sentence built
        # from the caller's own prefix and our own limit (`flatfile._counted`).
        raise BatchPreviewInvalidError(str(exc), detail={"connection_type": conn_type}) from exc
    except DataQError:
        # SuiteTargetInvalidError (422) — e.g. a batch target on a connection with no stored
        # credential to list with — already carries the right status/code/message; keep it as-is.
        raise
    except Exception as exc:
        log.warning(
            "batch_preview_failed", connection_type=conn_type, error_type=type(exc).__name__
        )
        raise BatchPreviewFailedError(
            "batch preview could not list the datasource store",
            detail={"reason": classify_failure_reason(exc)},
        ) from exc
