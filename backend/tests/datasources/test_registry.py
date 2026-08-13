"""Runner-registry dispatch tests (#146).

`build_check_runner` routes by `connection.type` to the right `CheckRunner`
builder, so the worker never branches on the type. Builders are exercised far
enough to return a runner (no live connection — that's lazy), asserting the
concrete runner class per type plus the error paths.
"""

import pytest

from backend.app.datasources.flatfile import FlatFileCheckRunner
from backend.app.datasources.iceberg import IcebergCheckRunner
from backend.app.datasources.registry import UnsupportedConnectionTypeError, build_check_runner
from backend.app.datasources.snowflake import SnowflakeCheckRunner
from backend.app.datasources.unity_catalog import UnityCatalogCheckRunner
from backend.tests.support.fake_secret_store import FakeSecretStore

_SNOWFLAKE_CONFIG = {
    "account": "ab12345.eu-west-1",
    "user": "svc_dataq",
    "database": "ANALYTICS",
    "schema": "FINANCE",
    "warehouse": "WH_DQ",
    "role": "DQ_ROLE",
}
_UC_CONFIG = {"workspace_url": "https://adb-1234.5.azuredatabricks.net", "warehouse_id": "abc123"}
_S3_CONFIG = {"bucket": "data", "region": "eu-west-1"}
_ICEBERG_CONFIG = {
    "catalog_type": "rest",
    "catalog_uri": "https://catalog.example.com",
    "secret_property": "token",
}


def test_dispatches_snowflake() -> None:
    runner = build_check_runner(
        conn_type="snowflake",
        config=_SNOWFLAKE_CONFIG,
        secret_ref="sf",
        secret_store=FakeSecretStore(default="secret"),
    )
    assert isinstance(runner, SnowflakeCheckRunner)


@pytest.mark.parametrize("conn_type", ["s3", "adls_gen2"])
def test_dispatches_flatfile(conn_type: str) -> None:
    runner = build_check_runner(
        conn_type=conn_type,
        config=_S3_CONFIG,
        secret_ref="ff",
        secret_store=FakeSecretStore(default="secret"),
    )
    assert isinstance(runner, FlatFileCheckRunner)


def test_dispatches_unity_catalog_with_catalog() -> None:
    runner = build_check_runner(
        conn_type="unity_catalog",
        config=_UC_CONFIG,
        secret_ref="pat",
        secret_store=FakeSecretStore(default="secret"),
        catalog="main",
    )
    assert isinstance(runner, UnityCatalogCheckRunner)


def test_dispatches_iceberg() -> None:
    runner = build_check_runner(
        conn_type="iceberg",
        config=_ICEBERG_CONFIG,
        secret_ref="iceberg-cred",
        secret_store=FakeSecretStore(default="secret"),
    )
    assert isinstance(runner, IcebergCheckRunner)


def test_unity_catalog_without_catalog_raises() -> None:
    with pytest.raises(UnsupportedConnectionTypeError):
        build_check_runner(
            conn_type="unity_catalog",
            config=_UC_CONFIG,
            secret_ref="pat",
            secret_store=FakeSecretStore(default="secret"),
        )


@pytest.mark.parametrize("conn_type", ["adf", "airflow", "bogus"])
def test_non_datasource_or_unknown_type_raises(conn_type: str) -> None:
    """Orchestration providers have no runner; nor does an unknown type."""
    with pytest.raises(UnsupportedConnectionTypeError):
        build_check_runner(
            conn_type=conn_type,
            config={},
            secret_ref="x",
            secret_store=FakeSecretStore(default="secret"),
        )
