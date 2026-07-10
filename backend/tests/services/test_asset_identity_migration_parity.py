"""Parity: the migration's frozen resolver == the app's `asset_identity` resolver.

The `f8b9c0d1e2a3_add_assets_entity` migration carries a **deliberately frozen,
self-contained copy** of `asset_identity.resolve_asset_identity` (a migration must
not import app code). Nothing structurally enforces the two stay in lock-step, and
this review already found both copies had drifted into the same two bugs (Snowflake
account normalization + regex-vs-glob pattern). This test `importlib`-loads the
migration module and runs an identity fixture battery — every datasource type plus
the normalization edge cases — through both `_resolve_identity` (migration) and
`resolve_asset_identity` (app), asserting identical `(namespace, name)` outputs OR
identical raise-vs-return behavior, so silent drift becomes impossible.
"""

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from backend.app.services.asset_identity import resolve_asset_identity

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "f8b9c0d1e2a3_add_assets_entity.py"
)


def _load_frozen_resolver() -> Any:
    """Import the migration module in isolation and return its `_resolve_identity`."""
    spec = importlib.util.spec_from_file_location("_asset_migration_frozen", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._resolve_identity


_FROZEN_RESOLVE = _load_frozen_resolver()


# (conn_type, config, target) — every datasource + the edges this review touched.
_FIXTURES: list[tuple[str, dict[str, Any], dict[str, Any]]] = [
    # ── snowflake: account normalization edge cases ──
    ("snowflake", {"account": "abc123", "database": "db", "schema": "s"}, {"table": "t"}),
    # locator + hyphenated region → `.aws` appended (hyphen scoped to parts[0])
    (
        "snowflake",
        {"account": "xy12345.us-east-1", "database": "db"},
        {"table": "t", "schema": "s"},
    ),
    # org-account with a trailing dot segment → parts[0] only
    (
        "snowflake",
        {"account": "myorg-myacct.extra", "database": "db"},
        {"table": "t", "schema": "s"},
    ),
    # full 3-part account → unchanged
    (
        "snowflake",
        {"account": "abc.uswest1.azure", "database": "db"},
        {"table": "t", "schema": "s"},
    ),
    # quoted identifiers (case-preserved) + quoted-empty (must raise in both)
    ("snowflake", {"account": "abc123", "database": "db", "schema": "s"}, {"table": '"Mixed"'}),
    ("snowflake", {"account": "abc123", "database": "db", "schema": "s"}, {"table": '""'}),
    # missing required key → raise in both
    ("snowflake", {"account": "abc123", "database": "db"}, {"table": "t"}),
    ("snowflake", {"account": "   ", "database": "db", "schema": "s"}, {"table": "t"}),
    # ── unity_catalog: normal + scheme-less workspace_url ──
    (
        "unity_catalog",
        {"workspace_url": "https://adb-1.2.azuredatabricks.net"},
        {"catalog": "MAIN", "schema": "SALES", "table": "ORDERS"},
    ),
    (
        "unity_catalog",
        {"workspace_url": "adb-1234.azuredatabricks.net"},  # scheme-less
        {"catalog": "main", "table": "orders"},  # default schema
    ),
    ("unity_catalog", {"workspace_url": "https://x"}, {"table": "t"}),  # missing catalog → raise
    # ── adls_gen2: blob/dfs/scheme-less + path + regex pattern ──
    (
        "adls_gen2",
        {"account_url": "https://mylake.blob.core.windows.net", "container": "raw"},
        {"path": "/retail/orders.csv"},
    ),
    (
        "adls_gen2",
        {"account_url": "mylake.dfs.core.windows.net", "container": "raw"},  # scheme-less
        {"pattern": r"retail/orders_(\d+)\.csv"},
    ),
    ("adls_gen2", {"container": "raw"}, {"path": "x"}),  # missing account_url → raise
    # ── s3: path + regex patterns (leading metachar, capture group, plain) ──
    ("s3", {"bucket": "b"}, {"path": "retail/orders.csv"}),
    ("s3", {"bucket": "b"}, {"pattern": r"orders_(\d{4}-\d{2}-\d{2})\.csv"}),
    ("s3", {"bucket": "b"}, {"pattern": r"(\d+)\.csv"}),  # leading metachar → whole pattern
    ("s3", {"bucket": "b"}, {"pattern": "retail/orders/2026-*.csv"}),
    ("s3", {}, {"path": "x"}),  # missing bucket → raise
    # ── iceberg: uri verbatim / file default / namespace fold ──
    ("iceberg", {"catalog_uri": "https://cat"}, {"namespace": "retail", "table": "po"}),
    ("iceberg", {}, {"table": "po"}),
    ("iceberg", {}, {"namespace": "retail"}),  # missing table → raise
    # ── orchestration + unknown type → raise in both ──
    ("adf", {"anything": "x"}, {"table": "t"}),
    ("airflow", {}, {"table": "t"}),
    ("made_up", {}, {"table": "t"}),
]


def _app_outcome(conn_type: str, config: dict[str, Any], target: dict[str, Any]) -> Any:
    try:
        ident = resolve_asset_identity(conn_type, config, target)
        return ("ok", ident.namespace, ident.name)
    except ValueError:
        return ("error",)


def _frozen_outcome(conn_type: str, config: dict[str, Any], target: dict[str, Any]) -> Any:
    try:
        namespace, name = _FROZEN_RESOLVE(conn_type, config, target)
        return ("ok", namespace, name)
    except ValueError:
        return ("error",)


@pytest.mark.parametrize("conn_type,config,target", _FIXTURES)
def test_migration_frozen_resolver_matches_app(
    conn_type: str, config: dict[str, Any], target: dict[str, Any]
) -> None:
    assert _app_outcome(conn_type, config, target) == _frozen_outcome(conn_type, config, target)
