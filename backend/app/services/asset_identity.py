"""Resolve a connection + suite target to an OpenLineage-shaped asset identity.

ADR 0034 adopts the OpenLineage dataset naming spec (``namespace`` + ``name``)
verbatim as the canonical asset key, so DataQ's identifiers match
``openlineage-dbt``/Spark emissions byte-for-byte — a join, not a mapping
layer, when lineage emission/pull lands (#758/#762). This module is the pure
resolver: it never touches the network or a store, mirroring
``run_target.resolve_target``'s shape (typed, small pure helpers).

Per-datasource identity (see docs/post-v1-assets-lineage-incidents-notes.md
§1 and ADR 0034):

    snowflake      → snowflake://{normalized account}
                     DB.SCHEMA.TABLE (upper unless quoted)
    unity_catalog  → unitycatalog://{workspace host[:port]}
                     catalog.schema.table (lower unless quoted)
    adls_gen2      → abfss://{container}@{account}.dfs.core.windows.net
                     {path or pattern base dir}
    s3             → s3://{bucket}
                     {path or pattern base dir}
    iceberg        → {catalog_uri verbatim, or "file"}
                     {namespace.table verbatim}

Snowflake/UC identifiers fold to the engine's *unquoted* case (Snowflake
upper, UC lower) unless a part is double-quote/backtick wrapped, in which
case the quotes are stripped and the inner case kept verbatim — the
"engine-returned case" the OL clients replicate. Iceberg identifiers are
case-sensitive as stored, so no folding is applied there.

Orchestration connection types (adf/airflow/dbt) have no asset identity —
they are never suite datasources (CLAUDE.md §4) — and raise ``ValueError``;
callers (the suite-save hook) wrap this fail-soft rather than surfacing it
to a caller who didn't ask for one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

_GLOB_METACHARS = re.compile(r"[*?\[]")


@dataclass(frozen=True)
class AssetIdentity:
    """The OpenLineage ``namespace`` + ``name`` pair that keys an asset row."""

    namespace: str
    name: str


def resolve_asset_identity(
    conn_type: str, config: dict[str, Any], target: dict[str, Any]
) -> AssetIdentity:
    """Resolve a connection's ``config`` + a suite's ``target`` to an `AssetIdentity`.

    Raises `ValueError` if ``conn_type`` has no asset identity (orchestration
    providers) or a required key is missing/empty on either dict — never
    returns an empty namespace or name.
    """
    if conn_type == "snowflake":
        return _resolve_snowflake(config, target)
    if conn_type == "unity_catalog":
        return _resolve_unity_catalog(config, target)
    if conn_type == "adls_gen2":
        return _resolve_adls_gen2(config, target)
    if conn_type == "s3":
        return _resolve_s3(config, target)
    if conn_type == "iceberg":
        return _resolve_iceberg(config, target)
    raise ValueError(f"connection type {conn_type!r} has no asset identity (not a datasource)")


def normalize_snowflake_account(account: str) -> str:
    """Normalize a Snowflake account identifier (mirrors `openlineage-common.fix_account_name`).

    An org-account identifier (contains a ``-``) is already fully qualified
    and passes through unchanged. A legacy locator (no ``-``) is dot-segmented
    and defaulted: one segment (bare locator) gets ``.us-west-1.aws``
    appended; two segments (locator + region) get ``.aws`` appended; three or
    more segments (locator + region + cloud) are already complete and pass
    through unchanged.
    """
    account = account.strip()
    if not account:
        raise ValueError("snowflake account must be non-empty")
    if "-" in account:
        return account
    segments = account.split(".")
    if len(segments) == 1:
        return f"{account}.us-west-1.aws"
    if len(segments) == 2:
        return f"{account}.aws"
    return account


def _resolve_snowflake(config: dict[str, Any], target: dict[str, Any]) -> AssetIdentity:
    account = _require_config(config, "account", "snowflake")
    database = _require_config(config, "database", "snowflake")
    schema = _str_or_none(target.get("schema")) or _str_or_none(config.get("schema"))
    if not schema:
        raise ValueError("snowflake asset identity requires a 'schema' (target or config)")
    table = _require_target(target, "table", "snowflake")
    namespace = f"snowflake://{normalize_snowflake_account(account)}"
    name = ".".join(_normalize_part(part, engine="snowflake") for part in (database, schema, table))
    return AssetIdentity(namespace=namespace, name=name)


def _resolve_unity_catalog(config: dict[str, Any], target: dict[str, Any]) -> AssetIdentity:
    workspace_url = _require_config(config, "workspace_url", "unity_catalog")
    netloc = urlparse(workspace_url).netloc
    if not netloc:
        raise ValueError("unity_catalog asset identity requires a valid 'workspace_url'")
    catalog = _require_target(target, "catalog", "unity_catalog")
    schema = _str_or_none(target.get("schema")) or "default"
    table = _require_target(target, "table", "unity_catalog")
    namespace = f"unitycatalog://{netloc}"
    name = ".".join(
        _normalize_part(part, engine="unity_catalog") for part in (catalog, schema, table)
    )
    return AssetIdentity(namespace=namespace, name=name)


def _resolve_adls_gen2(config: dict[str, Any], target: dict[str, Any]) -> AssetIdentity:
    container = _require_config(config, "container", "adls_gen2")
    account_url = _require_config(config, "account_url", "adls_gen2")
    netloc = urlparse(account_url).netloc
    account = netloc.split(".")[0] if netloc else ""
    if not account:
        raise ValueError("adls_gen2 asset identity requires a valid 'account_url'")
    namespace = f"abfss://{container}@{account}.dfs.core.windows.net"
    name = _flatfile_name(target, "adls_gen2")
    return AssetIdentity(namespace=namespace, name=name)


def _resolve_s3(config: dict[str, Any], target: dict[str, Any]) -> AssetIdentity:
    bucket = _require_config(config, "bucket", "s3")
    namespace = f"s3://{bucket}"
    name = _flatfile_name(target, "s3")
    return AssetIdentity(namespace=namespace, name=name)


def _resolve_iceberg(config: dict[str, Any], target: dict[str, Any]) -> AssetIdentity:
    catalog_uri = config.get("catalog_uri")
    namespace = (
        catalog_uri.strip() if isinstance(catalog_uri, str) and catalog_uri.strip() else "file"
    )
    table = _require_target(target, "table", "iceberg")
    ns_part = _str_or_none(target.get("namespace"))
    name = f"{ns_part}.{table}" if ns_part else table
    return AssetIdentity(namespace=namespace, name=name)


def _flatfile_name(target: dict[str, Any], conn_type: str) -> str:
    path = _str_or_none(target.get("path"))
    if path:
        return path.lstrip("/")
    pattern = _str_or_none(target.get("pattern"))
    if pattern:
        return _pattern_base_prefix(pattern)
    raise ValueError(f"{conn_type} asset identity requires a target 'path' or 'pattern'")


def _pattern_base_prefix(pattern: str) -> str:
    """The literal directory prefix in front of the first glob metacharacter.

    Cuts ``pattern`` at the first ``*``/``?``/``[`` and truncates to the last
    ``/`` before it (the Spark flat-file-dataset convention: the asset is the
    directory, not the per-file match). A pattern with no ``/`` before its
    first metacharacter has no directory prefix to fall back to, so the whole
    pattern is used verbatim rather than yielding an empty name.
    """
    match = _GLOB_METACHARS.search(pattern)
    prefix = pattern[: match.start()] if match else pattern
    last_slash = prefix.rfind("/")
    if last_slash == -1:
        return pattern.lstrip("/")
    return prefix[: last_slash + 1].lstrip("/")


def _normalize_part(part: str, *, engine: str) -> str:
    """Fold one dotted-name segment to the engine's unquoted-identifier case.

    A double-quote-wrapped (Snowflake) or double-quote/backtick-wrapped (UC)
    part keeps its inner case verbatim once the quotes are stripped — the
    engine-returned case for a quoted identifier. An unquoted part is folded
    to the case the engine's catalog would report it in (Snowflake upper, UC
    lower).
    """
    quote_chars = ('"',) if engine == "snowflake" else ('"', "`")
    for quote in quote_chars:
        if len(part) >= 2 and part.startswith(quote) and part.endswith(quote):
            return part[1:-1]
    return part.upper() if engine == "snowflake" else part.lower()


def _require_config(config: dict[str, Any], field: str, conn_type: str) -> str:
    value = config.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{conn_type} asset identity requires a non-empty config {field!r}")
    return value


def _require_target(target: dict[str, Any], field: str, conn_type: str) -> str:
    value = target.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{conn_type} asset identity requires a non-empty target {field!r}")
    return value


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
