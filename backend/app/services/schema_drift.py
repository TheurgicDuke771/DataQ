"""schema_drift monitor kind — the stateful baseline-diff engine (#592, ADR 0012).

Unlike the scalar kinds (freshness/volume), schema_drift compares the target's
CURRENT column shape against a persisted reference (`monitor_baselines`, #876):
the first run captures the baseline; every later run diffs against it and the
drifted-column count becomes the ADR-0016-banded ``metric_value``. Re-baselining
deletes the stored row — the next run recaptures from the live target, so the
API never runs datasource introspection on a request thread.

The pieces:

* :func:`introspect_columns` — the live column-name/type snapshot per datasource
  (SQL via ``information_schema``, flat-file via the Parquet footer / a CSV
  header sample, Iceberg from table metadata — no data scan anywhere except the
  CSV dtype sample).
* :func:`diff_schemas` — the pure diff (added / removed / type_changed), unit-
  testable without a datasource or DB.
* :func:`build_schema_drift_executor` — the per-run closure the worker injects
  into ``run_service._run_outcomes`` (the comparison pattern, #794): it owns the
  session, the baseline row, and introspection, and returns one
  ``CheckOutcome`` per check via the registry's outcome strategy. Runners never
  see stateful kinds — they have no DB.

Type strings are the datasource's own spelling (``NUMBER(38,0)``, ``int64``,
``string``): they are compared for equality within one datasource, never across
datasources, so no cross-dialect normalisation is attempted. Baselines are
metadata about the target's shape — no row data, no PII.

Known limits, stated up front: CSV types come from pandas inference over a
bounded row sample, so a value-shape change inside the sampled window (a NULL
appearing in an int column → ``float64``) reports a type change even though the
feed's declared schema never moved — ``ignore_columns`` or a re-baseline is the
workaround for known-noisy columns. And the flat-file paths read the object
through the existing ``download_bytes`` seam (like the profiler), so a Parquet
footer read still downloads the whole blob today — a range-read is a filed
follow-up, not a promise this module keeps yet.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.datasources.base import CheckOutcome
from backend.app.datasources.flatfile import download_bytes, read_csv_bytes
from backend.app.datasources.iceberg import (
    IcebergConfig,
    iceberg_credentials,
    load_iceberg_table,
)
from backend.app.datasources.monitors import (
    SCHEMA_DRIFT,
    monitor_expectation_type,
    monitor_outcome,
)
from backend.app.db.models import Check, Connection, MonitorBaseline
from backend.app.services.failure_classifier import classify_failure_reason
from backend.app.services.profile_service import (
    _open_connection,
    infer_file_format,
    resolve_effective_schema,
    validate_identifier,
)

log = get_logger(__name__)

# One column of a schema snapshot: {"name": str, "type": str}.
ColumnSpec = dict[str, str]

_SQL_TYPES = frozenset({"snowflake", "unity_catalog"})
_FILE_TYPES = frozenset({"adls_gen2", "s3"})
# How many CSV rows the dtype inference samples — a header-only read types every
# column `object`, which would report a phantom type change on the first run
# after baselining from a sampled read. Small, bounded, header-adjacent.
_CSV_TYPE_SAMPLE_ROWS = 1000


class SchemaIntrospectionError(Exception):
    """The target's columns could not be introspected (unreachable datasource,
    missing table/file). Carries a CLASSIFIED message — safe for result rows."""


def diff_schemas(
    baseline: list[ColumnSpec],
    current: list[ColumnSpec],
    *,
    ignore_columns: list[str] | None = None,
) -> dict[str, Any]:
    """The pure schema diff: added / removed / type_changed vs the baseline.

    Names are compared exactly as introspected (both sides come from the same
    datasource path, so their spelling is consistent); ``ignore_columns``
    matches case-insensitively — it is user-typed, and a case-mismatched ignore
    silently ignoring nothing would be a footgun.
    """
    ignore = {name.lower() for name in (ignore_columns or ())}
    base = {c["name"]: c["type"] for c in baseline if c["name"].lower() not in ignore}
    cur = {c["name"]: c["type"] for c in current if c["name"].lower() not in ignore}
    added = sorted(set(cur) - set(base))
    removed = sorted(set(base) - set(cur))
    type_changed = sorted(
        (
            {"column": name, "from": base[name], "to": cur[name]}
            for name in set(base) & set(cur)
            if base[name] != cur[name]
        ),
        key=lambda change: change["column"],
    )
    return {
        "added": added,
        "removed": removed,
        "type_changed": type_changed,
        "columns_checked": len(cur),
    }


# ───────────────────── per-datasource introspection ─────────────────────


def _sql_columns(
    connection: Connection,
    *,
    table: str,
    schema: str | None,
    catalog: str | None,
    secret_store: SecretStore,
) -> list[ColumnSpec]:
    """Column names+types from ``information_schema.columns`` (Snowflake / UC).

    Identifiers are allowlist-validated before the (only) interpolation — the
    optional catalog prefix; the schema/table land as BOUND parameters, matched
    case-insensitively to mirror how the engines resolve unquoted identifiers
    (the profiler's known #476 casing limits apply here identically).
    """
    effective_schema = resolve_effective_schema(connection, schema)
    validate_identifier(table)
    validate_identifier(effective_schema)
    if catalog is not None:
        validate_identifier(catalog)
    prefix = f"{catalog}." if catalog else ""
    query = text(
        f"SELECT table_schema, table_name, column_name, data_type "  # noqa: S608  # nosec B608 — identifiers validated; values bound
        f"FROM {prefix}information_schema.columns "
        "WHERE UPPER(table_schema) = UPPER(:schema_name) "
        "AND UPPER(table_name) = UPPER(:table_name) "
        "ORDER BY ordinal_position"
    )
    with _open_connection(connection, secret_store) as conn:
        rows = conn.execute(query, {"schema_name": effective_schema, "table_name": table}).all()
    if not rows:
        raise SchemaIntrospectionError(
            f"table {table!r} not found in information_schema (schema {effective_schema!r})"
        )
    # Case-insensitive matching mirrors how the engines resolve unquoted
    # identifiers — but it can match SEVERAL quoted case-variant objects (ORDERS
    # and "Orders"), and merging their columns would baseline a union schema no
    # real table has. Prefer the exact spelling; otherwise demand exactly one
    # distinct object, and refuse (classified) when the reference is ambiguous.
    by_object: dict[tuple[str, str], list[ColumnSpec]] = {}
    for row_schema, row_table, name, data_type in rows:
        by_object.setdefault((str(row_schema), str(row_table)), []).append(
            {"name": str(name), "type": str(data_type)}
        )
    exact = by_object.get((effective_schema, table))
    if exact is not None:
        return exact
    if len(by_object) > 1:
        raise SchemaIntrospectionError(
            f"table reference {table!r} is ambiguous: {len(by_object)} case-variant "
            "objects match in information_schema — quote/spell the exact name"
        )
    return next(iter(by_object.values()))


def _file_columns(
    connection: Connection,
    *,
    path: str,
    file_format: str | None,
    secret_store: SecretStore,
) -> list[ColumnSpec]:
    """Column names+types of a flat file: the Parquet footer schema (exact, no
    data read) or a bounded CSV sample (pandas dtype inference)."""
    fmt = infer_file_format(path, file_format)
    secret = secret_store.get(connection.secret_ref or "")
    raw = io.BytesIO(
        download_bytes(
            conn_type=connection.type, config=connection.config, path=path, secret=secret
        )
    )
    if fmt == "csv":
        df = read_csv_bytes(raw, nrows=_CSV_TYPE_SAMPLE_ROWS)
        return [{"name": str(col), "type": str(dtype)} for col, dtype in df.dtypes.items()]
    import pyarrow.parquet as pq

    arrow_schema = pq.ParquetFile(raw).schema_arrow
    return [{"name": field.name, "type": str(field.type)} for field in arrow_schema]


def _iceberg_columns(
    connection: Connection, *, identifier: str, secret_store: SecretStore
) -> list[ColumnSpec]:
    """Column names+types from Iceberg table metadata — the #859 drift leg: the
    schema is READ from ``metadata.json`` (via the loaded table's current
    schema), never inferred from data files."""
    config = IcebergConfig.model_validate(connection.config)
    secret, catalog_secret = iceberg_credentials(config, connection.secret_ref, secret_store)
    table = load_iceberg_table(config, secret, identifier, catalog_secret)
    return [{"name": field.name, "type": str(field.field_type)} for field in table.schema().fields]


def introspect_columns(
    connection: Connection,
    *,
    table: str,
    schema: str | None,
    catalog: str | None,
    secret_store: SecretStore,
) -> list[ColumnSpec]:
    """The live column snapshot for a run's resolved target, by connection type.

    ``table`` is the run path's materialized target: a table name (SQL), the
    ``namespace.table`` identifier (Iceberg), or the concrete file path
    (flat-file — materialized by the batch resolver before this runs). Raises
    :class:`SchemaIntrospectionError` with a classified message on any failure —
    the raw adapter exception (which can carry DSNs/credential fragments) never
    reaches a result row.
    """
    try:
        if connection.type in _SQL_TYPES:
            return _sql_columns(
                connection, table=table, schema=schema, catalog=catalog, secret_store=secret_store
            )
        if connection.type == "iceberg":
            return _iceberg_columns(connection, identifier=table, secret_store=secret_store)
        if connection.type in _FILE_TYPES:
            return _file_columns(
                connection, path=table, file_format=None, secret_store=secret_store
            )
    except SchemaIntrospectionError:
        raise
    except Exception as exc:
        log.warning(
            "schema_drift_introspection_failed",
            connection_type=connection.type,
            error_type=type(exc).__name__,
        )
        raise SchemaIntrospectionError(
            f"could not introspect columns: {classify_failure_reason(exc)}"
        ) from exc
    raise SchemaIntrospectionError(
        f"schema_drift is not supported on {connection.type!r} connections"
    )


# ───────────────────── baseline store + executor ─────────────────────


def get_baseline(session: Session, check_id: uuid.UUID) -> MonitorBaseline | None:
    return session.scalars(
        select(MonitorBaseline).where(MonitorBaseline.check_id == check_id)
    ).first()


def rebaseline(session: Session, check: Check) -> bool:
    """Drop the check's stored baseline so the NEXT run recaptures it live.

    Deliberately a delete, not an immediate recapture: recapturing here would run
    datasource introspection on the API request thread with the caller's
    patience as the timeout. Returns whether a baseline existed."""
    row = get_baseline(session, check.id)
    if row is None:
        return False
    session.delete(row)
    return True


def build_schema_drift_executor(
    session: Session,
    *,
    connection: Connection,
    target_table: str,
    target_schema: str | None,
    target_catalog: str | None,
    secret_store: SecretStore,
    persist: bool = True,
) -> Callable[[Check], CheckOutcome]:
    """A per-run executor for schema_drift checks (the comparison pattern, #794).

    First run (no stored baseline): captures the current snapshot as the
    baseline (``captured_by`` NULL = run path) and reports a passing
    "baseline captured" outcome. Later runs: diff current vs baseline →
    the registry's outcome strategy bands the drifted-column count.

    ``persist=False`` is the dry-run mode: nothing is written — a missing
    baseline previews as "would capture", an existing one previews the live
    diff. Introspection failure is the CHECK's operational error (#122), never
    the run's — one unreachable target must not fail sibling checks.
    """

    def executor(check: Check) -> CheckOutcome:
        try:
            current = introspect_columns(
                connection,
                table=target_table,
                schema=target_schema,
                catalog=target_catalog,
                secret_store=secret_store,
            )
        except SchemaIntrospectionError as exc:
            return CheckOutcome(
                expectation_type=monitor_expectation_type(SCHEMA_DRIFT),
                success=False,
                errored=True,
                error_message=str(exc),
            )
        baseline_row = get_baseline(session, check.id)
        config = dict(check.config)
        if baseline_row is None:
            payload: dict[str, Any] = {
                "baseline_captured": True,
                "columns_checked": len(current),
            }
            if persist:
                # ON CONFLICT DO NOTHING: two concurrent first runs of one suite
                # both see no baseline (READ COMMITTED) and both insert — the
                # loser must NOT blow up the whole run's commit with an
                # IntegrityError (discarding every sibling result row, #122).
                # Whichever run wins captured the same live schema moments apart;
                # the loser's "baseline captured" report stays truthful. Rides
                # the run's transaction, so a rolled-back run strands nothing.
                session.execute(
                    pg_insert(MonitorBaseline)
                    .values(
                        check_id=check.id,
                        kind=SCHEMA_DRIFT,
                        baseline={"columns": current},
                    )
                    .on_conflict_do_nothing(constraint="uq_monitor_baselines_check")
                )
            else:
                payload["dry_run"] = True
        else:
            stored = baseline_row.baseline.get("columns", [])
            payload = diff_schemas(stored, current, ignore_columns=config.get("ignore_columns"))
            payload["baseline_captured_at"] = baseline_row.captured_at.isoformat()
        return monitor_outcome(SCHEMA_DRIFT, scalar=payload, config=config, now=datetime.now(UTC))

    return executor
