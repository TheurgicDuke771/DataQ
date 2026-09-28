"""Interactive datasource browsing for the run-target editor (#466).

Two read-only, one-level-at-a-time listings through a connection's stored credential:

* **Unity Catalog** — catalogs → schemas → tables, from ``system.information_schema`` via the
  ADR 0040 enumeration seam, so a picked table is one the inventory sync would also see.
* **The generic SQL engines** (PostgreSQL, MySQL/MariaDB, Trino, SQL Server, Athena, Redshift) —
  schemas → tables, from the engine's own catalog query, the same one the inventory sync
  enumerates with.
* **ADLS Gen2 / S3** — the folders and files directly under a prefix of the connection's one
  container/bucket.

Every listing is capped and says so (``truncated``); only names, sizes and timestamps come back,
never a credential or a cell value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final, Literal

from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.datasources import flatfile, generic_sql
from backend.app.datasources.generic_sql import SqlEngineSpec
from backend.app.datasources.sql import is_sql_identifier, object_type
from backend.app.datasources.sql_engines import (
    GENERIC_SQL_TYPES,
    authenticates_without_secret,
    sql_engine,
)
from backend.app.db.models import Connection
from backend.app.lineage.warehouse_unity_catalog import UnityCatalogLineageProvider
from backend.app.services import credential_health
from backend.app.services.failure_classifier import classify_failure_reason
from backend.app.services.profile_service import _open_connection

log = get_logger(__name__)

TABLE_BROWSE_TYPES: Final[frozenset[str]] = frozenset(
    {"unity_catalog", "snowflake", "iceberg", *GENERIC_SQL_TYPES}
)
#: Types whose connection pins one database (or catalog): the tree starts at schemas — an Iceberg
#: namespace plays the schema's part.
_SCHEMA_ROOTED_TYPES: Final[frozenset[str]] = frozenset(
    {"snowflake", "iceberg", *GENERIC_SQL_TYPES}
)
FILE_BROWSE_TYPES: Final[frozenset[str]] = frozenset({"adls_gen2", "s3"})

DEFAULT_LIMIT: Final = 200
MAX_LIMIT: Final = 500

# Control characters (incl. NUL) and the backslash — never part of a key the picker should offer.
_BAD_PATH_CHARS: Final = re.compile(r"[\x00-\x1f\x7f\\]")


class BrowseUnsupportedError(DataQError):
    status_code = 422
    code = "browse_unsupported"


class BrowseInputInvalidError(DataQError):
    status_code = 422
    code = "browse_input_invalid"


class BrowseFailedError(DataQError):
    status_code = 502
    code = "browse_failed"


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    #: Whether DataQ can target this name — runs only accept plain SQL identifiers, so an
    #: exotic name is listed (hiding it would misreport the catalog) but not pickable.
    selectable: bool
    #: At the table level, the relation's kind (``sql.OBJECT_TYPES``); ``None`` above it.
    object_type: str | None = None


#: One listed name and, at the table level, its object type.
_Named = tuple[str, str | None]


def _untyped(names: list[str]) -> list[_Named]:
    return [(name, None) for name in names]


@dataclass(frozen=True)
class CatalogListing:
    level: Literal["catalog", "schema", "table"]
    catalog: str | None
    schema: str | None
    entries: list[CatalogEntry]
    truncated: bool
    limit: int


@dataclass(frozen=True)
class FileListing:
    root: str
    prefix: str
    folders: list[str]
    files: list[flatfile.BrowseFile]
    truncated: bool
    limit: int


def _require_credential(connection: Connection) -> str:
    if not connection.secret_ref:
        raise BrowseInputInvalidError(
            "connection has no stored credential to browse with", detail={"type": connection.type}
        )
    return connection.secret_ref


def _identifier(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    if not is_sql_identifier(value):
        raise BrowseInputInvalidError(
            f"not a valid {label} identifier", detail={"field": label, "value": value[:255]}
        )
    return value


def validate_prefix(prefix: str) -> str:
    """Reject a prefix that is not a plain relative key path (422)."""
    if not prefix:
        return ""
    if _BAD_PATH_CHARS.search(prefix):
        raise BrowseInputInvalidError(
            "prefix contains a control character or backslash", detail={"field": "prefix"}
        )
    if prefix.startswith("/"):
        raise BrowseInputInvalidError(
            "prefix must be relative to the container/bucket", detail={"field": "prefix"}
        )
    segments = prefix.split("/")
    # A trailing "/" leaves one empty last segment, which is the normal folder form.
    inner = segments[:-1] if segments[-1] == "" else segments
    if any(seg in ("", ".", "..") for seg in inner):
        raise BrowseInputInvalidError(
            "prefix may not contain empty, '.' or '..' segments", detail={"field": "prefix"}
        )
    return prefix


def browse_catalog(
    connection: Connection,
    *,
    session: Session,
    catalog: str | None,
    schema: str | None,
    limit: int,
    secret_store: SecretStore,
) -> CatalogListing:
    """One level of the catalog → schema → table tree, capped at ``limit`` names.

    A generic SQL engine (#1678) has no catalog level — its connection pins one database — so
    its tree starts at schemas and a ``catalog`` is refused rather than ignored.
    """
    if connection.type not in TABLE_BROWSE_TYPES:
        raise BrowseUnsupportedError(
            f"catalog browsing is not supported for {connection.type!r} connections",
            detail={"type": connection.type, "supported": sorted(TABLE_BROWSE_TYPES)},
        )
    spec = sql_engine(connection.type)
    schema_rooted = connection.type in _SCHEMA_ROOTED_TYPES
    if schema_rooted and catalog is not None:
        raise BrowseInputInvalidError(
            f"a {connection.type} connection has no catalog level — browse its schemas",
            detail={"field": "catalog"},
        )
    if not schema_rooted and schema is not None and catalog is None:
        raise BrowseInputInvalidError("schema requires a catalog", detail={"field": "schema"})
    catalog = _identifier(catalog, "catalog")
    schema = _identifier(schema, "schema")
    # An Iceberg catalog may authenticate by its own catalog secret or not at all.
    if connection.type != "iceberg" and not authenticates_without_secret(
        connection.type, connection.config
    ):
        _require_credential(connection)
    level: Literal["catalog", "schema", "table"]
    if schema_rooted:
        level = "schema" if schema is None else "table"
    else:
        level = "catalog" if catalog is None else "schema" if schema is None else "table"

    with credential_health.credential_use(session, connection):
        try:
            # limit + 1: the extra row is how a full page is told from a complete one.
            if connection.type == "iceberg":
                names = _iceberg_names(connection, secret_store, schema=schema, limit=limit + 1)
            else:
                with _open_connection(connection, secret_store) as conn:
                    if spec is not None:
                        names = _generic_sql_names(spec, conn, schema=schema, limit=limit + 1)
                    elif connection.type == "snowflake":
                        names = _snowflake_names(conn, schema=schema, limit=limit + 1)
                    else:
                        names = _unity_catalog_names(
                            conn, catalog=catalog, schema=schema, limit=limit + 1
                        )
        except Exception as exc:
            log.warning(
                "browse_catalog_failed",
                connection_type=connection.type,
                level=level,
                error_type=type(exc).__name__,
            )
            raise BrowseFailedError(
                "the datasource catalog could not be listed",
                detail={"reason": classify_failure_reason(exc)},
            ) from exc

    return CatalogListing(
        level=level,
        catalog=catalog,
        schema=schema,
        entries=[
            CatalogEntry(name=n, selectable=is_sql_identifier(n), object_type=kind)
            for n, kind in names[:limit]
        ],
        truncated=len(names) > limit,
        limit=limit,
    )


def _unity_catalog_names(
    conn: Any, *, catalog: str | None, schema: str | None, limit: int
) -> list[_Named]:
    provider = UnityCatalogLineageProvider()
    if catalog is None:
        return _untyped(provider.catalog_names(conn, limit=limit))
    if schema is None:
        return _untyped(provider.schema_names(conn, catalog=catalog, limit=limit))
    return [
        (table, kind)
        for _, _, table, kind in provider.typed_table_rows(
            conn, limit=limit, catalog=catalog, schema=schema
        )
    ]


def _snowflake_names(conn: Any, *, schema: str | None, limit: int) -> list[_Named]:
    """Schemas, then tables and views, of the connection's database — `INFORMATION_SCHEMA` is
    already filtered to what the role may see."""
    from sqlalchemy import text

    if schema is None:
        rows = conn.execute(
            text(
                "SELECT schema_name FROM INFORMATION_SCHEMA.SCHEMATA "
                "WHERE schema_name <> 'INFORMATION_SCHEMA' ORDER BY schema_name LIMIT :lim"
            ),
            {"lim": limit},
        ).all()
        return _untyped([str(name) for (name,) in rows if name])
    # A run emits an all-lower-case name unquoted, so Snowflake folds it — browse the same way.
    if schema == schema.lower():
        schema = schema.upper()
    rows = conn.execute(
        text(
            "SELECT table_name, table_type, is_dynamic FROM INFORMATION_SCHEMA.TABLES "
            "WHERE table_schema = :schema ORDER BY table_name LIMIT :lim"
        ),
        {"schema": schema, "lim": limit},
    ).all()
    # A dynamic table reports as a BASE TABLE; only IS_DYNAMIC tells it apart.
    return [
        (str(name), "dynamic_table" if is_dynamic == "YES" else object_type(kind))
        for name, kind, is_dynamic in rows
        if name
    ]


def _iceberg_names(
    connection: Connection, secret_store: SecretStore, *, schema: str | None, limit: int
) -> list[_Named]:
    """Top-level namespaces, then the tables in one — metadata only, no data file is read."""
    from backend.app.datasources.iceberg import (
        IcebergConfig,
        iceberg_credentials,
        load_iceberg_catalog,
    )

    config = IcebergConfig.model_validate(connection.config)
    secret, catalog_secret = iceberg_credentials(config, connection.secret_ref, secret_store)
    iceberg = load_iceberg_catalog(config, secret, catalog_secret)
    if schema is None:
        return _untyped(sorted({str(ns[0]) for ns in iceberg.list_namespaces() if ns})[:limit])
    # Views are not listed, so every entry at this level is a table.
    tables = sorted(str(ident[-1]) for ident in iceberg.list_tables(schema))
    return [(name, "table") for name in tables[:limit]]


def _generic_sql_names(
    spec: SqlEngineSpec, conn: Any, *, schema: str | None, limit: int
) -> list[_Named]:
    # The same catalog queries the inventory sync enumerates with (ADR 0040), so a picked table
    # is one the asset view would also show.
    if schema is None:
        return _untyped(generic_sql.schema_names(spec, conn, limit=limit))
    return [
        (table, kind)
        for _, table, kind in generic_sql.typed_table_rows(spec, conn, schema=schema, limit=limit)
    ]


def browse_files(
    connection: Connection,
    *,
    session: Session,
    prefix: str,
    limit: int,
    secret_store: SecretStore,
) -> FileListing:
    """The folders and files directly under ``prefix``, capped at ``limit`` entries."""
    if connection.type not in FILE_BROWSE_TYPES:
        raise BrowseUnsupportedError(
            f"file browsing is not supported for {connection.type!r} connections",
            detail={"type": connection.type, "supported": sorted(FILE_BROWSE_TYPES)},
        )
    prefix = validate_prefix(prefix)
    secret_ref = _require_credential(connection)
    config = dict(connection.config)
    root_key = "bucket" if connection.type == "s3" else "container"
    root = str(config.get(root_key) or "")

    with credential_health.credential_use(session, connection):
        try:
            listing = flatfile.list_directory(
                conn_type=connection.type,
                config=config,
                prefix=prefix,
                secret=secret_store.get(secret_ref),
                limit=limit,
            )
        except Exception as exc:
            log.warning(
                "browse_files_failed",
                connection_type=connection.type,
                error_type=type(exc).__name__,
            )
            raise BrowseFailedError(
                "the datasource store could not be listed",
                detail={"reason": classify_failure_reason(exc)},
            ) from exc

    return FileListing(
        root=root,
        prefix=prefix,
        folders=listing.folders,
        files=listing.files,
        truncated=listing.truncated,
        limit=limit,
    )
