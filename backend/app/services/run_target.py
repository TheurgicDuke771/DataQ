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

`resolve_target` is the run-time path (raises if a required field is missing).
`validate_target` is the write-time path (same rules; used by `suite_service`
when a target is set on a suite, so a malformed/wrong-datasource target is a
clean 422 at save rather than a failed run later).

FastAPI-free: takes a connection type + the stored dict, raises `DataQError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.core.errors import DataQError

_FLATFILE_TYPES = {"adls_gen2", "s3"}


class SuiteTargetInvalidError(DataQError):
    status_code = 422
    code = "suite_target_invalid"


@dataclass(frozen=True)
class ResolvedTarget:
    """The runner inputs a suite resolves to. ``table`` carries the file path for
    flat-file datasources; ``catalog`` is set only for Unity Catalog."""

    table: str
    schema: str | None
    catalog: str | None


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
