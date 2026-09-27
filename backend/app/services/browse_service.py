"""Interactive datasource browsing for the run-target editor (#466).

Two read-only, one-level-at-a-time listings through a connection's stored credential:

* **Unity Catalog** — catalogs → schemas → tables, from ``system.information_schema`` via the
  ADR 0040 enumeration seam, so a picked table is one the inventory sync would also see.
* **ADLS Gen2 / S3** — the folders and files directly under a prefix of the connection's one
  container/bucket.

Every listing is capped and says so (``truncated``); only names, sizes and timestamps come back,
never a credential or a cell value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.datasources import flatfile
from backend.app.datasources.sql import is_sql_identifier
from backend.app.db.models import Connection
from backend.app.lineage.warehouse_unity_catalog import UnityCatalogLineageProvider
from backend.app.services import credential_health
from backend.app.services.failure_classifier import classify_failure_reason
from backend.app.services.profile_service import _open_connection

log = get_logger(__name__)

TABLE_BROWSE_TYPES: Final[frozenset[str]] = frozenset({"unity_catalog"})
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
    """One level of the catalog → schema → table tree, capped at ``limit`` names."""
    if connection.type not in TABLE_BROWSE_TYPES:
        raise BrowseUnsupportedError(
            f"catalog browsing is not supported for {connection.type!r} connections",
            detail={"type": connection.type, "supported": sorted(TABLE_BROWSE_TYPES)},
        )
    if schema is not None and catalog is None:
        raise BrowseInputInvalidError("schema requires a catalog", detail={"field": "schema"})
    catalog = _identifier(catalog, "catalog")
    schema = _identifier(schema, "schema")
    _require_credential(connection)
    provider = UnityCatalogLineageProvider()
    level: Literal["catalog", "schema", "table"] = (
        "catalog" if catalog is None else "schema" if schema is None else "table"
    )

    with credential_health.credential_use(session, connection):
        try:
            with _open_connection(connection, secret_store) as conn:
                # limit + 1: the extra row is how a full page is told from a complete one.
                if catalog is None:
                    names = provider.catalog_names(conn, limit=limit + 1)
                elif schema is None:
                    names = provider.schema_names(conn, catalog=catalog, limit=limit + 1)
                else:
                    names = [
                        table
                        for _, _, table in provider.table_rows(
                            conn, limit=limit + 1, catalog=catalog, schema=schema
                        )
                    ]
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
        entries=[CatalogEntry(name=n, selectable=is_sql_identifier(n)) for n in names[:limit]],
        truncated=len(names) > limit,
        limit=limit,
    )


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
