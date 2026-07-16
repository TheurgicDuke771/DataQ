"""Iceberg connection adapter + native read runner tests (ADR 0030, #716).

No live catalog: ``pyiceberg.catalog.load_catalog`` and the runner's
``_load_table`` seam are monkeypatched with fakes whose ``scan()`` returns a real
``pyarrow`` table built from a canned frame — so GX (run_checks) and the pure
monitor banding (run_monitors) run for real over the materialised data, while the
catalog/scan I/O is faked. The adapter is DB-free, so these are pure unit tests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pandas as pd
import pyarrow as pa
import pytest
from pydantic import ValidationError

from backend.app.core.secrets import SecretStore
from backend.app.datasources import iceberg as iceberg_mod
from backend.app.datasources.base import CheckSpec, MonitorSpec
from backend.app.datasources.iceberg import (
    IcebergCheckRunner,
    IcebergConfig,
    IcebergConnectionAdapter,
    build_iceberg_runner,
    iceberg_credentials,
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
    def __init__(self, table: pa.Table, files: list[Any] | None = None, owner: Any = None) -> None:
        self._table = table
        self._files = files or []
        self._owner = owner

    def _count_data_call(self) -> None:
        if self._owner is not None:
            self._owner.scan_calls += 1

    def to_arrow(self) -> pa.Table:
        self._count_data_call()
        return self._table

    def count(self) -> int:
        self._count_data_call()
        return int(self._table.num_rows)

    def plan_files(self) -> list[Any]:
        return self._files  # metadata-only — deliberately NOT a data call


class _FakeTable:
    """Stands in for a ``pyiceberg`` Table — ``scan()`` returns a real Arrow table
    (optionally projected to ``selected_fields``) so the runner's materialisation
    + GX + monitor math run for real. ``snapshot``/``schema_``/``files`` model the
    metadata surface the #859 fast-paths read; the default (no snapshot) routes
    monitors down the scan fallback, like a table with no metadata to trust."""

    def __init__(
        self,
        df: pd.DataFrame,
        *,
        snapshot: Any | None = None,
        schema: Any | None = None,
        files: list[Any] | None = None,
    ) -> None:
        self._arrow = pa.Table.from_pandas(df, preserve_index=False)
        self._snapshot = snapshot
        self._schema = schema
        self._files = files or []
        self.scan_calls = 0  # data-path invocations — the fast-path tests pin 0

    def current_snapshot(self) -> Any | None:
        return self._snapshot

    def schema(self) -> Any:
        if self._schema is None:
            raise NotImplementedError("fake has no schema")
        return self._schema

    def scan(self, *, selected_fields: tuple[str, ...] | None = None) -> _FakeScan:
        scan = _FakeScan(
            self._arrow.select(list(selected_fields)) if selected_fields else self._arrow,
            files=self._files,
            owner=self,
        )
        return scan


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
    assert outcome.observed_value == {
        "row_count": 50,
        "deviation_pct": 0.0,
        # the default fake has no snapshot → the run says WHICH path answered (#859)
        "source": "scan-fallback",
        "fallback_reason": "table has no current snapshot",
    }


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
    monkeypatch.setattr(
        iceberg_mod,
        "load_iceberg_table",
        lambda config, secret, identifier, catalog_secret=None: table,
    )


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


def test_run_checks_null_sample_payload_json_serializes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live-crash regression (#751): a null cell in an Arrow-backed frame reaches the
    failing-check sample payload as ``pd.NA``, and result persistence JSON-serializes
    that payload — the full outcome must round-trip through ``sanitize_json``."""
    import json

    from backend.app.core.jsonsafe import sanitize_json

    df = pd.DataFrame({"supplier_id": ["SUP-0001", None, "SUP-0002"], "qty": [1, 2, 3]})
    runner = _runner_over(df, monkeypatch)
    outcome = runner.run_checks(
        table="retail.purchase_orders",
        schema=None,
        checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "supplier_id"})],
    )
    assert outcome.success is False
    for check in outcome.checks:
        json.dumps(sanitize_json(check.observed_value), allow_nan=False)
        json.dumps(sanitize_json(check.sample_failures), allow_nan=False)


def test_run_checks_timestamp_sample_payload_json_serializes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#751-review sibling gap: a failing NON-null value on an Arrow-backed timestamp
    column reaches the sample payload as ``pd.Timestamp`` — that must also survive
    ``sanitize_json`` → ``json.dumps``, not just the null-sentinel case."""
    import json

    from backend.app.core.jsonsafe import sanitize_json

    df = pd.DataFrame({"ordered_at": pd.to_datetime(["2026-07-01", "2099-01-01"])})
    runner = _runner_over(df, monkeypatch)
    outcome = runner.run_checks(
        table="retail.purchase_orders",
        schema=None,
        checks=[
            CheckSpec(
                "expect_column_values_to_be_between",
                {"column": "ordered_at", "max_value": "2027-01-01"},
            )
        ],
    )
    assert outcome.success is False
    for check in outcome.checks:
        json.dumps(sanitize_json(check.observed_value), allow_nan=False)
        json.dumps(sanitize_json(check.sample_failures), allow_nan=False)
        json.dumps(sanitize_json(check.expected_value), allow_nan=False)


# ── the catalog credential must live in the SecretStore, not in config (#754/#826) ──


class TestCatalogCredential:
    def test_a_password_in_catalog_uri_is_rejected_outright(self) -> None:
        """`config` is stored and returned in plaintext AND becomes the asset's lineage
        identity — so refuse the credential at the door rather than redacting forever."""
        with pytest.raises(ValidationError) as exc:
            IcebergConfig(
                catalog_type="sql",
                catalog_uri="postgresql+psycopg2://u:s3cr3t@h:5432/cat",
            )
        assert "catalog_secret_name" in str(exc.value)

    def test_a_credential_less_uri_with_a_username_is_fine(self) -> None:
        cfg = IcebergConfig(
            catalog_type="sql",
            catalog_uri="postgresql+psycopg2://u@h:5432/cat?sslmode=require",
            catalog_secret_name="kv-iceberg-catalog",
        )
        assert cfg.catalog_secret_name == "kv-iceberg-catalog"

    def test_the_password_is_attached_only_at_catalog_load(self) -> None:
        cfg = IcebergConfig(
            catalog_type="sql",
            catalog_uri="postgresql+psycopg2://u@h:5432/cat",
            catalog_secret_name="kv-cat",
        )
        # At rest: no credential anywhere in the config.
        assert "s3cr3t" not in cfg.model_dump_json()
        # At load: the resolved secret is injected into the URI pyiceberg needs.
        props = cfg.catalog_properties(None, "s3cr3t")
        assert props["uri"] == "postgresql+psycopg2://u:s3cr3t@h:5432/cat"
        # …and without the secret it stays credential-less rather than half-built.
        assert cfg.catalog_properties(None)["uri"] == "postgresql+psycopg2://u@h:5432/cat"

    def test_a_hostile_password_cannot_repoint_the_uri(self) -> None:
        cfg = IcebergConfig(catalog_type="sql", catalog_uri="postgresql://u@real-host:5432/cat")
        uri = cfg.catalog_properties(None, "pw@evil-host/")
        assert "@real-host:5432/cat" in uri["uri"]
        assert "evil-host" not in uri["uri"].split("@")[-1]


class TestEveryReadPathGetsTheCatalogCredential:
    """Regression: `catalog_uri` is credential-less now, so ANY read path that resolves
    only the storage secret would connect to the catalog with no password (#754/#826).
    The runner path was wired first and the profiler/comparison paths were NOT — this
    pins that every one of them goes through `iceberg_credentials`."""

    class _Store:
        def get(self, ref: str) -> str:
            return {"kv-storage": "STORAGE_KEY", "kv-catalog": "CATALOG_PW"}[ref]

    def _cfg(self) -> IcebergConfig:
        return IcebergConfig(
            catalog_type="sql",
            catalog_uri="postgresql+psycopg2://u@h:5432/cat",
            catalog_secret_name="kv-catalog",
            secret_property="adls.account-key",
        )

    def test_resolves_both_credentials(self) -> None:
        secret, catalog_secret = iceberg_credentials(
            self._cfg(), "kv-storage", cast(SecretStore, self._Store())
        )
        assert secret == "STORAGE_KEY"
        assert catalog_secret == "CATALOG_PW"

    def test_both_are_optional(self) -> None:
        cfg = IcebergConfig(catalog_type="rest", catalog_uri="https://cat.example")
        assert iceberg_credentials(cfg, None, cast(SecretStore, self._Store())) == (None, None)

    def test_the_runner_actually_reaches_the_catalog_with_the_password(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def _fake_load_catalog(name: str, **props: Any) -> Any:
            seen.update(props)
            raise RuntimeError("stop here — we only care about the props")

        monkeypatch.setattr("pyiceberg.catalog.load_catalog", _fake_load_catalog)
        runner = build_iceberg_runner(
            config=self._cfg().model_dump(),
            secret_ref="kv-storage",
            secret_store=cast(SecretStore, self._Store()),
        )
        with pytest.raises(RuntimeError):
            runner._load_table("retail.orders")

        # The DSN handed to pyiceberg carries the catalog password…
        assert seen["uri"] == "postgresql+psycopg2://u:CATALOG_PW@h:5432/cat"
        # …and the storage key still lands on its own property.
        assert seen["adls.account-key"] == "STORAGE_KEY"


# ───────────────────── metadata fast-paths (#859) ─────────────────────


class _FakeSnapshot:
    def __init__(self, summary: dict[str, str] | None) -> None:
        self.summary = summary


def _bounds_file(field_id: int, raw: bytes | None) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(file=SimpleNamespace(upper_bounds={field_id: raw} if raw else {}))


def test_volume_answers_from_snapshot_summary_without_scanning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The DataFrame deliberately DISAGREES with the summary (1 row vs 50) — the
    # metadata must win, proving no scan happened; scan_calls pins it to zero.
    snapshot = _FakeSnapshot({"total-records": "50", "added-records": "7", "deleted-records": "2"})
    fake = _FakeTable(pd.DataFrame({"id": [1]}), snapshot=snapshot)
    runner = IcebergCheckRunner(config=IcebergConfig.model_validate(_REST_CONFIG), secret="tok")
    monkeypatch.setattr(runner, "_load_table", lambda identifier: fake)
    [outcome] = runner.run_monitors(
        table="sales.orders",
        schema=None,
        monitors=[MonitorSpec("volume", {"min_rows": 10, "max_rows": 100})],
    )
    assert outcome.success is True
    assert outcome.observed_value == {
        "row_count": 50,
        "deviation_pct": 0.0,
        "source": "snapshot-summary",
        "added_records": 7,  # the per-commit delta a row-count scan can never give
        "deleted_records": 2,
    }
    assert fake.scan_calls == 0  # metadata-only — no data path touched


def test_volume_falls_back_to_scan_when_summary_lacks_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Summary fields are engine-written and OPTIONAL — degrade honestly (#828).
    fake = _FakeTable(pd.DataFrame({"id": [1, 2, 3]}), snapshot=_FakeSnapshot({"op": "append"}))
    runner = IcebergCheckRunner(config=IcebergConfig.model_validate(_REST_CONFIG), secret="tok")
    monkeypatch.setattr(runner, "_load_table", lambda identifier: fake)
    [outcome] = runner.run_monitors(
        table="sales.orders",
        schema=None,
        monitors=[MonitorSpec("volume", {"min_rows": 1, "max_rows": 100})],
    )
    assert outcome.observed_value is not None
    assert outcome.observed_value["row_count"] == 3
    assert outcome.observed_value["source"] == "scan-fallback"
    assert outcome.observed_value["fallback_reason"] == "snapshot summary lacks total-records"
    assert fake.scan_calls == 1


def _tz_field_schema() -> tuple[Any, int]:
    from pyiceberg.schema import Schema
    from pyiceberg.types import NestedField, TimestamptzType

    return Schema(NestedField(1, "loaded_at", TimestamptzType())), 1


def test_freshness_answers_from_file_bounds_without_scanning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pyiceberg.conversions import to_bytes
    from pyiceberg.types import TimestamptzType

    schema, field_id = _tz_field_schema()
    five_hours_ago = datetime.now(UTC) - timedelta(hours=5)
    stale = five_hours_ago - timedelta(hours=20)
    files = [
        _bounds_file(field_id, to_bytes(TimestamptzType(), int(stale.timestamp() * 1_000_000))),
        _bounds_file(
            field_id, to_bytes(TimestamptzType(), int(five_hours_ago.timestamp() * 1_000_000))
        ),
    ]
    # The DataFrame holds a FRESHER row than any bound — metadata must win.
    fake = _FakeTable(
        pd.DataFrame({"loaded_at": [datetime.now(UTC)]}),
        snapshot=_FakeSnapshot({"total-delete-files": "0"}),
        schema=schema,
        files=files,
    )
    runner = IcebergCheckRunner(config=IcebergConfig.model_validate(_REST_CONFIG), secret="tok")
    monkeypatch.setattr(runner, "_load_table", lambda identifier: fake)
    [outcome] = runner.run_monitors(
        table="sales.orders",
        schema=None,
        monitors=[MonitorSpec("freshness", {"column": "loaded_at"})],
    )
    assert outcome.metric_value == pytest.approx(5.0, abs=0.1)  # max bound across files
    assert outcome.observed_value is not None
    assert outcome.observed_value["source"] == "file-bounds"
    assert fake.scan_calls == 0


def test_freshness_falls_back_when_row_level_deletes_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A deleted row may hold the max → the bound would over-report freshness
    # (false-green). Must scan instead, and say why.
    schema, field_id = _tz_field_schema()
    recent = datetime.now(UTC) - timedelta(hours=2)
    fake = _FakeTable(
        pd.DataFrame({"loaded_at": [recent]}),
        snapshot=_FakeSnapshot({"total-delete-files": "3"}),
        schema=schema,
        files=[_bounds_file(field_id, None)],
    )
    runner = IcebergCheckRunner(config=IcebergConfig.model_validate(_REST_CONFIG), secret="tok")
    monkeypatch.setattr(runner, "_load_table", lambda identifier: fake)
    [outcome] = runner.run_monitors(
        table="sales.orders",
        schema=None,
        monitors=[MonitorSpec("freshness", {"column": "loaded_at"})],
    )
    assert outcome.metric_value == pytest.approx(2.0, abs=0.1)  # from the scan, not bounds
    assert outcome.observed_value is not None
    assert outcome.observed_value["source"] == "scan-fallback"
    assert "total-delete-files=3" in outcome.observed_value["fallback_reason"]
    assert fake.scan_calls == 1


def test_freshness_falls_back_when_a_file_lacks_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema, field_id = _tz_field_schema()
    recent = datetime.now(UTC) - timedelta(hours=1)
    fake = _FakeTable(
        pd.DataFrame({"loaded_at": [recent]}),
        snapshot=_FakeSnapshot({"total-delete-files": "0"}),
        schema=schema,
        files=[_bounds_file(field_id, None)],  # stats disabled on this file
    )
    runner = IcebergCheckRunner(config=IcebergConfig.model_validate(_REST_CONFIG), secret="tok")
    monkeypatch.setattr(runner, "_load_table", lambda identifier: fake)
    [outcome] = runner.run_monitors(
        table="sales.orders",
        schema=None,
        monitors=[MonitorSpec("freshness", {"column": "loaded_at"})],
    )
    assert outcome.observed_value is not None
    assert outcome.observed_value["source"] == "scan-fallback"
    assert outcome.observed_value["fallback_reason"] == (
        "a data file lacks an upper bound for the column"
    )


def test_freshness_empty_snapshot_is_operational_error_without_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A snapshot with zero live data files is AUTHORITATIVELY empty — same "no
    # rows" operational error as the scan path, but metadata-only.
    schema, _ = _tz_field_schema()
    fake = _FakeTable(
        pd.DataFrame({"loaded_at": pd.Series([], dtype="datetime64[ns, UTC]")}),
        snapshot=_FakeSnapshot({"total-delete-files": "0"}),
        schema=schema,
        files=[],
    )
    runner = IcebergCheckRunner(config=IcebergConfig.model_validate(_REST_CONFIG), secret="tok")
    monkeypatch.setattr(runner, "_load_table", lambda identifier: fake)
    [outcome] = runner.run_monitors(
        table="sales.orders",
        schema=None,
        monitors=[MonitorSpec("freshness", {"column": "loaded_at"})],
    )
    assert outcome.errored is True  # can't assess freshness with no rows (#122)
    assert fake.scan_calls == 0
