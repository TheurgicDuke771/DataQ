"""Resolve a suite's datasource-shaped target to the runner's (table, schema, catalog)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.core.config import get_settings
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


class BatchPreviewFailedError(DataQError):
    status_code = 502
    code = "batch_preview_failed"


@dataclass(frozen=True)
class BatchPreviewOutcome:
    """The preview's own answer shape (#1243) — deliberately distinct from the
    run path's plain `str` path, because a preview may legitimately stop before
    it can be sure. ``path`` is the best match seen within budget (or ``None``);
    ``truncated`` says whether the object/wall-clock budget cut the scan short,
    so a caller can render "no match in the first N objects (there may be more)"
    instead of a false "no match anywhere".
    """

    path: str | None
    scanned: int
    truncated: bool


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
) -> BatchPreviewOutcome:
    """Resolve a batch spec against the live listing, without saving it (#1193).

    Unlike the run path (`materialize_path` → `flatfile.resolve_batch_file`,
    bounded only by the worker-scale `_BATCH_LISTING_MAX`), this runs
    synchronously in the API process's threadpool, so it uses its own much
    tighter object-count + wall-clock budget (`Settings.batch_preview_max_*`,
    #1243) and never raises `BatchListingTooLargeError` — a budget-truncated
    scan is an honest partial answer, not a failure.
    """
    # Lazy import for the same reason `materialize_path` has one.
    from backend.app.datasources import flatfile

    target: dict[str, Any] = {"pattern": pattern, "strategy": strategy, "prefix": prefix}
    if batch is not None:
        target["batch"] = batch
    resolved = resolve_target(conn_type, target)
    if resolved.batch is None:
        # A flat-file batch target always resolves through this function; a literal
        # `path` target has no listing to preview.
        return BatchPreviewOutcome(path=resolved.table, scanned=0, truncated=False)
    if not secret_ref:
        raise SuiteTargetInvalidError(
            "flat-file batch target requires a connection credential to list the store",
            detail={"connection_type": conn_type},
        )
    spec = resolved.batch
    settings = get_settings()
    try:
        result = flatfile.resolve_batch_file_preview(
            conn_type=conn_type,
            config=dict(config),
            secret=secret_store.get(secret_ref),
            prefix=spec.prefix,
            pattern=spec.pattern,
            strategy=spec.strategy,
            batch=spec.batch,
            max_objects=settings.batch_preview_max_objects,
            max_seconds=settings.batch_preview_max_seconds,
        )
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

    if result.path is None and not result.truncated:
        # The listing was exhausted (within budget) and nothing matched — the
        # same definitive "no data yet" meaning a run gives it (#122), not a
        # shape problem, so it stays distinct from SuiteTargetInvalidError.
        raise BatchPreviewNoDataError(
            "no file currently matches this batch pattern",
            detail={"connection_type": conn_type},
        )
    return BatchPreviewOutcome(path=result.path, scanned=result.scanned, truncated=result.truncated)
