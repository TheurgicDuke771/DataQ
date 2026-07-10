"""Iceberg connection adapter + native read runner tests (ADR 0030, #716).

No live catalog: ``pyiceberg.catalog.load_catalog`` and the runner's
``_load_table`` seam are monkeypatched with fakes whose ``scan()`` returns a real
``pyarrow`` table built from a canned frame — so GX (run_checks) and the pure
monitor banding (run_monitors) run for real over the materialised data, while the
catalog/scan I/O is faked. The adapter is DB-free, so these are pure unit tests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
import pyarrow as pa
import pytest
from pydantic import ValidationError

from backend.app.datasources import iceberg as iceberg_mod
from backend.app.datasources.base import CheckSpec, MonitorSpec
from backend.app.datasources.iceberg import (
    IcebergCheckRunner,
    IcebergConfig,
    IcebergConnectionAdapter,
    build_iceberg_runner,
    list_iceberg_columns,
    read_iceberg_dataframe,
)

_REST_CONFIG = {
    "catalog_name": "prod",
    "catalog_type": "rest",
    "catalog_uri": "https://catalog.example.com",
    "warehouse": "s3://bucket/warehouse",
    "secret_property": "token",
}


# ───────────────────────── validate_config ─────────────────────────


def test_validate_config_accepts_rest_config() -> None:
    cfg = IcebergConnectionAdapter().validate_config(dict(_REST_CONFIG))
    assert isinstance(cfg, IcebergConfig)
    assert cfg.catalog_type == "rest"
    assert cfg.catalog_name == "prod"


def test_catalog_properties_injects_secret_last() -> None:
    cfg = IcebergConfig.model_validate(_REST_CONFIG)
    props = cfg.catalog_properties("SECRET-VALUE")
    assert props == {
        "type": "rest",
        "uri": "https://catalog.example.com",
        "warehouse": "s3://bucket/warehouse",
        "token": "SECRET-VALUE",
    }


def test_catalog_properties_omits_secret_when_absent() -> None:
    cfg = IcebergConfig.model_validate({"catalog_type": "sql", "catalog_uri": "sqlite:///w"})
    props = cfg.catalog_properties(None)
    assert props == {"type": "sql", "uri": "sqlite:///w"}
    assert "token" not in props


def test_catalog_properties_merges_extra_properties() -> None:
    cfg = IcebergConfig.model_validate(
        {"catalog_type": "glue", "properties": {"glue.region": "us-east-1"}}
    )
    props = cfg.catalog_properties(None)
    assert props["glue.region"] == "us-east-1"
    assert "uri" not in props  # glue needs no uri


def test_validate_config_requires_uri_for_rest() -> None:
    with pytest.raises(ValidationError, match="catalog_uri is required"):
        IcebergConfig.model_validate({"catalog_type": "rest"})


def test_validate_config_glue_needs_no_uri() -> None:
    cfg = IcebergConfig.model_validate({"catalog_type": "glue"})
    assert cfg.catalog_uri is None


def test_validate_config_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        IcebergConfig.model_validate({**_REST_CONFIG, "bogus": "x"})


def test_validate_config_rejects_unknown_catalog_type() -> None:
    with pytest.raises(ValidationError):
        IcebergConfig.model_validate({"catalog_type": "postgres", "catalog_uri": "u"})


# ───────────────────────── test() connectivity ─────────────────────


def test_test_loads_catalog_and_lists_namespaces(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

    class _FakeCatalog:
        def list_namespaces(self) -> list[tuple[str]]:
            calls["listed"] = True
            return [("sales",)]

    def fake_load_catalog(name: str, **props: Any) -> _FakeCatalog:
        calls["name"] = name
        calls["props"] = props
        return _FakeCatalog()

    monkeypatch.setattr("pyiceberg.catalog.load_catalog", fake_load_catalog)
    IcebergConnectionAdapter().test(dict(_REST_CONFIG), "tok")  # no raise

    assert calls["name"] == "prod"
    assert calls["props"]["token"] == "tok"
    assert calls["props"]["type"] == "rest"
    assert calls["listed"] is True


def test_test_propagates_catalog_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(name: str, **props: Any) -> Any:
        raise RuntimeError("catalog unreachable")

    monkeypatch.setattr("pyiceberg.catalog.load_catalog", boom)
    with pytest.raises(RuntimeError, match="catalog unreachable"):
        IcebergConnectionAdapter().test(dict(_REST_CONFIG), "tok")


# ───────────────────────── read runner (fakes over a real Arrow scan) ─


class _FakeScan:
    def __init__(self, table: pa.Table) -> None:
        self._table = table

    def to_arrow(self) -> pa.Table:
        return self._table

    def count(self) -> int:
        return int(self._table.num_rows)


class _FakeTable:
    """Stands in for a ``pyiceberg`` Table — ``scan()`` returns a real Arrow table
    (optionally projected to ``selected_fields``) so the runner's materialisation
    + GX + monitor math run for real."""

    def __init__(self, df: pd.DataFrame) -> None:
        self._arrow = pa.Table.from_pandas(df, preserve_index=False)

    def scan(self, *, selected_fields: tuple[str, ...] | None = None) -> _FakeScan:
        if selected_fields:
            return _FakeScan(self._arrow.select(list(selected_fields)))
        return _FakeScan(self._arrow)


def _runner_over(df: pd.DataFrame, monkeypatch: pytest.MonkeyPatch) -> IcebergCheckRunner:
    runner = IcebergCheckRunner(config=IcebergConfig.model_validate(_REST_CONFIG), secret="tok")
    monkeypatch.setattr(runner, "_load_table", lambda identifier: _FakeTable(df))
    return runner


class _FakeStore:
    def get(self, name: str) -> str:
        return "resolved-secret"

    def set(self, name: str, value: str) -> None:  # read-only double
        raise NotImplementedError

    def delete(self, name: str) -> None:
        raise NotImplementedError


def test_run_checks_runs_gx_on_arrow_backed_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    df = pd.DataFrame({"id": [1, 2, None], "amt": [10, 20, 30]})
    runner = _runner_over(df, monkeypatch)
    outcome = runner.run_checks(
        table="sales.orders",
        schema=None,
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
    runner = _runner_over(pd.DataFrame({"id": [1, 2, 3]}), monkeypatch)
    outcome = runner.run_checks(
        table="sales.orders",
        schema=None,
        checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "id"})],
    )
    assert outcome.success is True


def test_read_dataframe_uses_arrow_backed_dtypes(monkeypatch: pytest.MonkeyPatch) -> None:
    # The materialisation must go through Arrow-backed pandas (parity with the
    # flat-file/UC paths), not the numpy-dtype shortcut.
    runner = _runner_over(pd.DataFrame({"id": [1, 2, 3]}), monkeypatch)
    df = runner._read_dataframe("sales.orders")
    assert isinstance(df.dtypes["id"], pd.ArrowDtype)


# ───────────────────────── monitors (volume + freshness) ─────────────


def test_run_monitors_volume_in_range(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _runner_over(pd.DataFrame({"id": list(range(50))}), monkeypatch)
    [outcome] = runner.run_monitors(
        table="sales.orders",
        schema=None,
        monitors=[MonitorSpec("volume", {"min_rows": 10, "max_rows": 100})],
    )
    assert outcome.success is True
    assert outcome.metric_value == 0.0
    assert outcome.observed_value == {"row_count": 50, "deviation_pct": 0.0}


def test_run_monitors_volume_below_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _runner_over(pd.DataFrame({"id": [1, 2, 3]}), monkeypatch)
    [outcome] = runner.run_monitors(
        table="sales.orders",
        schema=None,
        monitors=[MonitorSpec("volume", {"min_rows": 10, "max_rows": 100})],
    )
    assert outcome.success is False
    assert outcome.metric_value == pytest.approx(70.0)  # (10-3)/10 * 100


def test_run_monitors_freshness_age(monkeypatch: pytest.MonkeyPatch) -> None:
    recent = datetime.now(UTC) - timedelta(hours=5)
    df = pd.DataFrame({"loaded_at": [recent - timedelta(hours=1), recent]})
    runner = _runner_over(df, monkeypatch)
    [outcome] = runner.run_monitors(
        table="sales.orders",
        schema=None,
        monitors=[MonitorSpec("freshness", {"column": "loaded_at"})],
    )
    assert outcome.metric_value == pytest.approx(5.0, abs=0.1)  # ~5h stale


def test_run_monitors_freshness_empty_table_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    df = pd.DataFrame({"loaded_at": pd.Series([], dtype="datetime64[ns, UTC]")})
    runner = _runner_over(df, monkeypatch)
    [outcome] = runner.run_monitors(
        table="sales.orders",
        schema=None,
        monitors=[MonitorSpec("freshness", {"column": "loaded_at"})],
    )
    assert outcome.errored is True  # MAX over no rows → can't assess (#122)


def test_run_monitors_load_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    # A catalog/load failure is a run-level failure (not N per-monitor errors): the
    # table loads once, before the banding loop, so the exception propagates.
    runner = IcebergCheckRunner(config=IcebergConfig.model_validate(_REST_CONFIG), secret="tok")
    monkeypatch.setattr(
        runner,
        "_load_table",
        lambda identifier: (_ for _ in ()).throw(RuntimeError("catalog down")),
    )
    with pytest.raises(RuntimeError, match="catalog down"):
        runner.run_monitors(
            table="sales.orders",
            schema=None,
            monitors=[MonitorSpec("volume", {"min_rows": 1, "max_rows": 100})],
        )


def test_run_monitors_bad_monitor_errors_only_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _runner_over(pd.DataFrame({"id": [1, 2, 3]}), monkeypatch)
    outcomes = runner.run_monitors(
        table="sales.orders",
        schema=None,
        monitors=[
            MonitorSpec("freshness", {"column": "does_not_exist"}),  # scan raises
            MonitorSpec("volume", {"min_rows": 1, "max_rows": 100}),  # still runs
        ],
    )
    assert outcomes[0].errored is True
    assert outcomes[1].errored is False
    assert outcomes[1].success is True


# ───────────────────────── build_iceberg_runner ─────────────────────


def test_build_iceberg_runner_resolves_secret() -> None:
    runner = build_iceberg_runner(
        config=dict(_REST_CONFIG), secret_ref="kv-ref", secret_store=_FakeStore()
    )
    assert isinstance(runner, IcebergCheckRunner)
    assert runner._secret == "resolved-secret"


def test_build_iceberg_runner_allows_credential_less_catalog() -> None:
    # A credential-less catalog (local warehouse, vended-credentials REST) has no
    # secret_ref — like the ADLS/S3 adapters. Must not raise.
    runner = build_iceberg_runner(
        config={"catalog_type": "sql", "catalog_uri": "sqlite:///w"},
        secret_ref=None,
        secret_store=_FakeStore(),
    )
    assert runner._secret is None


# ─── shared read/list helpers used by the column profiler (#721) ─────


class _SchemaField:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeSchema:
    def __init__(self, names: list[str]) -> None:
        self.fields = [_SchemaField(n) for n in names]


class _ProjectingScan:
    """Applies ``selected_fields`` projection + ``limit`` sampling to a real Arrow
    table, so the helper's projection/limit levers are exercised for real."""

    def __init__(self, arrow: pa.Table, selected: tuple[str, ...], limit: int | None) -> None:
        self._arrow = arrow
        self._selected = selected
        self._limit = limit

    def to_arrow(self) -> pa.Table:
        arrow = self._arrow
        if self._selected != ("*",):
            arrow = arrow.select(list(self._selected))
        if self._limit is not None:
            arrow = arrow.slice(0, self._limit)
        return arrow


class _SchemaTable:
    """A fake ``pyiceberg`` Table exposing ``schema()`` (for the no-scan lister)
    and a projecting/limiting ``scan()`` (for the sampled read)."""

    def __init__(self, df: pd.DataFrame) -> None:
        self._arrow = pa.Table.from_pandas(df, preserve_index=False)

    def schema(self) -> _FakeSchema:
        return _FakeSchema(list(self._arrow.column_names))

    def scan(
        self, *, selected_fields: tuple[str, ...] = ("*",), limit: int | None = None
    ) -> _ProjectingScan:
        return _ProjectingScan(self._arrow, selected_fields, limit)


def _cfg() -> IcebergConfig:
    return IcebergConfig.model_validate(_REST_CONFIG)


def _patch_load(monkeypatch: pytest.MonkeyPatch, table: Any) -> None:
    monkeypatch.setattr(iceberg_mod, "load_iceberg_table", lambda config, secret, identifier: table)


def test_read_iceberg_dataframe_projects_and_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6], "c": [7, 8, 9]})
    _patch_load(monkeypatch, _SchemaTable(df))
    out = read_iceberg_dataframe(_cfg(), "tok", "sales.orders", columns=["a", "c"], limit=2)
    assert list(out.columns) == ["a", "c"]  # 'b' never materialised
    assert len(out) == 2  # limit applied
    assert isinstance(out.dtypes["a"], pd.ArrowDtype)  # Arrow-backed parity


def test_read_iceberg_dataframe_full_read_when_no_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    _patch_load(monkeypatch, _SchemaTable(df))
    out = read_iceberg_dataframe(_cfg(), None, "ns.t")  # credential-less
    assert set(out.columns) == {"a", "b"} and len(out) == 2


def test_read_iceberg_dataframe_unknown_column_not_projected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A requested column absent from the schema is simply not selected (the caller
    # reports genuinely-missing columns as a clean 422 — never a scan crash).
    df = pd.DataFrame({"a": [1, 2]})
    _patch_load(monkeypatch, _SchemaTable(df))
    out = read_iceberg_dataframe(_cfg(), None, "ns.t", columns=["a", "ghost"])
    assert list(out.columns) == ["a"]


def test_read_iceberg_dataframe_all_unknown_columns_reads_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No requested column exists → fall back to a full scan so the frame still
    # carries the real columns for the profiler to report the requested ones missing.
    df = pd.DataFrame({"a": [1, 2]})
    _patch_load(monkeypatch, _SchemaTable(df))
    out = read_iceberg_dataframe(_cfg(), None, "ns.t", columns=["ghost"])
    assert list(out.columns) == ["a"]


def test_list_iceberg_columns_returns_schema_names_without_scanning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = _SchemaTable(pd.DataFrame({"id": [1], "amount": [2], "city": ["x"]}))

    def _no_scan(**_: Any) -> Any:
        raise AssertionError("column listing must not scan table data")

    monkeypatch.setattr(table, "scan", _no_scan)  # guard: names come from schema()
    _patch_load(monkeypatch, table)
    cols = list_iceberg_columns(_cfg(), "tok", "sales.orders")
    assert cols == ["id", "amount", "city"]
