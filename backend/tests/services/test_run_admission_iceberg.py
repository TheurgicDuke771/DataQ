"""Iceberg's admission estimate, over a REAL local catalog (#2019, #2146).

Everything the estimator branches on — record counts, the Arrow schema, the sampled Arrow
size of variable-width columns — comes from pyiceberg, so the tables here are written,
planned and read by pyiceberg itself (sqlite `SqlCatalog` + a `file://` warehouse) rather
than faked.
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
from backend.app.datasources.iceberg import ICEBERG_RUN_OVERHEAD_BYTES, snapshot_row_bytes
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
    """High-entropy strings: large on disk and in memory alike."""
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


def _low_cardinality(cat: Any, name: str, rows: int) -> Any:
    """Twelve dictionary-encoded columns of 100-character labels: a few bytes a cell on disk,
    full width once read — the shape both earlier proxies (rows, file bytes) missed."""
    labels = [f"{k:03d}" + "x" * 97 for k in range(20)]
    data = pa.table(
        {
            "id": pa.array(range(rows), pa.int64()),
            **{
                f"label_{i}": pa.array([labels[(r + i) % 20] for r in range(rows)])
                for i in range(12)
            },
        }
    )
    table = cat.create_table(name, schema=data.schema)
    table.append(data)
    return table


def _decoded_bytes(table: Any) -> int:
    """Independent of the estimator: the snapshot's own Arrow size, read in full by pyiceberg."""
    return int(table.scan().to_arrow().nbytes)


def _data_file_bytes(table: Any) -> int:
    """pyiceberg's own `files` metadata table."""
    return sum(table.inspect.data_files().column("file_size_in_bytes").to_pylist())


# ── pricing by Arrow type ──


def test_a_fixed_width_schema_is_priced_without_reading_any_data(
    catalog: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import datetime
    import decimal

    cat, _ = catalog
    data = pa.table(
        {
            "big": pa.array([1], pa.int64()),
            "small": pa.array([1], pa.int32()),
            "flag": pa.array([True]),
            "amount": pa.array([decimal.Decimal("1.50")], pa.decimal128(18, 2)),
            "day": pa.array([datetime.date(2026, 1, 1)]),
            "at": pa.array([datetime.datetime(2026, 1, 1)], pa.timestamp("us")),
        }
    )
    table = cat.create_table("sales.fixed", schema=data.schema)
    table.append(data)
    monkeypatch.setattr(table, "scan", lambda **_kw: pytest.fail("fixed widths read no data"))

    # int64, int32, bool, decimal(18,2), date, timestamp — as pyiceberg's schema reports them.
    assert snapshot_row_bytes(table) == 26 + 14 + 8 + 34 + 14 + 26


def test_variable_width_columns_are_sampled_one_at_a_time_from_at_most_three_files(
    catalog: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pyiceberg scan applies its row limit only after reading whole files — 0.9 GiB to
    sample 1,000 rows of a long-text table on the rig — so the sample never goes through it.
    However many files the snapshot holds, the sample reads three at most (#2221)."""
    import pyarrow.parquet as pq
    from pyiceberg.table import DataScan

    cat, _ = catalog
    table = _low_cardinality(cat, "sales.labels", 3000)
    head = table.scan().to_arrow()
    for rows in (500, 1500, 2500, 100):  # four more data files of different sizes
        table.append(head.slice(0, rows))
    assert len(list(table.scan().plan_files())) == 5
    reads: list[tuple[str, list[str], int]] = []
    real_iter = pq.ParquetFile.iter_batches

    def _spy(self: Any, *args: Any, **kwargs: Any) -> Any:
        reads.append((str(self.metadata), kwargs["columns"], kwargs["batch_size"]))
        return real_iter(self, *args, **kwargs)

    monkeypatch.setattr(pq.ParquetFile, "iter_batches", _spy)
    monkeypatch.setattr(DataScan, "to_arrow", lambda *_a, **_k: pytest.fail("no scan read"))

    snapshot_row_bytes(table)

    files = {file for file, *_ in reads}
    assert 1 < len(files) <= 3
    assert len(reads) == 12 * len(files)
    assert {columns[0] for _, columns, _ in reads} == {f"label_{i}" for i in range(12)}
    assert all(len(columns) == 1 for _, columns, _ in reads)
    assert {size for *_, size in reads} == {iceberg_mod._WIDTH_SAMPLE_ROWS}


def _sampled_row_bytes(table: Any, fixed: int) -> int:
    """Independent of the sampler: the per-row price of `table`'s variable-width columns as
    pyiceberg itself reads their first rows, sized in the schema's own Arrow types."""
    types = {f.name: f.type for f in table.schema().as_arrow()}
    head = table.scan(limit=iceberg_mod._WIDTH_SAMPLE_ROWS).to_arrow()
    return fixed + sum(
        iceberg_mod._VARIABLE_CELL_BYTES
        + int(
            iceberg_mod._VARIABLE_BYTES_FACTOR
            * head.column(name).cast(types[name]).nbytes
            / head.num_rows
        )
        for name in types
        if name != "id"
    )


def test_the_sample_is_sized_in_the_schemas_own_arrow_types(catalog: Any) -> None:
    """The costs were fitted on the Iceberg schema's Arrow types (large offsets), so a file whose
    writer stored plain `string` is sized as the schema's `large_string`, not as written."""
    cat, _ = catalog
    table = _low_cardinality(cat, "sales.labels", 3000)
    head = table.scan(limit=10).to_arrow()
    schema_type = table.schema().as_arrow().field("label_0").type
    assert head.schema.field("label_0").type != schema_type  # the writer's own type differs

    assert snapshot_row_bytes(table) == _sampled_row_bytes(table, fixed=26)


def test_a_renamed_column_is_sampled_by_field_id(catalog: Any) -> None:
    """The data file still carries the old name; only the field id ties it to the schema."""
    cat, _ = catalog
    rows = 3000
    table = _low_cardinality(cat, "sales.labels", rows)
    with table.update_schema() as update:
        for i in range(12):
            update.rename_column(f"label_{i}", f"renamed_{i}")
    table = cat.load_table("sales.labels")

    assert snapshot_row_bytes(table) == _sampled_row_bytes(table, fixed=26)
    assert snapshot_row_bytes(table) * rows >= _decoded_bytes(table)


def test_a_struct_that_gained_a_child_since_the_file_was_written_is_still_sampled(
    catalog: Any,
) -> None:
    """The file's struct lacks the new child; the sample is sized in the schema's struct."""
    import pyarrow.parquet as pq
    from pyiceberg.types import StringType

    cat, _ = catalog
    rows = 50
    data = pa.table({"point": pa.array([{"x": "a" * 60, "y": 2.0}] * rows)})
    table = cat.create_table("sales.points", schema=data.schema)
    table.append(data)
    with table.update_schema() as update:
        update.add_column(("point", "z"), StringType())
    table = cat.load_table("sales.points")
    first = next(iter(table.scan().plan_files())).file.file_path.removeprefix("file://")
    stored = pq.read_table(first).column("point")
    schema_type = table.schema().as_arrow().field("point").type
    assert stored.type.num_fields == 2 and schema_type.num_fields == 3

    assert snapshot_row_bytes(table) == iceberg_mod._VARIABLE_CELL_BYTES + int(
        iceberg_mod._VARIABLE_BYTES_FACTOR * stored.cast(schema_type).nbytes / rows
    )


def test_a_file_written_without_field_ids_is_sampled_by_name(catalog: Any, tmp_path: Any) -> None:
    """`add_files` registers foreign Parquet (no field ids) through a name mapping."""
    import pyarrow.parquet as pq

    cat, _ = catalog
    rows = 3000
    source = _low_cardinality(cat, "sales.source", rows).scan().to_arrow()
    path = tmp_path / "foreign.parquet"
    pq.write_table(
        source.replace_schema_metadata(None).cast(_without_field_ids(source.schema)), path
    )
    table = cat.create_table("sales.foreign", schema=source.schema)
    table.add_files([f"file://{path}"])
    assert pq.ParquetFile(path).schema_arrow.field("label_0").metadata is None

    assert snapshot_row_bytes(table) == _sampled_row_bytes(table, fixed=26)
    assert snapshot_row_bytes(table) * rows >= _decoded_bytes(table)


def _without_field_ids(schema: pa.Schema) -> pa.Schema:
    return pa.schema([pa.field(f.name, f.type, f.nullable) for f in schema])


def test_a_column_the_first_file_lacks_is_priced_from_a_file_that_holds_it(
    catalog: Any, tmp_path: Any
) -> None:
    """The head file holding none of a column says nothing about the other files, and the
    wider file the sample also reads does hold it (#2221)."""
    import pyarrow.parquet as pq

    cat, _ = catalog
    rows = 50
    table = cat.create_table(
        "sales.orders", schema=pa.schema([("id", pa.int64()), ("note", pa.large_string())])
    )
    table.append(
        pa.table(
            {
                "id": pa.array(range(rows), pa.int64()),
                "note": pa.array(["x" * 400] * rows, pa.large_string()),
            }
        )
    )
    lacking = tmp_path / "ids_only.parquet"
    pq.write_table(pa.table({"id": pa.array(range(5), pa.int64())}), lacking)
    table.add_files([f"file://{lacking}"])
    first = next(iter(table.scan().plan_files())).file.file_path
    assert first == f"file://{lacking}"  # the head file has no `note` column

    # 400 characters plus the large_string's 8-byte offset.
    per_cell = iceberg_mod._VARIABLE_CELL_BYTES + int(iceberg_mod._VARIABLE_BYTES_FACTOR * 408)
    assert snapshot_row_bytes(table) == 26 + per_cell


def test_a_column_no_sampled_file_holds_is_priced_at_the_fallback_not_as_empty(
    catalog: Any, tmp_path: Any
) -> None:
    import pyarrow.parquet as pq

    cat, _ = catalog
    table = cat.create_table(
        "sales.orders", schema=pa.schema([("id", pa.int64()), ("note", pa.large_string())])
    )
    lacking = tmp_path / "ids_only.parquet"
    pq.write_table(pa.table({"id": pa.array(range(5), pa.int64())}), lacking)
    table.add_files([f"file://{lacking}"])

    per_cell = iceberg_mod._VARIABLE_CELL_BYTES + int(
        iceberg_mod._VARIABLE_BYTES_FACTOR * iceberg_mod._UNSAMPLED_CELL_ARROW_BYTES
    )
    assert snapshot_row_bytes(table) == 26 + per_cell


def test_with_no_parquet_file_to_sample_a_variable_cell_is_priced_conservatively(
    catalog: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ORC/Avro data files are not sampled: the cell is priced at the documented fallback size,
    never at zero."""
    from pyiceberg.manifest import FileFormat

    cat, _ = catalog
    table = _low_cardinality(cat, "sales.labels", 30)
    real_scan = table.scan

    def _as_orc(**kwargs: Any) -> Any:
        scan = real_scan(**kwargs)
        tasks = list(scan.plan_files())
        for task in tasks:
            task.file[2] = FileFormat.ORC  # `file_format` is read-only; its slot is not
            assert task.file.file_format == FileFormat.ORC
        scan.plan_files = lambda: tasks
        return scan

    monkeypatch.setattr(table, "scan", _as_orc)

    per_cell = iceberg_mod._VARIABLE_CELL_BYTES + int(
        iceberg_mod._VARIABLE_BYTES_FACTOR * iceberg_mod._UNSAMPLED_CELL_ARROW_BYTES
    )
    assert snapshot_row_bytes(table) == 26 + 12 * per_cell


def test_a_failed_sample_read_reserves_the_fallback_rather_than_going_unmetered(
    catalog: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The manifests read fine but the data file does not (a 403 on data files, a timeout).
    Escaping would make `estimate_run_memory` return None — a run admitted with nothing held."""
    import pyarrow.parquet as pq

    cat, properties = catalog
    rows = 30
    _low_cardinality(cat, "sales.labels", rows)

    def _unreadable(*_a: Any, **_k: Any) -> None:
        raise OSError("AWS Error ACCESS_DENIED during GetObject operation")

    monkeypatch.setattr(pq.ParquetFile, "__init__", _unreadable)
    warned: list[tuple[str, dict[str, Any]]] = []

    class _Log:
        def warning(self, event: str, **kw: Any) -> None:
            warned.append((event, kw))

    monkeypatch.setattr(iceberg_mod, "log", _Log(), raising=False)

    estimate = _estimate(properties, "sales.labels")

    per_cell = iceberg_mod._VARIABLE_CELL_BYTES + int(
        iceberg_mod._VARIABLE_BYTES_FACTOR * iceberg_mod._UNSAMPLED_CELL_ARROW_BYTES
    )
    assert estimate == run_admission.MemoryEstimate(
        bytes=ICEBERG_RUN_OVERHEAD_BYTES + rows * (26 + 12 * per_cell),
        basis="iceberg_schema_width",
    )
    assert [(event, kw["table"], kw["exc_info"]) for event, kw in warned] == [
        ("iceberg_width_sample_failed", "sales.labels", True)
    ]


def test_a_failed_manifest_read_still_fails_the_estimate(
    catalog: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the data-file sample is caught: a planning failure keeps today's path (logged by
    `run_admission_estimate_failed`, the run's own read raising the real error)."""
    cat, properties = catalog
    _low_cardinality(cat, "sales.labels", 30)
    real_load = iceberg_mod.load_iceberg_table
    calls = {"n": 0}

    def _load(*args: Any, **kwargs: Any) -> Any:
        table = real_load(*args, **kwargs)
        real_scan = table.scan

        def _scan(**kw: Any) -> Any:
            calls["n"] += 1
            if calls["n"] > 1:  # the row count plans fine; the sampler's own plan does not
                raise OSError("manifest list unreadable")
            return real_scan(**kw)

        table.scan = _scan
        return table

    monkeypatch.setattr(iceberg_mod, "load_iceberg_table", _load)

    assert _estimate(properties, "sales.labels") is None


def test_nested_and_binary_columns_are_priced_from_their_sample(catalog: Any) -> None:
    cat, _ = catalog
    rows = 50
    data = pa.table(
        {
            "tags": pa.array([[1, 2, 3]] * rows, pa.list_(pa.int64())),
            "point": pa.array([{"x": 1, "y": 2.0}] * rows),
            "raw": pa.array([os.urandom(64) for _ in range(rows)], pa.binary()),
        }
    )
    table = cat.create_table("sales.nested", schema=data.schema)
    table.append(data)

    assert snapshot_row_bytes(table) * rows >= _decoded_bytes(table)


def _skewed(cat: Any, name: str, rows: int) -> Any:
    """A file of long high-entropy payloads, then a short-text append (#2221). pyiceberg plans
    the newest manifest first, so the short file is the head the old sample read alone."""
    table = cat.create_table(
        name, schema=pa.schema([("id", pa.int64()), ("payload", pa.large_string())])
    )
    table.append(
        pa.table(
            {
                "id": pa.array(range(rows), pa.int64()),
                "payload": pa.array(
                    [os.urandom(1000).hex() for _ in range(rows)], pa.large_string()
                ),
            }
        )
    )
    table.append(
        pa.table(
            {
                "id": pa.array(range(rows, 2 * rows), pa.int64()),
                "payload": pa.array([f"s{r}" for r in range(rows)], pa.large_string()),
            }
        )
    )
    return table


def test_long_text_outside_the_head_file_is_not_priced_from_the_short_head(catalog: Any) -> None:
    """#2221: the head file alone priced this table at a sliver of its decoded size."""
    import pyarrow.parquet as pq

    cat, properties = catalog
    rows = 1500
    table = _skewed(cat, "sales.skewed", rows)
    first = next(iter(table.scan().plan_files())).file.file_path.removeprefix("file://")
    assert pq.read_table(first).column("payload")[0].as_py().startswith("s")  # head is short

    estimate = _estimate(properties, "sales.skewed")

    assert estimate is not None
    assert estimate.bytes - ICEBERG_RUN_OVERHEAD_BYTES >= _decoded_bytes(table)


def test_one_unreadable_sampled_file_still_prices_at_least_the_fallback(
    catalog: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that fails to read may hold the widest cells, so the readable ones cannot
    price the column below the fallback on their own."""
    import pyarrow.parquet as pq

    cat, _ = catalog
    table = cat.create_table(
        "sales.orders", schema=pa.schema([("id", pa.int64()), ("note", pa.large_string())])
    )
    for rows in (10, 20):
        table.append(
            pa.table(
                {
                    "id": pa.array(range(rows), pa.int64()),
                    "note": pa.array(["ab"] * rows, pa.large_string()),
                }
            )
        )
    real_init = pq.ParquetFile.__init__
    opened: list[int] = []

    def _second_unreadable(self: Any, *args: Any, **kwargs: Any) -> None:
        opened.append(1)
        if len(opened) == 2:
            raise OSError("AWS Error ACCESS_DENIED during GetObject operation")
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(pq.ParquetFile, "__init__", _second_unreadable)

    per_cell = iceberg_mod._VARIABLE_CELL_BYTES + int(
        iceberg_mod._VARIABLE_BYTES_FACTOR * iceberg_mod._UNSAMPLED_CELL_ARROW_BYTES
    )
    assert snapshot_row_bytes(table) == 26 + per_cell
    assert len(opened) == 2


# ── the estimate ──


def test_an_iceberg_run_reserves_its_rows_priced_by_schema_plus_the_overhead(
    catalog: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    cat, properties = catalog
    table = _narrow(cat, "sales.orders", 50)
    logged: list[str] = []
    monkeypatch.setattr(run_admission.log, "info", lambda event, **_kw: logged.append(event))

    estimate = _estimate(properties, "sales.orders")

    assert estimate is not None
    assert estimate.basis == "iceberg_schema_width"
    assert estimate.bytes == ICEBERG_RUN_OVERHEAD_BYTES + 50 * snapshot_row_bytes(table)
    assert estimate.exclusive is False
    assert "run_admission_no_estimator" not in logged


def test_a_narrow_numeric_row_is_not_priced_at_the_flat_row_factor(
    catalog: Any,
) -> None:
    """The flat 1 KiB a row over-reserved a narrow numeric table 3-4.5x (#2146)."""
    cat, _ = catalog
    table = _narrow(cat, "sales.orders", 5)

    assert snapshot_row_bytes(table) < get_settings().run_admission_row_bytes


def test_dictionary_encoded_strings_reserve_at_least_their_decoded_size(
    catalog: Any,
) -> None:
    """Small on disk, full width in memory: neither rows x 1 KiB nor file bytes x 9 covered it."""
    cat, properties = catalog
    rows = 3000
    table = _low_cardinality(cat, "sales.labels", rows)
    settings = get_settings()
    decoded = _decoded_bytes(table)
    assert rows * settings.run_admission_row_bytes < decoded
    assert _data_file_bytes(table) * settings.run_admission_expansion_parquet < decoded

    estimate = _estimate(properties, "sales.labels")

    assert estimate is not None
    assert estimate.bytes - ICEBERG_RUN_OVERHEAD_BYTES >= decoded


def test_high_entropy_strings_reserve_at_least_their_decoded_size(catalog: Any) -> None:
    cat, properties = catalog
    table = _wide(cat, "sales.wide", 200)

    estimate = _estimate(properties, "sales.wide")

    assert estimate is not None
    assert estimate.bytes - ICEBERG_RUN_OVERHEAD_BYTES >= _decoded_bytes(table)


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
    table = _narrow(cat, "sales.orders", 50)

    estimate = _estimate(properties, "sales.orders")

    assert estimate is not None
    assert estimate.bytes == ICEBERG_RUN_OVERHEAD_BYTES + 50 * snapshot_row_bytes(table)


def test_a_disabled_cap_reserves_the_whole_table(
    catalog: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RUN_MAX_SCAN_ROWS_ICEBERG", "0")
    get_settings.cache_clear()
    cat, properties = catalog
    table = _narrow(cat, "sales.orders", 50)

    estimate = _estimate(properties, "sales.orders")

    assert estimate is not None
    assert estimate.bytes == ICEBERG_RUN_OVERHEAD_BYTES + 50 * snapshot_row_bytes(table)


def test_an_empty_table_reserves_nothing_rather_than_reading_as_unmetered(catalog: Any) -> None:
    """A snapshot-less table reads nothing, so it is not charged the read's overhead either."""
    cat, properties = catalog
    _narrow(cat, "sales.empty", 0)

    estimate = _estimate(properties, "sales.empty")

    assert estimate == run_admission.MemoryEstimate(bytes=0, basis="iceberg_empty")


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
    table = _narrow(cat, "sales.orders", 50)
    _with_summary(monkeypatch, **overrides)
    logged: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(run_admission.log, "info", lambda event, **kw: logged.append((event, kw)))

    estimate = _estimate(properties, "sales.orders", kinds=("volume",))

    assert estimate is not None
    assert estimate.basis == "iceberg_monitor_fallback_schema_width"
    assert estimate.bytes == ICEBERG_RUN_OVERHEAD_BYTES + 50 * snapshot_row_bytes(table)
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
    table = _narrow(cat, "sales.orders", 50)
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
    assert reserved == [(str(run.id), ICEBERG_RUN_OVERHEAD_BYTES + 50 * snapshot_row_bytes(table))]
