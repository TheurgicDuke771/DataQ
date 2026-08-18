"""Warehouse-native column classification — G3 / #433, the authoritative source.

`run_service`'s redaction ladder has always had a **governance-tag floor** as its
top rung (`_tag_sensitive` / `_tag_non_sensitive`), threaded through every call
site and populated by nothing. This module is what populates it: it reads the
classification a customer has already applied **in their warehouse**, so
DataQ's masking follows the organisation's own data governance instead of a name
heuristic guessing at it.

## The convention (fixed, not configurable)

DataQ honours a **documented convention** rather than a per-connection mapping.
A mapping would be more flexible and worse: every deployment would express the
same idea differently, the mapping itself would become an unreviewed security
control, and a typo in it would silently un-mask a column. One convention is
checkable by reading this file.

**Tag key** — `dataq_classification` on the COLUMN, matched case-insensitively.

**Tag values** — the same vocabulary the redactor already speaks:

* masks: ``sensitive`` · ``pii`` · ``confidential`` · ``restricted`` · ``secret``
* clears: ``public`` · ``non_sensitive`` · ``nonsensitive``

Anything else is **ignored rather than guessed at** — an unrecognised value means
the column falls through to the next rung of the ladder (suite policy, then the
classifier), which is where it would have been with no tag at all. Treating an
unknown value as a clearance is the one interpretation that could un-mask data,
so it is the one explicitly not taken.

**Snowflake additionally honours its own `PRIVACY_CATEGORY` system tag**, set by
Snowflake's built-in classification. It costs nothing extra — the same query
returns it — and it is the most authoritative signal available on that platform,
since the warehouse assigned it. Its values (`IDENTIFIER`, `QUASI_IDENTIFIER`,
`SENSITIVE`) all mean "personal data", so all three mask.

## What has no tag source at all

Only `snowflake` and `unity_catalog` have a column-tag concept. ADLS, S3, Iceberg
and flat files have none — there is no authoritative source to read — so for those
types G3's answer remains the suite policy, the name/value classifier, and the
fail-closed mode. `fetch_column_tags` returns `{}` for them, which the redactor
already treats as "no opinion" rather than "cleared".

## Failure is silence, and silence is safe

Every failure here — no permission on the tag, a missing `information_schema`, a
dead connection — returns `{}` and logs. It never raises into a run, and it never
invents a clearance. That direction matters more than usual because fail-closed
mode (`require_classification`) treats an explicit non-sensitive tag as a
clearance: a fetcher that guessed on failure could *un-mask* data. So an
unreadable tag source degrades to exactly the behaviour that existed before this
module, which is the only safe degradation available.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import text

from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.db.models import Asset, Connection

log = get_logger(__name__)

#: The documented tag key. Lower-cased comparison; warehouses differ on whether
#: they preserve the case a tag was created with.
DATAQ_TAG_KEY: Final[str] = "dataq_classification"

#: Snowflake's own classification tag, honoured in addition to the convention —
#: assigned by the warehouse itself, so more authoritative than anything a user
#: types. All three of its values denote personal data.
SNOWFLAKE_PRIVACY_TAG: Final[str] = "privacy_category"
_SNOWFLAKE_PRIVACY_SENSITIVE: Final[frozenset[str]] = frozenset(
    {"identifier", "quasi_identifier", "sensitive"}
)

#: Normalized outputs. Deliberately the *same strings* `run_service` already
#: matches on, so this module cannot drift into a private vocabulary that the
#: redactor silently ignores.
SENSITIVE: Final[str] = "sensitive"
NON_SENSITIVE: Final[str] = "public"

_SENSITIVE_VALUES: Final[frozenset[str]] = frozenset(
    {"sensitive", "pii", "confidential", "restricted", "secret"}
)
_NON_SENSITIVE_VALUES: Final[frozenset[str]] = frozenset(
    {"public", "non_sensitive", "nonsensitive"}
)

#: Datasource types with a column-tag concept at all.
TAGGABLE_TYPES: Final[frozenset[str]] = frozenset({"snowflake", "unity_catalog"})


def normalize_tag(tag_name: str | None, tag_value: str | None) -> str | None:
    """Map one warehouse tag to the redactor's vocabulary, or `None` to ignore it.

    `None` is the important return: an unrecognised tag **name** or **value**
    means this module has no opinion, and the column falls through to the next
    rung of the ladder exactly as if it were untagged. The alternative — guessing
    — could only guess in two directions, and one of them un-masks data.
    """
    if not tag_name or tag_value is None:
        return None
    name = tag_name.strip().lower()
    value = str(tag_value).strip().lower()

    if name == SNOWFLAKE_PRIVACY_TAG:
        # Snowflake's own classification. Only the sensitive side is meaningful:
        # the tag is absent rather than set to a "public" value for data it does
        # not consider personal, so there is no clearance to read from it.
        return SENSITIVE if value in _SNOWFLAKE_PRIVACY_SENSITIVE else None
    if name != DATAQ_TAG_KEY:
        return None
    if value in _SENSITIVE_VALUES:
        return SENSITIVE
    if value in _NON_SENSITIVE_VALUES:
        return NON_SENSITIVE
    return None


def _merge(tags: dict[str, str], column: str, verdict: str | None) -> None:
    """Record `verdict` for `column`, with **sensitive winning any conflict**.

    A column carrying both a sensitive and a non-sensitive tag is a governance
    contradiction, and the resolution is not arbitrary: this map feeds a clearance
    path in fail-closed mode, so resolving toward "public" would let a mislabelled
    column surface. Masking a column that someone also called public is a visible,
    recoverable annoyance; the other way round is not.
    """
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
    """Per-table tag references, fresh.

    `TAG_REFERENCES_ALL_COLUMNS` rather than `SNOWFLAKE.ACCOUNT_USAGE.TAG_REFERENCES`
    deliberately: ACCOUNT_USAGE lags by up to two hours and needs a grant on the
    shared database, and a *stale* classification is the failure mode this whole
    feature exists to remove. The object name is a bound STRING argument to a
    table function, not an interpolated identifier.
    """
    return text(
        "SELECT COLUMN_NAME, TAG_NAME, TAG_VALUE "
        "FROM TABLE(INFORMATION_SCHEMA.TAG_REFERENCES_ALL_COLUMNS(:obj, 'TABLE'))"
    ).bindparams(obj=f"{database}.{schema}.{table}")


def _unity_catalog_query(*, catalog: str, schema: str, table: str) -> Any:
    """Unity Catalog exposes column tags through each catalog's information_schema.

    Fully-qualified as `<catalog>.information_schema.column_tags` rather than
    relying on the session's current catalog, which a shared warehouse connection
    does not reliably set.

    **The catalog is interpolated because it cannot be bound** — it is an
    identifier prefix, and no driver binds those. The control is
    `validate_identifier`, applied by the caller before this is reached: it is the
    same shared plain-identifier allowlist the profiler and the monitor engine use
    (#428), so a catalog containing anything but an identifier raises a 422 rather
    than reaching this string. The schema and table, which CAN be bound, are.
    S608/B608 are suppressed on that basis.
    """
    return text(
        f"SELECT column_name, tag_name, tag_value "  # noqa: S608  # nosec B608
        f"FROM {catalog}.information_schema.column_tags "
        "WHERE schema_name = :schema AND table_name = :table"
    ).bindparams(schema=schema, table=table)


def fetch_column_tags(
    connection: Connection,
    *,
    table: str,
    schema: str | None = None,
    catalog: str | None = None,
    secret_store: SecretStore,
) -> dict[str, str]:
    """Read a target's column classifications from the warehouse.

    Returns `{column_lower: "sensitive" | "public"}`, or `{}` for a datasource
    with no tag concept, an unreadable tag source, or a target with no tags. The
    caller cannot distinguish those cases and does not need to: all four mean
    "this module has no opinion", and the redaction ladder already has a rung
    below.

    **Never raises.** See the module docstring — a fetcher that failed loudly
    would take out a run over a governance lookup, and one that guessed on failure
    could un-mask data through fail-closed mode's clearance path.
    """
    if connection.type not in TAGGABLE_TYPES:
        return {}

    # Imported here rather than at module scope: `profile_service` pulls in the
    # datasource stack, and this module is imported by the read path, which must
    # not pay for that.
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
                return {}
            validate_identifier(str(database))
            stmt = _snowflake_query(database=str(database), schema=effective_schema, table=table)
        else:
            if not catalog:
                return {}
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
        return {}


def refresh_asset_column_tags(
    session: Any,
    *,
    suite: Any,
    connection: Connection,
    target: Any,
    secret_store: SecretStore,
) -> dict[str, str] | None:
    """Read the target's column tags and cache them on the suite's asset.

    Called from the run path, which is the one moment DataQ already holds the
    warehouse credentials and the resolved target. Returns the map it stored, or
    `None` when there was nothing to do.

    **Swallows every failure.** A governance lookup must never be the reason a
    data-quality run fails, and — more importantly — a fetcher that guessed on
    failure could hand fail-closed mode a false clearance. Silence degrades to
    exactly the pre-G3 behaviour, which is the only safe direction.

    A no-op when the suite has no `asset_id` (nothing to cache against) or the
    datasource has no tag concept.
    """
    if connection.type not in TAGGABLE_TYPES:
        return None
    asset_id = getattr(suite, "asset_id", None)
    if asset_id is None:
        return None

    table = getattr(target, "table", None)
    if not table:
        return None

    try:
        tags = fetch_column_tags(
            connection,
            table=str(table),
            schema=getattr(target, "schema", None),
            catalog=getattr(target, "catalog", None),
            secret_store=secret_store,
        )
        asset = session.get(Asset, asset_id)
        if asset is None:
            return None
        # Written even when EMPTY, together with the timestamp: "we looked and
        # found none" is a different fact from "we never looked", and the
        # timestamp is the only thing that distinguishes them for an operator
        # asking why a column surfaced. The redactor treats both identically.
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
        # Rollback before logging: after a database-level failure the session is
        # in a failed transaction, and the run continues afterwards — leaving it
        # poisoned would turn this swallowed governance lookup into the run's
        # visible error, which is precisely what swallowing it is for.
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
