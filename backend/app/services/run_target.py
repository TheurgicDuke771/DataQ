"""Resolve a suite's datasource-shaped target to the runner's (table, schema, catalog).

A suite's `target` (#215) is a small JSONB document shaped like the column
profiler request (``table`` / ``schema`` / ``catalog`` / ``path`` /
``file_format``), datasource-typed. The `CheckRunner` interface is *table-shaped*
— for a flat-file datasource the file path rides the ``table`` argument
(``flatfile.py``) — so every datasource resolves to the same triple the worker
hands to ``run_service.execute_run`` and ``build_check_runner``:

    snowflake      → table (+ schema)
    unity_catalog  → table (+ schema) + catalog        (catalog.schema.table)
    adls_gen2 / s3 → path  (carried as `table`; schema/catalog unused)

A flat-file target can instead be a **batch** spec — files arrive in batches
(``orders_2026-06-01.csv`` …) and a run targets one of them: ``pattern`` (a regex
whose first capture group is the batch key) + ``strategy`` (``latest`` /
``specific``, with ``batch`` for ``specific``) + an optional ``prefix`` to list
under. The concrete path can only be known by *listing the store*, so it's
resolved at run time (`materialize_path`), not at save time.

Resolution is two-phase so write-time validation stays pure (no network, no GX):

* `resolve_target` (pure) validates the spec and returns the static triple plus,
  for a batch flat-file target, an unresolved `BatchSpec`. `validate_target` is
  the write-time wrapper `suite_service` calls, so a malformed/wrong-datasource
  target is a clean 422 at save.
* `materialize_path` (run-time, may touch the network) turns a `BatchSpec` into a
  concrete file path by listing + resolving the batch; for every other target it
  returns the already-resolved table. It raises `flatfile.BatchNotFoundError`
  when no file matches — the run path maps that to *skipped* results (the data
  hasn't landed yet), not a failure.

FastAPI-free: takes a connection type + the stored dict, raises `DataQError`.
"""

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
    """Resolve ``target`` for a ``conn_type`` connection, or raise (422).

    Raises `SuiteTargetInvalidError` if the suite is targetless, the target is
    missing the field its datasource requires (`path` for flat files, `table`
    for SQL, `catalog` for Unity Catalog), or the connection type has no run path
    (orchestration providers — they are never suite datasources).
    """
    if not target:
        raise SuiteTargetInvalidError(
            "suite has no target configured", detail={"connection_type": conn_type}
        )

    # The datasource-specific SHAPE lives with its adapter and runner (#727). This
    # used to be an `if conn_type ==` chain — a second dispatch site outside the
    # registry that every new datasource had to remember to edit, which quietly
    # falsified registry.py's "adding a datasource is one entry here" contract (the
    # Iceberg addition already had to touch it).
    #
    # What stays here is what is genuinely shared: the targetless check above, and
    # translating a shape complaint into this module's 422 contract — so the
    # datasource layer never has to know about HTTP status codes.
    try:
        return resolve_target_shape(conn_type, target)
    except TargetShapeError as exc:
        raise SuiteTargetInvalidError(str(exc), detail={"connection_type": conn_type}) from exc


def validate_target(conn_type: str, target: dict[str, Any]) -> None:
    """Write-time guard: a non-null target must resolve for its datasource.

    Reuses `resolve_target`'s rules so a target saved on a suite is always
    runnable. Callers only invoke this when a target is *provided* — a suite may
    be created/updated targetless (NULL), which is valid-but-not-yet-runnable.
    """
    resolve_target(conn_type, target)


def materialize_path(
    conn_type: str,
    config: dict[str, Any],
    resolved: ResolvedTarget,
    *,
    secret_ref: str | None,
    secret_store: SecretStore,
) -> str:
    """Run-time resolution of ``resolved`` to a concrete table/path.

    A no-op for SQL and literal flat-file targets (returns ``resolved.table``).
    For a flat-file *batch* target it lists the store under the batch prefix and
    resolves the pattern to one concrete file path — the network-touching step
    that can't run at save time. Raises `flatfile.BatchNotFoundError` when no file
    matches the batch (the caller maps that to skipped results, not a failure).
    """
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


# ── batch-target preview (#1193) ────────────────────────────────────
#
# The error taxonomy lives here, beside the logic that raises it, the same way
# `dryrun_service` owns `DryRunNoDataError`/`DryRunFailedError` — the router is a
# thin pass-through.


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
    """Resolve a batch spec against the live listing, without saving it (#1193).

    Reuses `resolve_target`'s shape validation (regex compiles, ``specific`` has a
    capture group, ...) and `materialize_path`'s live resolution — the exact path a
    saved batch-target suite takes at run time — so the preview an author sees
    before saving can never drift from what a real run would do.

    Raises `SuiteTargetInvalidError` (422) for a malformed spec **and** for a
    connection type that has no flat-file batch shape at all (a batch spec carries
    no ``table``/``path``, so every SQL datasource rejects it, and an orchestration
    provider has no run path) — the type gate is `resolve_target`'s job, not a
    second hardcoded type set here. `BatchPreviewNoDataError` (422) when nothing
    has landed yet, `BatchPreviewInvalidError` (422) when the prefix is too broad
    to scan, and `BatchPreviewFailedError` (502) for anything else — with a
    *classified* reason, never the adapter's own message, which can carry
    DSN/credential/PII fragments (`failure_classifier`).
    """
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
        # SuiteTargetInvalidError (422) — e.g. a batch target on a connection with
        # no stored credential to list with — already carries the right
        # status/code/message; keep it as-is.
        raise
    except Exception as exc:
        log.warning(
            "batch_preview_failed", connection_type=conn_type, error_type=type(exc).__name__
        )
        raise BatchPreviewFailedError(
            "batch preview could not list the datasource store",
            detail={"reason": classify_failure_reason(exc)},
        ) from exc
