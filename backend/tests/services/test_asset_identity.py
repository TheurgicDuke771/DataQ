"""Unit tests for asset_identity.resolve_asset_identity / normalize_snowflake_account.

Pure (no DB, no datasource): each datasource's namespace/name construction,
the OpenLineage normalization rules (quote-strip, engine-returned case, the
Snowflake account fixup), the flat-file pattern base-prefix rule, and the
orchestration-type / missing-required-key error paths (#757, ADR 0034).
"""

import pytest

from backend.app.services.asset_identity import (
    AssetIdentity,
    normalize_snowflake_account,
    resolve_asset_identity,
)

# ───────────────────────── snowflake ─────────────────────────


def test_snowflake_hyphenated_account_passes_through() -> None:
    assert normalize_snowflake_account("myorg-myaccount") == "myorg-myaccount"


def test_snowflake_bare_locator_gets_default_region_and_cloud() -> None:
    assert normalize_snowflake_account("abc123") == "abc123.us-west-1.aws"


def test_snowflake_locator_plus_region_gets_cloud() -> None:
    # No hyphen anywhere in this locator+region form, so it takes the
    # dot-segment path rather than the org-account passthrough.
    assert normalize_snowflake_account("abc123.uswest1") == "abc123.uswest1.aws"


def test_snowflake_full_three_part_unchanged() -> None:
    assert normalize_snowflake_account("abc123.uswest1.azure") == "abc123.uswest1.azure"


def test_snowflake_normalize_strips_whitespace() -> None:
    assert normalize_snowflake_account("  abc123  ") == "abc123.us-west-1.aws"


def test_snowflake_normalize_empty_raises() -> None:
    with pytest.raises(ValueError):
        normalize_snowflake_account("   ")


def test_snowflake_identity_uppercases_unquoted_parts() -> None:
    identity = resolve_asset_identity(
        "snowflake",
        {"account": "myorg-myaccount", "database": "retail", "schema": "sales"},
        {"table": "orders"},
    )
    assert identity == AssetIdentity(
        namespace="snowflake://myorg-myaccount", name="RETAIL.SALES.ORDERS"
    )


def test_snowflake_identity_quoted_part_keeps_case_strips_quotes() -> None:
    identity = resolve_asset_identity(
        "snowflake",
        {"account": "myorg-myaccount", "database": "retail", "schema": "sales"},
        {"table": '"MixedCase"'},
    )
    assert identity.name == "RETAIL.SALES.MixedCase"


def test_snowflake_target_schema_overrides_config_schema() -> None:
    identity = resolve_asset_identity(
        "snowflake",
        {"account": "abc123", "database": "retail", "schema": "config_schema"},
        {"table": "orders", "schema": "target_schema"},
    )
    assert identity.name == "RETAIL.TARGET_SCHEMA.ORDERS"


def test_snowflake_bare_locator_namespace() -> None:
    identity = resolve_asset_identity(
        "snowflake",
        {"account": "abc123", "database": "retail", "schema": "sales"},
        {"table": "orders"},
    )
    assert identity.namespace == "snowflake://abc123.us-west-1.aws"


def test_snowflake_missing_table_raises() -> None:
    with pytest.raises(ValueError):
        resolve_asset_identity(
            "snowflake",
            {"account": "abc123", "database": "retail", "schema": "sales"},
            {},
        )


def test_snowflake_missing_schema_raises() -> None:
    with pytest.raises(ValueError):
        resolve_asset_identity(
            "snowflake",
            {"account": "abc123", "database": "retail"},
            {"table": "orders"},
        )


# ───────────────────────── unity_catalog ─────────────────────────


def test_unity_catalog_namespace_from_host() -> None:
    identity = resolve_asset_identity(
        "unity_catalog",
        {"workspace_url": "https://adb-123.4.azuredatabricks.net"},
        {"catalog": "main", "schema": "sales", "table": "orders"},
    )
    assert identity.namespace == "unitycatalog://adb-123.4.azuredatabricks.net"
    assert identity.name == "main.sales.orders"


def test_unity_catalog_namespace_keeps_port() -> None:
    identity = resolve_asset_identity(
        "unity_catalog",
        {"workspace_url": "https://localhost:8443"},
        {"catalog": "main", "schema": "sales", "table": "orders"},
    )
    assert identity.namespace == "unitycatalog://localhost:8443"


def test_unity_catalog_default_schema() -> None:
    identity = resolve_asset_identity(
        "unity_catalog",
        {"workspace_url": "https://adb-123.4.azuredatabricks.net"},
        {"catalog": "main", "table": "orders"},
    )
    assert identity.name == "main.default.orders"


def test_unity_catalog_lowercases_unquoted_parts() -> None:
    identity = resolve_asset_identity(
        "unity_catalog",
        {"workspace_url": "https://adb-123.4.azuredatabricks.net"},
        {"catalog": "MAIN", "schema": "SALES", "table": "ORDERS"},
    )
    assert identity.name == "main.sales.orders"


def test_unity_catalog_backtick_quoted_part_keeps_case() -> None:
    identity = resolve_asset_identity(
        "unity_catalog",
        {"workspace_url": "https://adb-123.4.azuredatabricks.net"},
        {"catalog": "main", "schema": "sales", "table": "`MixedCase`"},
    )
    assert identity.name == "main.sales.MixedCase"


def test_unity_catalog_missing_catalog_raises() -> None:
    with pytest.raises(ValueError):
        resolve_asset_identity(
            "unity_catalog",
            {"workspace_url": "https://adb-123.4.azuredatabricks.net"},
            {"table": "orders"},
        )


# ───────────────────────── adls_gen2 ─────────────────────────


def test_adls_account_from_blob_url() -> None:
    identity = resolve_asset_identity(
        "adls_gen2",
        {"account_url": "https://mylake.blob.core.windows.net", "container": "raw"},
        {"path": "retail/orders.csv"},
    )
    assert identity.namespace == "abfss://raw@mylake.dfs.core.windows.net"
    assert identity.name == "retail/orders.csv"


def test_adls_account_from_dfs_url() -> None:
    identity = resolve_asset_identity(
        "adls_gen2",
        {"account_url": "https://mylake.dfs.core.windows.net", "container": "raw"},
        {"path": "retail/orders.csv"},
    )
    assert identity.namespace == "abfss://raw@mylake.dfs.core.windows.net"


def test_adls_path_strips_single_leading_slash() -> None:
    identity = resolve_asset_identity(
        "adls_gen2",
        {"account_url": "https://mylake.blob.core.windows.net", "container": "raw"},
        {"path": "/retail/orders.csv"},
    )
    assert identity.name == "retail/orders.csv"


def test_adls_missing_account_url_raises() -> None:
    with pytest.raises(ValueError):
        resolve_asset_identity("adls_gen2", {"container": "raw"}, {"path": "retail/orders.csv"})


# ───────────────────────── s3 ─────────────────────────


def test_s3_bucket_namespace() -> None:
    identity = resolve_asset_identity("s3", {"bucket": "my-bucket"}, {"path": "retail/orders.csv"})
    assert identity.namespace == "s3://my-bucket"
    assert identity.name == "retail/orders.csv"


def test_s3_pattern_glob_mid_filename_yields_parent_dir() -> None:
    identity = resolve_asset_identity(
        "s3", {"bucket": "my-bucket"}, {"pattern": "retail/orders/2026-*.csv"}
    )
    assert identity.name == "retail/orders/"


def test_s3_pattern_glob_in_first_segment_falls_back_to_whole_pattern() -> None:
    identity = resolve_asset_identity("s3", {"bucket": "my-bucket"}, {"pattern": "2026-*.csv"})
    assert identity.name == "2026-*.csv"


def test_s3_pattern_plain_path_no_glob_still_base_prefixes() -> None:
    # No glob metachar at all: still base-prefixed to its directory — a
    # pattern-shaped target is always a directory-scoped asset (Spark
    # convention), never the literal per-file match.
    identity = resolve_asset_identity(
        "s3", {"bucket": "my-bucket"}, {"pattern": "retail/orders/fixed.csv"}
    )
    assert identity.name == "retail/orders/"


def test_s3_missing_bucket_raises() -> None:
    with pytest.raises(ValueError):
        resolve_asset_identity("s3", {}, {"path": "orders.csv"})


def test_s3_empty_bucket_raises() -> None:
    with pytest.raises(ValueError):
        resolve_asset_identity("s3", {"bucket": "   "}, {"path": "orders.csv"})


def test_s3_missing_path_and_pattern_raises() -> None:
    with pytest.raises(ValueError):
        resolve_asset_identity("s3", {"bucket": "my-bucket"}, {})


# ───────────────────────── iceberg ─────────────────────────


def test_iceberg_rest_catalog_uri_verbatim() -> None:
    identity = resolve_asset_identity(
        "iceberg",
        {"catalog_type": "rest", "catalog_uri": "https://catalog.example.com"},
        {"namespace": "retail", "table": "purchase_orders"},
    )
    assert identity.namespace == "https://catalog.example.com"
    assert identity.name == "retail.purchase_orders"


def test_iceberg_no_uri_defaults_to_file() -> None:
    identity = resolve_asset_identity(
        "iceberg", {"catalog_type": "sql"}, {"namespace": "retail", "table": "purchase_orders"}
    )
    assert identity.namespace == "file"


def test_iceberg_namespace_folds_into_name_verbatim() -> None:
    identity = resolve_asset_identity(
        "iceberg", {}, {"namespace": "retail", "table": "purchase_orders"}
    )
    assert identity.name == "retail.purchase_orders"


def test_iceberg_no_namespace_uses_table_only() -> None:
    identity = resolve_asset_identity("iceberg", {}, {"table": "purchase_orders"})
    assert identity.name == "purchase_orders"


def test_iceberg_missing_table_raises() -> None:
    with pytest.raises(ValueError):
        resolve_asset_identity("iceberg", {}, {"namespace": "retail"})


# ───────────────────────── orchestration / general errors ─────────────────


@pytest.mark.parametrize("conn_type", ["adf", "airflow", "dbt"])
def test_orchestration_types_have_no_asset_identity(conn_type: str) -> None:
    with pytest.raises(ValueError):
        resolve_asset_identity(conn_type, {"anything": "x"}, {"table": "x"})


def test_empty_config_account_raises() -> None:
    with pytest.raises(ValueError):
        resolve_asset_identity(
            "snowflake",
            {"account": "   ", "database": "retail", "schema": "sales"},
            {"table": "orders"},
        )


# ───────────────────────── hostile inputs ─────────────────────────


def test_unicode_table_name_round_trips() -> None:
    identity = resolve_asset_identity(
        "snowflake",
        {"account": "myorg-myaccount", "database": "retail", "schema": "sales"},
        {"table": '"héllo_wörld_日本語"'},
    )
    assert identity.name == "RETAIL.SALES.héllo_wörld_日本語"


def test_unicode_iceberg_name_verbatim() -> None:
    identity = resolve_asset_identity("iceberg", {}, {"table": "日本語テーブル"})
    assert identity.name == "日本語テーブル"


def test_nul_byte_in_part_does_not_crash() -> None:
    # NUL bytes are not glob metachars and not whitespace, so this resolves
    # cleanly rather than raising — asserting the (odd but harmless) name
    # rather than a crash is the contract we care about here.
    identity = resolve_asset_identity(
        "s3", {"bucket": "my-bucket"}, {"path": "retail/orders\x00.csv"}
    )
    assert identity.name == "retail/orders\x00.csv"


def test_nul_byte_in_snowflake_table_uppercases_without_crash() -> None:
    identity = resolve_asset_identity(
        "snowflake",
        {"account": "myorg-myaccount", "database": "retail", "schema": "sales"},
        {"table": "ord\x00ers"},
    )
    assert identity.name == "RETAIL.SALES.ORD\x00ERS"


def test_non_string_target_field_raises_cleanly() -> None:
    with pytest.raises(ValueError):
        resolve_asset_identity(
            "snowflake",
            {"account": "abc123", "database": "retail", "schema": "sales"},
            {"table": 12345},
        )


def test_unknown_conn_type_raises() -> None:
    with pytest.raises(ValueError):
        resolve_asset_identity("made_up_type", {}, {"table": "x"})
