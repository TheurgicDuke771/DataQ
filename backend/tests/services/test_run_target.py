"""Unit tests for run_target.resolve_target / validate_target (#215).

Pure functions — no DB, no datasource. Covers each datasource's required field,
the targetless / wrong-datasource error paths, and that a flat-file path rides
the runner's `table` slot (the table-shaped CheckRunner contract).
"""

import pytest

from backend.app.services.run_target import (
    SuiteTargetInvalidError,
    resolve_target,
    validate_target,
)


def test_snowflake_resolves_table_and_optional_schema() -> None:
    r = resolve_target("snowflake", {"table": "ORDERS", "schema": "SALES"})
    assert (r.table, r.schema, r.catalog) == ("ORDERS", "SALES", None)


def test_snowflake_schema_optional() -> None:
    r = resolve_target("snowflake", {"table": "ORDERS"})
    assert (r.table, r.schema, r.catalog) == ("ORDERS", None, None)


def test_unity_catalog_requires_catalog_and_table() -> None:
    r = resolve_target("unity_catalog", {"catalog": "main", "schema": "sales", "table": "orders"})
    assert (r.table, r.schema, r.catalog) == ("orders", "sales", "main")


def test_unity_catalog_missing_catalog_raises() -> None:
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target("unity_catalog", {"table": "orders"})


@pytest.mark.parametrize("conn_type", ["adls_gen2", "s3"])
def test_flatfile_path_rides_table_slot(conn_type: str) -> None:
    # The CheckRunner interface is table-shaped; the file path is the `table`.
    r = resolve_target(conn_type, {"path": "data/orders.csv"})
    assert (r.table, r.schema, r.catalog) == ("data/orders.csv", None, None)


@pytest.mark.parametrize("conn_type", ["adls_gen2", "s3"])
def test_flatfile_missing_path_raises(conn_type: str) -> None:
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target(conn_type, {"table": "orders"})  # SQL field, wrong datasource


def test_snowflake_missing_table_raises() -> None:
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target("snowflake", {"schema": "SALES"})


def test_blank_table_is_rejected() -> None:
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target("snowflake", {"table": "   "})


@pytest.mark.parametrize("target", [None, {}])
def test_targetless_suite_raises(target: dict[str, str] | None) -> None:
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target("snowflake", target)


@pytest.mark.parametrize("conn_type", ["adf", "airflow"])
def test_orchestration_types_have_no_run_path(conn_type: str) -> None:
    # ADF / Airflow are orchestration providers, never suite datasources.
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target(conn_type, {"table": "x"})


def test_validate_target_is_resolve_without_return() -> None:
    validate_target("snowflake", {"table": "ORDERS"})  # no raise
    with pytest.raises(SuiteTargetInvalidError):
        validate_target("snowflake", {"schema": "SALES"})
