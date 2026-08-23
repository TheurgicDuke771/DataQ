"""Resolve a connection + suite target to an OpenLineage-shaped asset identity."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from backend.app.core.uri_credentials import strip_uri_credentials

# `pattern` is a **regex** (flatfile.py `re.compile`s it, first capture group = batch key), not a
# glob — so the asset's directory prefix is the literal text before the first regex metacharacter.
_REGEX_METACHARS = re.compile(r"[\\.^$*+?{}\[\]|()]")


@dataclass(frozen=True)
class AssetIdentity:
    """The OpenLineage ``namespace`` + ``name`` pair that keys an asset row."""

    namespace: str
    name: str


def resolve_asset_identity(
    conn_type: str, config: dict[str, Any], target: dict[str, Any]
) -> AssetIdentity:
    """Resolve a connection's ``config`` + a suite's ``target`` to an `AssetIdentity`."""
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
    """Normalize a Snowflake account identifier (openlineage's ``fix_account_name``)."""
    account = account.strip()
    if not account:
        raise ValueError("snowflake account must be non-empty")
    parts = account.split(".")
    if "-" in parts[0]:
        return parts[0]
    if len(parts) == 1:
        return f"{parts[0]}.us-west-1.aws"
    if len(parts) == 2:
        return f"{parts[0]}.{parts[1]}.aws"
    return account


def format_snowflake_name(database: str, schema: str, table: str) -> str:
    """Join a Snowflake ``DB.SCHEMA.TABLE`` into the OpenLineage ``name`` string."""
    return ".".join(_normalize_part(part, engine="snowflake") for part in (database, schema, table))


def format_unity_catalog_name(catalog: str, schema: str, table: str) -> str:
    """Join a Unity Catalog ``catalog.schema.table`` into the OpenLineage ``name``."""
    return ".".join(
        _normalize_part(part, engine="unity_catalog") for part in (catalog, schema, table)
    )


def _resolve_snowflake(config: dict[str, Any], target: dict[str, Any]) -> AssetIdentity:
    account = _require(config, "account", "snowflake", "config")
    database = _require(config, "database", "snowflake", "config")
    schema = _str_or_none(target.get("schema")) or _str_or_none(config.get("schema"))
    if not schema:
        raise ValueError("snowflake asset identity requires a 'schema' (target or config)")
    table = _require(target, "table", "snowflake", "target")
    namespace = f"snowflake://{normalize_snowflake_account(account)}"
    name = format_snowflake_name(database, schema, table)
    return AssetIdentity(namespace=namespace, name=name)


def _resolve_unity_catalog(config: dict[str, Any], target: dict[str, Any]) -> AssetIdentity:
    workspace_url = _require(config, "workspace_url", "unity_catalog", "config")
    netloc = _url_host(workspace_url)
    if not netloc:
        raise ValueError("unity_catalog asset identity requires a valid 'workspace_url'")
    catalog = _require(target, "catalog", "unity_catalog", "target")
    schema = _str_or_none(target.get("schema")) or "default"
    table = _require(target, "table", "unity_catalog", "target")
    namespace = f"unitycatalog://{netloc}"
    name = format_unity_catalog_name(catalog, schema, table)
    return AssetIdentity(namespace=namespace, name=name)


def _resolve_adls_gen2(config: dict[str, Any], target: dict[str, Any]) -> AssetIdentity:
    container = _require(config, "container", "adls_gen2", "config")
    account_url = _require(config, "account_url", "adls_gen2", "config")
    host = _url_host(account_url)
    account = host.split(".")[0] if host else ""
    if not account:
        raise ValueError("adls_gen2 asset identity requires a valid 'account_url'")
    namespace = f"abfss://{container}@{account}.dfs.core.windows.net"
    name = _flatfile_name(target, "adls_gen2")
    return AssetIdentity(namespace=namespace, name=name)


def _resolve_s3(config: dict[str, Any], target: dict[str, Any]) -> AssetIdentity:
    bucket = _require(config, "bucket", "s3", "config")
    endpoint_url = _str_or_none(config.get("endpoint_url"))
    # No endpoint_url means AWS: keep the namespace byte-stable at `s3://{bucket}` — this form is
    # the OpenLineage naming-spec convention and is already persisted on `assets` rows in
    # production; changing it forks every existing S3 asset and orphans its lineage/incidents.
    namespace = (
        f"s3://{_s3_endpoint_authority(endpoint_url)}/{bucket}"
        if endpoint_url
        else f"s3://{bucket}"
    )
    name = _flatfile_name(target, "s3")
    return AssetIdentity(namespace=namespace, name=name)


#: Ports implied by the scheme, so an explicit `:443` on `https://` (or `:80` on `http://`) doesn't
#: fork the namespace from the equivalent endpoint written without a port.
_S3_DEFAULT_PORTS = {"http": 80, "https": 443}


def _s3_endpoint_authority(endpoint_url: str) -> str:
    """``endpoint_url``'s host[:port] — scheme stripped, default port elided, lowercased."""
    parsed = urlparse(endpoint_url)
    host = parsed.hostname or ""
    # `urlparse().hostname` STRIPS the brackets off an IPv6 literal.
    if ":" in host:
        host = f"[{host}]"
    port = parsed.port
    default_port = _S3_DEFAULT_PORTS.get(parsed.scheme)
    return f"{host}:{port}" if port is not None and port != default_port else host


def _resolve_iceberg(config: dict[str, Any], target: dict[str, Any]) -> AssetIdentity:
    catalog_uri = config.get("catalog_uri")
    # NEVER let a credential into the identity (#826).
    namespace = (
        strip_uri_credentials(catalog_uri.strip())
        if isinstance(catalog_uri, str) and catalog_uri.strip()
        else "file"
    )
    table = _require(target, "table", "iceberg", "target")
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
    """The literal directory prefix in front of the first regex metacharacter."""
    match = _REGEX_METACHARS.search(pattern)
    prefix = pattern[: match.start()] if match else pattern
    if "/" in prefix:
        base = prefix[: prefix.rfind("/") + 1]
    elif prefix:
        base = prefix
    else:
        base = pattern
    return base.lstrip("/")


def _normalize_part(part: str, *, engine: str) -> str:
    """Fold one dotted-name segment to the engine's unquoted-identifier case."""
    quote_chars = ('"',) if engine == "snowflake" else ('"', "`")
    for quote in quote_chars:
        if len(part) >= 2 and part.startswith(quote) and part.endswith(quote):
            inner = part[1:-1]
            if not inner:
                # A quoted-empty identifier (`""`) slips past _require (the raw value is non-empty)
                # but yields an empty dotted segment.
                raise ValueError("identifier part is empty after stripping quotes")
            return inner
    return part.upper() if engine == "snowflake" else part.lower()


def _url_host(url: str) -> str:
    """The host of ``url``, tolerating a scheme-less value."""
    parsed = urlparse(url)
    return parsed.netloc or parsed.path.split("/", 1)[0]


def _require(d: dict[str, Any], field: str, conn_type: str, kind: str) -> str:
    """Require a non-empty string ``field`` on config/target dict ``d`` (``kind``)."""
    value = d.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{conn_type} asset identity requires a non-empty {kind} {field!r}")
    return value


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
