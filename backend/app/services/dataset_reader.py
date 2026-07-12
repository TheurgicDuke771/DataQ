"""Read one comparison side (source or target) into a DataFrame (ADR 0015, #792).

The `DatasetReader` seam: given a **datasource** connection and a `DatasetSpec`
(a resolved table triple, a flat-file path, or a read-only SQL projection),
return a bounded pandas DataFrame for the comparison engine (#793). Grown from
plumbing that already exists — the profiler's SQL engine access
(`profile_service`), `flatfile`'s object read, `pyiceberg`'s scan — behind the
same dict-dispatch shape as the profiler's `_PROFILERS`, so service code never
branches on `connection.type`. Orchestration types have no reader (a clean 422,
mirroring `build_check_runner`).

Row-cap discipline (ADR 0015 §3): `max_rows` is enforced **fail-fast** — a
cheap COUNT preflight where the backend offers one (SQL engines, Iceberg scan
metadata) so an oversized table never transfers, plus a post-read length check
everywhere (the preflight is racy by nature; flat files have no preflight at
all). Over-cap raises `DatasetTooLargeError`, **never** a silent truncation —
a truncated diff produces confidently wrong mismatch buckets, which is worse
than no answer.

FastAPI-free like the sibling services: takes ORM `Connection` + `SecretStore`,
returns DataFrames, raises `DataQError` subclasses (the #794 run path maps them
to operational `error` results; the authoring/dry-run paths surface them as
422s).
"""

from __future__ import annotations

import string
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from backend.app.core.config import get_settings
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.datasources.flatfile import read_dataframe as read_flatfile_dataframe
from backend.app.datasources.iceberg import (
    IcebergConfig,
    load_iceberg_table,
    read_iceberg_dataframe,
)
from backend.app.db.models import Connection
from backend.app.services.custom_sql import validate_query
from backend.app.services.profile_service import (
    _open_connection,
    _table,
    resolve_effective_schema,
)

log = get_logger(__name__)


class DatasetReadUnsupportedError(DataQError):
    status_code = 422
    code = "comparison_read_unsupported"


class DatasetTooLargeError(DataQError):
    status_code = 422
    code = "comparison_dataset_too_large"


@dataclass(frozen=True)
class DatasetSpec:
    """What to read from one comparison side.

    Exactly one addressing mode is used per datasource: `query` (SQL sources,
    already author-time validated read-only), the `table`/`schema`/`catalog`
    triple (SQL sources; `table` also carries the Iceberg `namespace.table`
    identifier), or `path` (flat-file sources — the caller materializes batch
    targets to a concrete path first, exactly like a run does).
    """

    table: str | None = None
    schema: str | None = None
    catalog: str | None = None
    path: str | None = None
    query: str | None = None


def default_max_rows() -> int:
    """The settings-level comparison row cap (per-check `config.max_rows`
    overrides it — resolved by the caller, ADR 0015 §3)."""
    return get_settings().comparison_max_rows


def _too_large(count: int, max_rows: int, *, side_hint: str) -> DatasetTooLargeError:
    return DatasetTooLargeError(
        f"{side_hint} has {count} rows, over the comparison cap of {max_rows} — "
        "raise config.max_rows deliberately or narrow the dataset (a truncated "
        "diff would be silently wrong, so DataQ refuses instead)",
        detail={"rows": count, "max_rows": max_rows},
    )


def _require_secret(connection: Connection, secret_store: SecretStore) -> str:
    if not connection.secret_ref:
        raise DatasetReadUnsupportedError(
            "connection has no stored credential",
            detail={"connection_id": str(connection.id)},
        )
    return secret_store.get(connection.secret_ref)


# ───────────────────────── SQL (snowflake / unity_catalog) ──────────


def _wrapped_query(spec: DatasetSpec) -> str:
    """The validated read-only projection as a parenthesized FROM source.

    Re-validated here (defence in depth — author-time validation already ran):
    a single read-only SELECT/WITH statement, so interpolating it into the
    COUNT/LIMIT wrappers below cannot smuggle a second statement or a write.
    The whitespace+semicolon tail is stripped in ONE pass (matching
    `validate_query`'s own trailing-chars rule — `.rstrip().rstrip(";")` would
    leave `SELECT 1; ` from a `; ;` tail and break the wrapper).
    """
    assert spec.query is not None
    validate_query(spec.query)
    return spec.query.strip().rstrip(string.whitespace + ";")


def _sql_read(
    connection: Connection, spec: DatasetSpec, max_rows: int, secret_store: SecretStore
) -> Any:
    import pandas as pd

    if not connection.secret_ref:
        # Pre-check so a credential-less connection is the same clean 422 the
        # flat-file path gives, not `_open_connection`'s bare ValueError 500.
        raise DatasetReadUnsupportedError(
            "connection has no stored credential",
            detail={"connection_id": str(connection.id)},
        )
    count_stmt: Any
    select_stmt: Any
    if spec.query is not None:
        q = _wrapped_query(spec)
        # Interpolation is safe: `q` passed the read-only single-statement
        # validator (ADR 0019) at author time AND immediately above. The
        # newline before the closing paren keeps a trailing `-- comment` (legal
        # per the validator) from swallowing the wrapper's tail.
        count_sql = f"SELECT COUNT(*) FROM (\n{q}\n) __dataq_src"  # noqa: S608  # nosec B608
        select_sql = (
            f"SELECT * FROM (\n{q}\n) __dataq_src "  # noqa: S608  # nosec B608
            f"LIMIT {int(max_rows) + 1}"
        )
        count_stmt = sa.text(count_sql)
        select_stmt = sa.text(select_sql)
    else:
        if not spec.table:
            raise DatasetReadUnsupportedError(
                "SQL comparison side needs a table (or a query)", detail={"spec": "table"}
            )
        if connection.type == "unity_catalog" and not spec.catalog:
            # The UC engine URL deliberately pins no catalog (profiler parity) —
            # an unqualified 2-part name would resolve against the session
            # default catalog and silently read the wrong table.
            raise DatasetReadUnsupportedError(
                "a Unity Catalog comparison side needs a catalog",
                detail={"spec": "catalog"},
            )
        schema = resolve_effective_schema(connection, spec.schema)
        source = _table(schema, spec.table, spec.catalog)
        count_stmt = sa.select(sa.func.count()).select_from(source)
        select_stmt = sa.select(sa.text("*")).select_from(source).limit(max_rows + 1)

    with _open_connection(connection, secret_store) as conn:
        count = int(conn.execute(count_stmt).scalar_one())
        if count > max_rows:
            raise _too_large(count, max_rows, side_hint="dataset")
        # Arrow-backed dtypes for parity with the flat-file/Iceberg readers —
        # cross-side dtype/null-semantics normalization is the #793 engine's
        # contract, but the reader must not hand it numpy-vs-arrow skew of its
        # own making (NULL ints → float64+NaN on one side only).
        df = pd.read_sql(select_stmt, conn, dtype_backend="pyarrow")
    # Belt over the braces: the COUNT is racy (rows can land between preflight
    # and read) and the LIMIT is max_rows+1 exactly so growth is detectable.
    if len(df) > max_rows:
        raise _too_large(len(df), max_rows, side_hint="dataset")
    return df


# ───────────────────────── flat file (adls_gen2 / s3) ───────────────


def _flatfile_read(
    connection: Connection, spec: DatasetSpec, max_rows: int, secret_store: SecretStore
) -> Any:
    path = spec.path or spec.table
    if not path:
        raise DatasetReadUnsupportedError(
            "flat-file comparison side needs a resolved path", detail={"spec": "path"}
        )
    secret = _require_secret(connection, secret_store)
    # No cheap preflight exists for an object read — the whole file downloads,
    # then the cap is enforced before any diffing.
    df = read_flatfile_dataframe(
        conn_type=connection.type, config=dict(connection.config), path=path, secret=secret
    )
    if len(df) > max_rows:
        raise _too_large(len(df), max_rows, side_hint=f"file {path!r}")
    return df


# ───────────────────────── iceberg (native pyiceberg) ───────────────


def _iceberg_read(
    connection: Connection, spec: DatasetSpec, max_rows: int, secret_store: SecretStore
) -> Any:
    if not spec.table:
        raise DatasetReadUnsupportedError(
            "iceberg comparison side needs a namespace.table identifier",
            detail={"spec": "table"},
        )
    cfg = IcebergConfig.model_validate(connection.config)
    # Credential optional, mirroring `build_iceberg_runner` (a local warehouse /
    # vended-credentials REST catalog has none).
    secret = secret_store.get(connection.secret_ref) if connection.secret_ref else None
    table = load_iceberg_table(cfg, secret, spec.table)
    count = int(table.scan().count())  # snapshot metadata — no data files read
    if count > max_rows:
        raise _too_large(count, max_rows, side_hint=f"iceberg table {spec.table!r}")
    df = read_iceberg_dataframe(cfg, secret, spec.table, limit=max_rows + 1, table=table)
    if len(df) > max_rows:
        raise _too_large(len(df), max_rows, side_hint=f"iceberg table {spec.table!r}")
    return df


# ───────────────────────── dispatch ─────────────────────────────────

_Reader = Callable[[Connection, DatasetSpec, int, SecretStore], Any]

_READERS: dict[str, _Reader] = {
    "snowflake": _sql_read,
    "unity_catalog": _sql_read,
    "adls_gen2": _flatfile_read,
    "s3": _flatfile_read,
    "iceberg": _iceberg_read,
}


def read_dataset(
    connection: Connection,
    spec: DatasetSpec,
    *,
    max_rows: int,
    secret_store: SecretStore,
) -> Any:
    """Read one comparison side as a pandas DataFrame, capped at `max_rows`.

    Raises `DatasetReadUnsupportedError` (no reader for the connection type or
    an unusable spec) or `DatasetTooLargeError` (over-cap, fail-fast).
    """
    reader = _READERS.get(connection.type)
    if reader is None:
        raise DatasetReadUnsupportedError(
            f"connection type {connection.type!r} has no dataset reader "
            "(orchestration providers are never comparison sides)",
            detail={"type": connection.type, "supported": sorted(_READERS)},
        )
    if max_rows <= 0:
        raise DatasetReadUnsupportedError(
            "max_rows must be positive", detail={"max_rows": max_rows}
        )
    df = reader(connection, spec, max_rows, secret_store)
    log.info(
        "comparison_side_read",
        connection_id=str(connection.id),
        connection_type=connection.type,
        rows=len(df),
        max_rows=max_rows,
    )
    return df
