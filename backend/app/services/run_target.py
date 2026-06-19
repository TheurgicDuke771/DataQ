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

from dataclasses import dataclass
from typing import Any

from backend.app.core.errors import DataQError
from backend.app.core.secrets import SecretStore

_FLATFILE_TYPES = {"adls_gen2", "s3"}
_BATCH_STRATEGIES = {"latest", "specific"}


class SuiteTargetInvalidError(DataQError):
    status_code = 422
    code = "suite_target_invalid"


@dataclass(frozen=True)
class BatchSpec:
    """An unresolved flat-file batch selector (resolved live by `materialize_path`).

    ``pattern`` is a regex whose first capture group is the batch key; ``strategy``
    is ``latest`` (greatest key) or ``specific`` (``batch`` key); ``prefix`` scopes
    the object listing.
    """

    prefix: str
    pattern: str
    strategy: str
    batch: str | None


@dataclass(frozen=True)
class ResolvedTarget:
    """The runner inputs a suite resolves to. ``table`` carries the file path for
    flat-file datasources; ``catalog`` is set only for Unity Catalog. ``batch`` is
    set only for a flat-file *batch* target, in which case ``table`` is empty until
    `materialize_path` lists the store and resolves the concrete path."""

    table: str
    schema: str | None
    catalog: str | None
    batch: BatchSpec | None = None


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

    if conn_type in _FLATFILE_TYPES:
        # A batch target (regex `pattern`) is resolved to a concrete path at run
        # time; a literal target carries the `path` directly.
        if "pattern" in target:
            return ResolvedTarget(
                table="", schema=None, catalog=None, batch=_batch_spec(target, conn_type)
            )
        path = _require(target, "path", conn_type)
        return ResolvedTarget(table=path, schema=None, catalog=None)

    if conn_type == "snowflake":
        table = _require(target, "table", conn_type)
        return ResolvedTarget(table=table, schema=_str_or_none(target.get("schema")), catalog=None)

    if conn_type == "unity_catalog":
        table = _require(target, "table", conn_type)
        catalog = _require(target, "catalog", conn_type)
        return ResolvedTarget(
            table=table, schema=_str_or_none(target.get("schema")), catalog=catalog
        )

    raise SuiteTargetInvalidError(
        f"connection type {conn_type!r} has no run path (not a datasource)",
        detail={"connection_type": conn_type},
    )


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


def _batch_spec(target: dict[str, Any], conn_type: str) -> BatchSpec:
    """Validate + build a flat-file `BatchSpec` from a batch target (422 on bad shape)."""
    pattern = _require(target, "pattern", conn_type)
    strategy = target.get("strategy", "latest")
    if strategy not in _BATCH_STRATEGIES:
        raise SuiteTargetInvalidError(
            f"batch strategy must be one of {sorted(_BATCH_STRATEGIES)}; got {strategy!r}",
            detail={"connection_type": conn_type, "strategy": strategy},
        )
    batch = target.get("batch")
    if strategy == "specific" and (not isinstance(batch, str) or not batch.strip()):
        raise SuiteTargetInvalidError(
            "batch strategy 'specific' requires a non-empty 'batch' key",
            detail={"connection_type": conn_type, "strategy": strategy},
        )
    prefix = target.get("prefix", "")
    if not isinstance(prefix, str):
        raise SuiteTargetInvalidError(
            "batch target 'prefix' must be a string",
            detail={"connection_type": conn_type},
        )
    return BatchSpec(
        prefix=prefix,
        pattern=pattern,
        strategy=strategy,
        batch=batch if strategy == "specific" else None,
    )


def _require(target: dict[str, Any], field: str, conn_type: str) -> str:
    value = target.get(field)
    if not isinstance(value, str) or not value.strip():
        raise SuiteTargetInvalidError(
            f"target for a {conn_type!r} suite requires a non-empty {field!r}",
            detail={"connection_type": conn_type, "missing": field},
        )
    return value


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
