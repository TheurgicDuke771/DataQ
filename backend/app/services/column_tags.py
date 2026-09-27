"""Warehouse-native column classification — G3 / #433, the authoritative source."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final, cast

from sqlalchemy import text

from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.db.models import Asset, Connection

log = get_logger(__name__)

#: The documented tag key. Lower-cased comparison; warehouses differ on whether
#: they preserve the case a tag was created with.
DATAQ_TAG_KEY: Final[str] = "dataq_classification"

#: Snowflake's own classification tag, honoured in addition to the convention — assigned by the
#: warehouse itself, so more authoritative than anything a user types.
SNOWFLAKE_PRIVACY_TAG: Final[str] = "privacy_category"
_SNOWFLAKE_PRIVACY_SENSITIVE: Final[frozenset[str]] = frozenset(
    {"identifier", "quasi_identifier", "sensitive"}
)

#: Normalized outputs.
SENSITIVE: Final[str] = "sensitive"
NON_SENSITIVE: Final[str] = "public"

_SENSITIVE_VALUES: Final[frozenset[str]] = frozenset(
    {"sensitive", "pii", "confidential", "restricted", "secret"}
)
_NON_SENSITIVE_VALUES: Final[frozenset[str]] = frozenset(
    {"public", "non_sensitive", "nonsensitive"}
)

#: How long a cached map is trusted before the run path re-reads it.
REFRESH_TTL: Final[timedelta] = timedelta(minutes=15)

#: Datasource types with a column-tag concept at all.
TAGGABLE_TYPES: Final[frozenset[str]] = frozenset({"snowflake", "unity_catalog"})


def normalize_tag(tag_name: str | None, tag_value: str | None) -> str | None:
    """Map one warehouse tag to the redactor's vocabulary, or `None` to ignore it."""
    if not tag_name or tag_value is None:
        return None
    name = tag_name.strip().lower()
    value = str(tag_value).strip().lower()

    if name == SNOWFLAKE_PRIVACY_TAG:
        # Snowflake's own classification.
        return SENSITIVE if value in _SNOWFLAKE_PRIVACY_SENSITIVE else None
    if name != DATAQ_TAG_KEY:
        return None
    if value in _SENSITIVE_VALUES:
        return SENSITIVE
    if value in _NON_SENSITIVE_VALUES:
        return NON_SENSITIVE
    return None


def _merge(tags: dict[str, str], column: str, verdict: str | None) -> None:
    """Record `verdict` for `column`, with **sensitive winning any conflict**."""
    if verdict is None:
        return
    key = column.strip().lower()
    if tags.get(key) == SENSITIVE:
        return
    tags[key] = verdict


def _rows_to_tags(rows: Any) -> dict[str, str]:
    """`(column, tag_name, tag_value)` rows → the redactor's column→verdict map."""
    tags: dict[str, str] = {}
    for row in rows:
        try:
            column, tag_name, tag_value = row[0], row[1], row[2]
        except (IndexError, TypeError):  # pragma: no cover - defensive on shape
            continue
        if column is None:
            continue
        _merge(tags, str(column), normalize_tag(tag_name, tag_value))
    return tags


def _snowflake_query(*, database: str, schema: str, table: str) -> Any:
    """Per-table tag references, fresh, **column-level only**."""
    obj = f"{database.upper()}.{schema.upper()}.{table.upper()}"
    return text(
        "SELECT COLUMN_NAME, TAG_NAME, TAG_VALUE "
        "FROM TABLE(INFORMATION_SCHEMA.TAG_REFERENCES_ALL_COLUMNS(:obj, 'TABLE')) "
        "WHERE LEVEL = 'COLUMN'"
    ).bindparams(obj=obj)


def _unity_catalog_query(*, catalog: str, schema: str, table: str) -> Any:
    """Unity Catalog exposes column tags through each catalog's information_schema."""
    return text(
        f"SELECT column_name, tag_name, tag_value "  # noqa: S608  # nosec B608
        f"FROM {catalog}.information_schema.column_tags "
        "WHERE lower(schema_name) = :schema AND lower(table_name) = :table"
    ).bindparams(schema=schema.lower(), table=table.lower())


def fetch_column_tags(
    connection: Connection,
    *,
    table: str,
    schema: str | None = None,
    catalog: str | None = None,
    secret_store: SecretStore,
) -> dict[str, str] | None:
    """Read a target's column classifications from the warehouse."""
    if connection.type not in TAGGABLE_TYPES:
        return None

    # Imported here rather than at module scope: `profile_service` pulls in the datasource stack,
    # and this module is imported by the read path, which must not pay for that.
    from backend.app.services.profile_service import (
        _open_connection,
        resolve_effective_schema,
        validate_identifier,
    )

    try:
        effective_schema = resolve_effective_schema(connection, schema)
        validate_identifier(table)
        validate_identifier(effective_schema)
        if catalog is not None:
            validate_identifier(catalog)

        if connection.type == "snowflake":
            database = (connection.config or {}).get("database")
            if not database:
                return None
            validate_identifier(str(database))
            stmt = _snowflake_query(database=str(database), schema=effective_schema, table=table)
        else:
            if not catalog:
                return None
            stmt = _unity_catalog_query(catalog=catalog, schema=effective_schema, table=table)

        with _open_connection(connection, secret_store) as conn:
            return _rows_to_tags(conn.execute(stmt))
    except Exception as exc:
        # By name and type only — a tag query's error text can echo identifiers.
        log.warning(
            "column_tags_unavailable",
            connection_type=connection.type,
            error_type=type(exc).__name__,
        )
        return None


def refresh_asset_column_tags(
    session: Any,
    *,
    suite: Any,
    connection: Connection,
    target: Any,
    secret_store: SecretStore,
) -> dict[str, str] | None:
    """Read the target's column tags and cache them on the suite's asset."""
    if connection.type not in TAGGABLE_TYPES:
        return None
    asset_id = getattr(suite, "asset_id", None)
    if asset_id is None:
        return None

    table = getattr(target, "table", None)
    if not table:
        return None

    asset = session.get(Asset, asset_id)
    if asset is None:
        return None
    # A frequently-scheduled suite would otherwise open a warehouse connection on every single run
    # to re-read a map that changes when someone edits governance — i.e. rarely.
    refreshed = getattr(asset, "column_tags_refreshed_at", None)
    if refreshed is not None and datetime.now(UTC) - refreshed < REFRESH_TTL:
        return cast("dict[str, str] | None", asset.column_tags)

    try:
        tags = fetch_column_tags(
            connection,
            table=str(table),
            schema=getattr(target, "schema", None),
            catalog=getattr(target, "catalog", None),
            secret_store=secret_store,
        )
        if tags is None:
            # Could NOT read — leave the cached map alone.
            log.info("column_tags_unreadable_cache_kept", asset_id=str(asset_id))
            return cast("dict[str, str] | None", asset.column_tags)
        # Written even when EMPTY, together with the timestamp: "we looked and found none" is a
        # different fact from "we never looked".
        asset.column_tags = tags
        asset.column_tags_refreshed_at = datetime.now(UTC)
        session.commit()
        if tags:
            log.info(
                "column_tags_refreshed",
                asset_id=str(asset_id),
                tagged_columns=len(tags),
                connection_type=connection.type,
            )
        return tags
    except Exception as exc:
        # Rollback before logging: after a database-level failure the session is in a failed
        # transaction, and the run continues afterwards.
        try:
            session.rollback()
        except Exception:  # pragma: no cover - defensive
            log.warning("column_tags_rollback_failed", exc_info=True)
        log.warning(
            "column_tags_refresh_failed",
            connection_type=connection.type,
            error_type=type(exc).__name__,
        )
        return None


# ── lineage propagation (#1710, ADR 0034 amendment 2026-09-27) ──────────────────────────────


def _cell_sensitive(tags: dict[str, str] | None, column: str) -> bool:
    if not tags:
        return False
    verdict = tags.get(column) or tags.get(column.strip().lower())
    return str(verdict or "").strip().lower() in _SENSITIVE_VALUES


def inherited_sensitive(session: Any, asset: Asset) -> dict[str, list[tuple[Any, str]]]:
    """Columns of ``asset`` that inherit ``sensitive`` from an upstream column through RECORDED
    column lineage, with the upstream ``(asset_id, column)`` cells that justify it.

    Additive-only by construction: a column with ANY own warehouse verdict (sensitive or public)
    is skipped — the steward looked at this column, and their answer wins — and nothing ever
    inherits ``public``. Only upstream assets whose tags DataQ has read (a suite ran on them)
    contribute; an unread upstream contributes nothing, never a clearance.
    """
    from sqlalchemy import select

    from backend.app.core.config import get_settings
    from backend.app.lineage.columns import MAX_TRACE_DEPTH, upstream_column_sources

    if not get_settings().lineage_classification_propagation:
        return {}
    walk = upstream_column_sources(session, asset.id, max_depth=MAX_TRACE_DEPTH)
    if walk.truncated:
        # Never below the own-tags floor, but a column beyond the cap is NOT inherited — say so.
        log.warning("column_tags_propagation_truncated", asset_id=str(asset.id))
    if not walk.sources:
        return {}
    upstream_ids = {aid for cells in walk.sources.values() for aid, _ in cells}
    tags_by_asset: dict[Any, dict[str, str] | None] = dict(
        session.execute(select(Asset.id, Asset.column_tags).where(Asset.id.in_(upstream_ids)))
        .tuples()
        .all()
    )
    own = {str(k).strip().lower() for k in (asset.column_tags or {})}
    out: dict[str, list[tuple[Any, str]]] = {}
    for column, cells in walk.sources.items():
        key = column.strip().lower()
        if key in own:
            continue
        hits = sorted(
            (cell for cell in cells if _cell_sensitive(tags_by_asset.get(cell[0]), cell[1])),
            key=lambda cell: (str(cell[0]), cell[1]),
        )
        if hits:
            out[key] = hits
    return out


def effective_column_tags(session: Any, asset: Asset | None) -> dict[str, str] | None:
    """The asset's own warehouse classifications PLUS ``sensitive`` inherited through column
    lineage — the map every redaction read path uses as the governance floor.

    Fail-soft to the asset's own tags (the pre-propagation floor) inside a SAVEPOINT, so a lineage
    read error can neither remove masking the own tags provide nor poison the caller's session.
    """
    if asset is None:
        return None
    own = asset.column_tags
    try:
        with session.begin_nested():
            inherited = inherited_sensitive(session, asset)
    except Exception as exc:
        log.warning(
            "column_tags_propagation_failed",
            asset_id=str(asset.id),
            error_type=type(exc).__name__,
        )
        return own
    if not inherited:
        return own
    merged = dict(own or {})
    for column in inherited:
        merged[column] = SENSITIVE
    return merged
