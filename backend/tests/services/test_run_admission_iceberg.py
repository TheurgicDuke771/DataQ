"""Iceberg's admission estimate, over a REAL local catalog (#2019).

Everything the estimator branches on — record counts, file sizes, the file-format enum —
comes from pyiceberg's manifest plan, so the tables here are written and planned by
pyiceberg itself (sqlite `SqlCatalog` + a `file://` warehouse) rather than faked.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, cast

import pyarrow as pa
import pytest
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.datasources import iceberg as iceberg_mod
from backend.app.datasources.iceberg import PlannedScan, planned_scan
from backend.app.db.models import Check, Connection, Run, Suite
from backend.app.services import run_admission


class _FakeSession:
    def __init__(self, suite: Suite, connection: Connection, checks: list[Check]) -> None:
        self._objs: dict[type, Any] = {Suite: suite, Connection: connection}
        self._checks = checks

    def get(self, model: type, _pk: Any) -> Any:
        return self._objs.get(model)

    def scalars(self, _stmt: Any) -> Any:
        return iter(self._checks)


@pytest.fixture
def catalog(tmp_path: Any) -> tuple[Any, dict[str, Any]]:
    from pyiceberg.catalog.sql import SqlCatalog

    warehouse = tmp_path / "warehouse"
    warehouse.mkdir()
    properties = {
        "catalog_name": "local",
        "catalog_type": "sql",
        "catalog_uri": f"sqlite:///{tmp_path}/catalog.db",
        "warehouse": f"file://{warehouse}",
    }
    cat = SqlCatalog("local", uri=properties["catalog_uri"], warehouse=properties["warehouse"])
    cat.create_namespace("sales")
    return cat, properties


class _NoSecrets:
    def get(self, ref: str) -> str:
        raise AssertionError(f"a credential-less connection must not read the store ({ref})")


@pytest.fixture(autouse=True)
def _no_secret_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_admission, "get_secret_store", _NoSecrets)


def _estimate(
    properties: dict[str, Any], table: str, *, kinds: tuple[str, ...] = ("expectation",)
) -> run_admission.MemoryEstimate | None:
    suite_id, conn_id, user_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    namespace, name = table.split(".")
    suite = Suite(
        id=suite_id,
        name="s",
        connection_id=conn_id,
        created_by=user_id,
        target={"namespace": namespace, "table": name},
    )
    connection = Connection(
        id=conn_id,
        name="c",
        type="iceberg",
        env="dev",
        config=properties,
        secret_ref=None,
        created_by=user_id,
    )
    checks = [
        Check(
            id=uuid.uuid4(),
            suite_id=suite_id,
            name=f"c{i}",
            kind=kind,
            expectation_type="expect_column_values_to_not_be_null",
            config={"column": "id"},
        )
        for i, kind in enumerate(kinds)
    ]
    run = Run(id=uuid.uuid4(), suite_id=suite_id, status="queued")
    return run_admission.estimate_run_memory(
        cast(Session, _FakeSession(suite, connection, checks)), run
    )


def _narrow(cat: Any, name: str, rows: int) -> Any:
    table = cat.create_table(name, schema=pa.schema([("id", pa.int64())]))
    if rows:
        table.append(pa.table({"id": pa.array(range(rows), pa.int64())}))
    return table


def _wide(cat: Any, name: str, rows: int) -> Any:
    """High-entropy strings: ~1.2 KB/row even after Parquet compression."""
    data = pa.table(
        {
            "id": pa.array(range(rows), pa.int64()),
            **{
                f"blob_{i}": pa.array([os.urandom(150).hex() for _ in range(rows)])
                for i in range(4)
            },
        }
    )
    table = cat.create_table(name, schema=data.schema)
    table.append(data)
    return table


def _data_file_bytes(table: Any) -> int:
    """Independent of `planned_scan`: pyiceberg's own `files` metadata table."""
    return sum(table.inspect.data_files().column("file_size_in_bytes").to_pylist())


# ── the probe ──


def test_planned_scan_totals_a_real_tables_files_by_format(catalog: Any) -> None:
    cat, _ = catalog
    table = _narrow(cat, "sales.orders", 3)
    table.append(pa.table({"id": pa.array([9, 10], pa.int64())}))  # a second data file

    scan = planned_scan(table)

    assert scan.rows == 5 and isinstance(scan.rows, int)
    assert scan.bytes_by_format == {"parquet": _data_file_bytes(table)}
    assert iceberg_mod.planned_row_count(table) == 5


def test_planned_scan_of_a_snapshot_less_table_is_empty(catalog: Any) -> None:
    cat, _ = catalog
    assert planned_scan(_narrow(cat, "sales.empty", 0)) == PlannedScan(rows=0, bytes_by_format={})


# ── the estimate ──


def test_an_iceberg_run_reserves_its_planned_rows(
    catalog: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    cat, properties = catalog
    _narrow(cat, "sales.orders", 50)
    logged: list[str] = []
    monkeypatch.setattr(run_admission.log, "info", lambda event, **_kw: logged.append(event))

    estimate = _estimate(properties, "sales.orders")

    assert estimate is not None
    assert estimate.basis == "iceberg_planned_rows"
    assert estimate.bytes == 50 * get_settings().run_admission_row_bytes
    assert estimate.exclusive is False
    assert "run_admission_no_estimator" not in logged


def test_a_wide_table_reserves_by_its_file_bytes_not_its_row_count(catalog: Any) -> None:
    """Rows alone are width-blind: here the files say far more than rows x row_bytes does."""
    cat, properties = catalog
    table = _wide(cat, "sales.wide", 200)
    settings = get_settings()

    estimate = _estimate(properties, "sales.wide")

    assert estimate is not None
    assert estimate.basis == "iceberg_file_bytes"
    expected = int(_data_file_bytes(table) * settings.run_admission_expansion_parquet)
    assert estimate.bytes == expected
    assert estimate.bytes > 200 * settings.run_admission_row_bytes


def test_an_over_cap_table_reserves_nothing(catalog: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The row-cap probe refuses it before anything is read, so the run holds nothing.
    Reserving even the cap's worth (3M rows x 1 KiB at the defaults, ~3x the whole budget)
    would park a run that is certain to be refused until the worker drains.
    """
    monkeypatch.setenv("RUN_MAX_SCAN_ROWS_ICEBERG", "20")
    get_settings.cache_clear()
    cat, properties = catalog
    _narrow(cat, "sales.orders", 50)

    estimate = _estimate(properties, "sales.orders")

    assert estimate == run_admission.MemoryEstimate(bytes=0, basis="iceberg_over_cap")


def test_a_table_at_the_cap_is_still_reserved(
    catalog: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RUN_MAX_SCAN_ROWS_ICEBERG", "50")
    get_settings.cache_clear()
    cat, properties = catalog
    _narrow(cat, "sales.orders", 50)

    estimate = _estimate(properties, "sales.orders")

    assert estimate is not None
    assert estimate.bytes == 50 * get_settings().run_admission_row_bytes


def test_a_disabled_cap_reserves_the_whole_table(
    catalog: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RUN_MAX_SCAN_ROWS_ICEBERG", "0")
    get_settings.cache_clear()
    cat, properties = catalog
    _narrow(cat, "sales.orders", 50)

    estimate = _estimate(properties, "sales.orders")

    assert estimate is not None
    assert estimate.bytes == 50 * get_settings().run_admission_row_bytes


def test_an_empty_table_reserves_nothing_rather_than_reading_as_unmetered(catalog: Any) -> None:
    cat, properties = catalog
    _narrow(cat, "sales.empty", 0)

    estimate = _estimate(properties, "sales.empty")

    assert estimate is not None and estimate.bytes == 0


def test_a_monitor_only_suite_on_clean_metadata_reserves_nothing(
    catalog: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    cat, properties = catalog
    _narrow(cat, "sales.orders", 50)
    logged: list[str] = []
    monkeypatch.setattr(run_admission.log, "info", lambda event, **_kw: logged.append(event))

    assert _estimate(properties, "sales.orders", kinds=("volume", "freshness")) is None
    assert "run_admission_iceberg_metadata_only" in logged


def _with_summary(monkeypatch: pytest.MonkeyPatch, **overrides: str | None) -> None:
    """Serve the real table with its real snapshot summary edited — row-level deletes cannot
    be written by pyiceberg itself (it falls back to copy-on-write)."""
    from pyiceberg.table.snapshots import Summary

    real = iceberg_mod.load_iceberg_table

    def _load(*args: Any, **kwargs: Any) -> Any:
        table = real(*args, **kwargs)
        snapshot = table.current_snapshot()
        props = {**snapshot.summary.additional_properties, **overrides}
        summary = Summary(
            snapshot.summary.operation, **{k: v for k, v in props.items() if v is not None}
        )
        edited = snapshot.model_copy(update={"summary": summary})
        table.current_snapshot = lambda: edited
        return table

    monkeypatch.setattr(iceberg_mod, "load_iceberg_table", _load)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"total-position-deletes": "3"}, "row-level deletes present"),
        ({"total-equality-deletes": None}, "omits total-equality-deletes"),
        ({"total-records": None}, "lacks total-records"),
    ],
)
def test_a_monitor_only_suite_that_must_scan_is_reserved_for(
    catalog: Any, monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any], reason: str
) -> None:
    """Merge-on-read (or a writer that omits the delete totals) sends the volume monitor to
    `scan().count()`, which materialises the tasks — not a metadata answer."""
    cat, properties = catalog
    _narrow(cat, "sales.orders", 50)
    _with_summary(monkeypatch, **overrides)
    logged: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(run_admission.log, "info", lambda event, **kw: logged.append((event, kw)))

    estimate = _estimate(properties, "sales.orders", kinds=("volume",))

    assert estimate is not None
    assert estimate.basis == "iceberg_monitor_fallback_planned_rows"
    assert estimate.bytes == 50 * get_settings().run_admission_row_bytes
    fallback = [kw for event, kw in logged if event == "run_admission_iceberg_monitor_fallback"]
    assert len(fallback) == 1 and reason in fallback[0]["reason"]


def test_a_suite_with_no_materialising_check_never_probes(
    catalog: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, properties = catalog
    monkeypatch.setattr(
        iceberg_mod, "load_iceberg_table", lambda *_a, **_k: pytest.fail("no probe needed")
    )

    assert _estimate(properties, "sales.orders", kinds=("schema_drift",)) is None


def test_summary_scan_fallback_reason_reads_a_real_snapshot(
    catalog: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    cat, _ = catalog
    assert iceberg_mod.summary_scan_fallback_reason(_narrow(cat, "sales.orders", 5)) is None
    assert iceberg_mod.summary_scan_fallback_reason(_narrow(cat, "sales.empty", 0)) is None


def test_a_missing_table_never_fails_the_run(catalog: Any) -> None:
    """The read path raises its own classified error moments later."""
    _, properties = catalog

    assert _estimate(properties, "sales.nope") is None


def test_a_non_parquet_file_uses_the_default_expansion(
    catalog: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The two factors are equal by default, which would hide a mix-up.
    monkeypatch.setenv("RUN_ADMISSION_EXPANSION_DEFAULT", "3.0")
    get_settings.cache_clear()
    cat, properties = catalog
    _narrow(cat, "sales.orders", 1)
    monkeypatch.setattr(
        iceberg_mod,
        "planned_scan",
        lambda _t: PlannedScan(rows=1, bytes_by_format={"orc": 1_000_000, "parquet": 1_000_000}),
    )
    settings = get_settings()

    estimate = _estimate(properties, "sales.orders")

    assert estimate is not None
    assert estimate.basis == "iceberg_file_bytes"
    assert estimate.bytes == int(
        1_000_000 * settings.run_admission_expansion_default
        + 1_000_000 * settings.run_admission_expansion_parquet
    )


def test_the_stored_credential_is_what_opens_the_catalog(
    catalog: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    cat, properties = catalog
    _narrow(cat, "sales.orders", 5)
    seen: list[Any] = []
    real = iceberg_mod.load_iceberg_table

    def _spy(config: Any, secret: Any, identifier: str, catalog_secret: Any = None) -> Any:
        seen.append((secret, identifier))
        return real(config, None, identifier, catalog_secret)

    class _Store:
        def get(self, ref: str) -> str:
            return f"secret-for-{ref}"

    monkeypatch.setattr(iceberg_mod, "load_iceberg_table", _spy)
    monkeypatch.setattr(run_admission, "get_secret_store", _Store)
    suite_id, conn_id, user_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    session = _FakeSession(
        Suite(
            id=suite_id,
            name="s",
            connection_id=conn_id,
            created_by=user_id,
            target={"namespace": "sales", "table": "orders"},
        ),
        Connection(
            id=conn_id,
            name="c",
            type="iceberg",
            env="dev",
            config=properties,
            secret_ref="ice-ref",
            created_by=user_id,
        ),
        [
            Check(
                id=uuid.uuid4(),
                suite_id=suite_id,
                name="c",
                kind="expectation",
                expectation_type="expect_column_values_to_not_be_null",
                config={"column": "id"},
            )
        ],
    )

    estimate = run_admission.estimate_run_memory(
        cast(Session, session), Run(id=uuid.uuid4(), suite_id=suite_id, status="queued")
    )

    assert estimate is not None and estimate.bytes > 0
    assert seen == [("secret-for-ice-ref", "sales.orders")]


def test_the_iceberg_estimate_is_what_admission_reserves(
    catalog: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.app.core.memory_budget import Admission

    cat, properties = catalog
    _narrow(cat, "sales.orders", 50)
    reserved: list[tuple[str, int]] = []

    class _Budget:
        budget_bytes = 1 << 30

        def reserve(self, key: str, amount: int) -> Admission:
            reserved.append((key, amount))
            return Admission(admitted=True, used_bytes=amount, degraded=False)

    monkeypatch.setattr(run_admission, "get_memory_budget", _Budget)
    estimate = _estimate(properties, "sales.orders")
    run = Run(id=uuid.uuid4(), suite_id=uuid.uuid4(), status="queued")

    decision = run_admission.admit(
        cast(Session, None), run=run, estimate=estimate, waited_out=False
    )

    assert decision.defer is False
    assert reserved == [(str(run.id), 50 * get_settings().run_admission_row_bytes)]
