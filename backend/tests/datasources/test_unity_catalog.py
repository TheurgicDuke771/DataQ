"""Unity Catalog connection adapter tests — config validation + the SELECT 1 probe.

No live Databricks: ``databricks.sql.connect`` is monkeypatched so the
warehouse probe runs against a fake. The adapter is DB-free, so these are pure
unit tests (no db_session).
"""

from typing import Any

import pytest
from databricks import sql
from pydantic import ValidationError

from backend.app.datasources.unity_catalog import (
    UnityCatalogConfig,
    UnityCatalogConnectionAdapter,
)

_UC_CONFIG = {
    "workspace_url": "https://adb-1234.5.azuredatabricks.net",
    "warehouse_id": "abc123def456",
}


# ───────────────────────── validate_config ─────────────────────────


def test_validate_config_accepts_config() -> None:
    cfg = UnityCatalogConnectionAdapter().validate_config(dict(_UC_CONFIG))
    assert isinstance(cfg, UnityCatalogConfig)
    assert cfg.warehouse_id == "abc123def456"


def test_config_derives_hostname_and_http_path() -> None:
    cfg = UnityCatalogConfig.model_validate(_UC_CONFIG)
    assert cfg.server_hostname == "adb-1234.5.azuredatabricks.net"
    assert cfg.http_path == "/sql/1.0/warehouses/abc123def456"


def test_validate_config_rejects_non_http_workspace_url() -> None:
    with pytest.raises(ValidationError, match="http"):
        UnityCatalogConnectionAdapter().validate_config(
            {"workspace_url": "adb-1234.azuredatabricks.net", "warehouse_id": "w"}
        )


def test_validate_config_strips_trailing_slash() -> None:
    cfg = UnityCatalogConnectionAdapter().validate_config(
        {"workspace_url": "https://adb-1.azuredatabricks.net/", "warehouse_id": "w"}
    )
    assert cfg.workspace_url == "https://adb-1.azuredatabricks.net"


def test_validate_config_rejects_missing_warehouse_id() -> None:
    with pytest.raises(ValidationError):
        UnityCatalogConnectionAdapter().validate_config(
            {"workspace_url": "https://adb-1.azuredatabricks.net"}
        )


def test_validate_config_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        UnityCatalogConnectionAdapter().validate_config({**_UC_CONFIG, "catalog": "main"})


# ───────────────────────── test() connectivity ─────────────────────


def test_test_runs_select_1_with_pat(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

    class _FakeCursor:
        def execute(self, query: str) -> None:
            calls["query"] = query

        def fetchone(self) -> tuple[int]:
            calls["fetched"] = True
            return (1,)

        def close(self) -> None:
            calls["cursor_closed"] = True

    class _FakeConnection:
        def cursor(self) -> _FakeCursor:
            return _FakeCursor()

        def close(self) -> None:
            calls["conn_closed"] = True

    def fake_connect(**kwargs: Any) -> _FakeConnection:
        calls["connect_kwargs"] = kwargs
        return _FakeConnection()

    monkeypatch.setattr(sql, "connect", fake_connect)
    UnityCatalogConnectionAdapter().test(dict(_UC_CONFIG), "dapi-pat-token")  # no raise

    assert calls["connect_kwargs"]["server_hostname"] == "adb-1234.5.azuredatabricks.net"
    assert calls["connect_kwargs"]["http_path"] == "/sql/1.0/warehouses/abc123def456"
    assert calls["connect_kwargs"]["access_token"] == "dapi-pat-token"
    assert calls["query"] == "SELECT 1"
    assert calls["fetched"] is True
    assert calls["cursor_closed"] is True
    assert calls["conn_closed"] is True


def test_test_raises_and_closes_when_query_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: dict[str, bool] = {}

    class _FakeCursor:
        def execute(self, query: str) -> None:
            raise RuntimeError("warehouse stopped")

        def close(self) -> None:
            closed["cursor"] = True

    class _FakeConnection:
        def cursor(self) -> _FakeCursor:
            return _FakeCursor()

        def close(self) -> None:
            closed["conn"] = True

    monkeypatch.setattr(sql, "connect", lambda **kw: _FakeConnection())
    with pytest.raises(RuntimeError, match="warehouse stopped"):
        UnityCatalogConnectionAdapter().test(dict(_UC_CONFIG), "dapi-pat-token")
    assert closed["cursor"] is True  # finally-closes the cursor
    assert closed["conn"] is True  # …and the connection


# ───────────────────────── GX runner (build_databricks_url, runner) ─

import pandas as pd  # noqa: E402

from backend.app.datasources.base import CheckSpec  # noqa: E402
from backend.app.datasources.unity_catalog import (  # noqa: E402
    UnityCatalogCheckRunner,
    build_databricks_url,
    build_unity_catalog_runner,
)


class _FakeStore:
    def get(self, name: str) -> str:
        return "pat-token"

    def set(self, name: str, value: str) -> None:  # read-only test double
        raise NotImplementedError


def test_build_databricks_url_encodes_parts() -> None:
    cfg = UnityCatalogConfig.model_validate(_UC_CONFIG)
    url = build_databricks_url(cfg, "a b/c")
    assert url.startswith("databricks://token:a+b%2Fc@adb-1234.5.azuredatabricks.net")
    # http_path is URL-encoded; no catalog pinned by default
    assert "http_path=%2Fsql%2F1.0%2Fwarehouses%2Fabc123def456" in url
    assert "catalog=" not in url


def test_build_databricks_url_pins_catalog() -> None:
    cfg = UnityCatalogConfig.model_validate(_UC_CONFIG)
    assert "&catalog=main" in build_databricks_url(cfg, "t", catalog="main")


def test_build_unity_catalog_runner_resolves_pat() -> None:
    runner = build_unity_catalog_runner(
        config=dict(_UC_CONFIG), secret_ref="kv-ref", secret_store=_FakeStore(), catalog="main"
    )
    assert isinstance(runner, UnityCatalogCheckRunner)


def test_build_unity_catalog_runner_requires_secret_ref() -> None:
    with pytest.raises(ValueError, match="secret_ref"):
        build_unity_catalog_runner(
            config=dict(_UC_CONFIG), secret_ref=None, secret_store=_FakeStore(), catalog="main"
        )


def _runner_over(df: pd.DataFrame, monkeypatch: pytest.MonkeyPatch) -> UnityCatalogCheckRunner:
    runner = UnityCatalogCheckRunner(
        config=UnityCatalogConfig.model_validate(_UC_CONFIG), token="t", catalog="main"
    )
    # Replace the live reflect+read seam with a canned frame; GX still runs for real.
    monkeypatch.setattr(runner, "_read_table", lambda **kwargs: df)
    return runner


def test_run_checks_runs_gx_on_dataframe(monkeypatch: pytest.MonkeyPatch) -> None:
    df = pd.DataFrame({"id": [1, 2, None], "amt": [10, 20, 30]})
    runner = _runner_over(df, monkeypatch)
    outcome = runner.run_checks(
        table="orders",
        schema="sales",
        checks=[
            CheckSpec("expect_column_values_to_not_be_null", {"column": "id"}),
            CheckSpec("expect_table_row_count_to_be_between", {"min_value": 1, "max_value": 10}),
        ],
    )
    assert outcome.success is False
    by_type = {c.expectation_type: c for c in outcome.checks}
    assert by_type["expect_column_values_to_not_be_null"].success is False
    assert by_type["expect_table_row_count_to_be_between"].success is True
    assert by_type["expect_table_row_count_to_be_between"].observed_value == {"observed_value": 3}


def test_run_checks_all_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    df = pd.DataFrame({"id": [1, 2, 3]})
    runner = _runner_over(df, monkeypatch)
    outcome = runner.run_checks(
        table="t",
        schema="s",
        checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "id"})],
    )
    assert outcome.success is True
    assert outcome.checks[0].success is True


def test_databricks_sqlalchemy_dialect_is_installed() -> None:
    """Dependency contract (#535): `_read_table` does
    `create_engine('databricks://…')`, whose dialect lives in the separate
    `databricks-sqlalchemy` package since databricks-sql-connector 4.x —
    tests mock the runner seam, so without this check a missing dialect only
    surfaces as a failed run in production. No network: dialect load only.
    """
    from sqlalchemy import create_engine

    engine = create_engine(
        "databricks://token:x@example.cloud.databricks.com"
        "?http_path=/sql/1.0/warehouses/x&catalog=c"
    )
    assert engine.dialect.name == "databricks"
