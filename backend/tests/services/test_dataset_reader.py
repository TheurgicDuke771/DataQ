"""DatasetReader seam tests (ADR 0015, #792) — no live datasources."""

import re
import uuid
from contextlib import contextmanager
from typing import Any

import pandas as pd
import pytest
from sqlalchemy.engine.default import DefaultDialect

from backend.app.datasources import flatfile
from backend.app.db.models import Connection
from backend.app.services import dataset_reader
from backend.app.services.custom_sql import CustomSqlInvalidError
from backend.app.services.dataset_reader import (
    DatasetReadUnsupportedError,
    DatasetSpec,
    DatasetTooLargeError,
    read_dataset,
)
from backend.tests.support.fake_secret_store import FakeSecretStore

# This file's shape: every ref resolves to the same fixed value regardless of name
# (`FakeSecretStore(default="s3cret")`), and `.requested` (built into the shared fake) tracks every
# name asked — `_conn`'s connections all carry `secret_ref="conn-x"`, so a test below asserts
# `store.requested == ["conn-x"]` to pin exactly which ref was resolved.


def _conn(conn_type: str, *, secret_ref: str | None = "conn-x", **config: Any) -> Connection:
    return Connection(
        id=uuid.uuid4(),
        name=f"{conn_type}-t",
        type=conn_type,
        env="dev",
        config=config,
        secret_ref=secret_ref,
        created_by=uuid.uuid4(),
    )


def _frame(rows: int) -> pd.DataFrame:
    return pd.DataFrame({"id": range(rows)})


class FakeSqlConnection:
    """Stands in for the SQLAlchemy connection `_open_connection` yields."""

    # A real Connection exposes `.dialect` (the engine's) — `_table`/`core_table` need it whenever
    # `spec.catalog` is given (#936), so the fake carries a stand-in too.
    dialect = DefaultDialect()

    def __init__(self, count: int) -> None:
        self._count = count
        self.statements: list[str] = []

    def execute(self, stmt: Any) -> Any:
        self.statements.append(str(stmt))
        count = self._count

        class _Result:
            def scalar_one(self) -> int:
                return count

        return _Result()


def _patch_sql(
    monkeypatch: pytest.MonkeyPatch, *, count: int, frame: pd.DataFrame
) -> FakeSqlConnection:
    fake = FakeSqlConnection(count)

    @contextmanager
    def _fake_open(connection: Connection, secret_store: Any) -> Any:
        yield fake

    monkeypatch.setattr(dataset_reader, "_open_connection", _fake_open)
    monkeypatch.setattr(pd, "read_sql", lambda stmt, conn, **kw: frame)
    return fake


# ───────────────────────── dispatch ─────────────────────────────────


def test_orchestration_type_has_no_reader() -> None:
    with pytest.raises(DatasetReadUnsupportedError, match="orchestration"):
        read_dataset(
            _conn("airflow"),
            DatasetSpec(table="t"),
            max_rows=10,
            secret_store=FakeSecretStore(default="s3cret"),
        )


def test_non_positive_cap_rejected() -> None:
    with pytest.raises(DatasetReadUnsupportedError, match="max_rows"):
        read_dataset(
            _conn("snowflake"),
            DatasetSpec(table="t", schema="s"),
            max_rows=0,
            secret_store=FakeSecretStore(default="s3cret"),
        )


def test_default_max_rows_reads_settings() -> None:
    assert dataset_reader.default_max_rows() == 100_000


# ───────────────────────── SQL path ─────────────────────────────────


def test_sql_table_read_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch_sql(monkeypatch, count=3, frame=_frame(3))
    df = read_dataset(
        _conn("snowflake"),
        DatasetSpec(table="ORDERS", schema="RETAIL"),
        max_rows=10,
        secret_store=FakeSecretStore(default="s3cret"),
    )
    assert len(df) == 3
    # COUNT preflight ran before the read.
    assert "count" in fake.statements[0].lower()


def test_sql_count_preflight_fails_fast_without_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _explode(stmt: Any, conn: Any, **kw: Any) -> Any:
        raise AssertionError("read_sql must not run when the preflight is over-cap")

    fake = FakeSqlConnection(count=11)

    @contextmanager
    def _fake_open(connection: Connection, secret_store: Any) -> Any:
        yield fake

    monkeypatch.setattr(dataset_reader, "_open_connection", _fake_open)
    monkeypatch.setattr(pd, "read_sql", _explode)
    with pytest.raises(DatasetTooLargeError, match="11 rows"):
        read_dataset(
            _conn("unity_catalog"),
            DatasetSpec(table="t", schema="s", catalog="c"),
            max_rows=10,
            secret_store=FakeSecretStore(default="s3cret"),
        )


def test_sql_post_read_race_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    # COUNT said 10 (under cap) but rows landed before the read → the LIMIT
    # max_rows+1 read surfaces 11 rows → refuse, never diff a truncated frame.
    _patch_sql(monkeypatch, count=10, frame=_frame(11))
    with pytest.raises(DatasetTooLargeError):
        read_dataset(
            _conn("snowflake"),
            DatasetSpec(table="t", schema="s"),
            max_rows=10,
            secret_store=FakeSecretStore(default="s3cret"),
        )


def test_sql_query_spec_wraps_and_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch_sql(monkeypatch, count=2, frame=_frame(2))
    df = read_dataset(
        _conn("snowflake"),
        DatasetSpec(query="SELECT id FROM RETAIL.ORDERS;"),
        max_rows=10,
        secret_store=FakeSecretStore(default="s3cret"),
    )
    assert len(df) == 2
    assert "SELECT COUNT(*) FROM (\nSELECT id FROM RETAIL.ORDERS\n) __dataq_src" in (
        fake.statements[0]
    )


def test_sql_query_with_trailing_comment_and_semicolons_wraps_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Both pass validate_query but broke the old single-line wrapper: a trailing line comment
    # swallowed `) __dataq_src LIMIT …`, and a `; ;` tail survived the old two-step rstrip.
    fake = _patch_sql(monkeypatch, count=1, frame=_frame(1))
    read_dataset(
        _conn("snowflake"),
        DatasetSpec(query="SELECT id FROM T -- latest snapshot"),
        max_rows=10,
        secret_store=FakeSecretStore(default="s3cret"),
    )
    assert "-- latest snapshot\n) __dataq_src" in fake.statements[0]

    fake2 = _patch_sql(monkeypatch, count=1, frame=_frame(1))
    read_dataset(
        _conn("snowflake"),
        DatasetSpec(query="SELECT 1; ;"),
        max_rows=10,
        secret_store=FakeSecretStore(default="s3cret"),
    )
    assert "SELECT 1;" not in fake2.statements[0]
    assert "SELECT 1\n" in fake2.statements[0]


def test_sql_requires_secret_table_and_uc_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_sql(monkeypatch, count=1, frame=_frame(1))
    # Credential-less SQL connection → the same clean 422 as the flat-file path.
    with pytest.raises(DatasetReadUnsupportedError, match="credential"):
        read_dataset(
            _conn("snowflake", secret_ref=None),
            DatasetSpec(table="t", schema="s"),
            max_rows=10,
            secret_store=FakeSecretStore(default="s3cret"),
        )
    # Neither query nor table → comparison-branded 422, not a profiler error.
    with pytest.raises(DatasetReadUnsupportedError, match="table"):
        read_dataset(
            _conn("snowflake"),
            DatasetSpec(schema="s"),
            max_rows=10,
            secret_store=FakeSecretStore(default="s3cret"),
        )
    # UC without a catalog would silently resolve against the session default
    # catalog (the engine URL pins none) — refuse instead.
    with pytest.raises(DatasetReadUnsupportedError, match="catalog"):
        read_dataset(
            _conn("unity_catalog"),
            DatasetSpec(table="t", schema="s"),
            max_rows=10,
            secret_store=FakeSecretStore(default="s3cret"),
        )


def test_sql_query_revalidated_read_only_at_read_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Defence in depth: even if a writeful query somehow reached storage, the
    # reader re-validates before interpolating it into the wrappers.
    _patch_sql(monkeypatch, count=1, frame=_frame(1))
    with pytest.raises(CustomSqlInvalidError):
        read_dataset(
            _conn("snowflake"),
            DatasetSpec(query="DELETE FROM ORDERS"),
            max_rows=10,
            secret_store=FakeSecretStore(default="s3cret"),
        )


# ───────────────────────── flat-file path ───────────────────────────


def _small_object(monkeypatch: pytest.MonkeyPatch, size: int = 4096) -> None:
    """Stub the object-metadata seam the #595 byte preflight probes."""
    monkeypatch.setattr(dataset_reader, "file_stat", lambda **kw: flatfile.FileStat(None, size))


def test_flatfile_read_and_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    _small_object(monkeypatch)
    monkeypatch.setattr(dataset_reader, "read_flatfile_dataframe", lambda **kw: _frame(5))
    store = FakeSecretStore(default="s3cret")
    df = read_dataset(
        _conn("s3", bucket="b"),
        DatasetSpec(path="orders.csv"),
        max_rows=5,
        secret_store=store,
    )
    assert len(df) == 5 and store.requested == ["conn-x"]

    monkeypatch.setattr(dataset_reader, "read_flatfile_dataframe", lambda **kw: _frame(6))
    with pytest.raises(DatasetTooLargeError, match=re.escape("orders.csv")):
        read_dataset(
            _conn("adls_gen2"),
            DatasetSpec(path="orders.csv"),
            max_rows=5,
            secret_store=store,
        )


def test_flatfile_requires_secret_and_path() -> None:
    with pytest.raises(DatasetReadUnsupportedError, match="credential"):
        read_dataset(
            _conn("s3", secret_ref=None),
            DatasetSpec(path="orders.csv"),
            max_rows=5,
            secret_store=FakeSecretStore(default="s3cret"),
        )
    with pytest.raises(DatasetReadUnsupportedError, match="path"):
        read_dataset(
            _conn("s3"),
            DatasetSpec(),
            max_rows=5,
            secret_store=FakeSecretStore(default="s3cret"),
        )


# ───────────────────────── iceberg path ─────────────────────────────


_NO_DELETES = {
    "total-delete-files": "0",
    "total-position-deletes": "0",
    "total-equality-deletes": "0",
}


class FakeIcebergTable:
    def __init__(self, count: int, summary: dict[str, str] | None = None) -> None:
        self._count = count
        self._summary = _NO_DELETES if summary is None else summary

    def current_snapshot(self) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(summary=self._summary)

    def scan(self) -> Any:
        count = self._count

        class _Scan:
            def count(self) -> int:  # pragma: no cover — the preflight plans, it never counts
                raise AssertionError("the preflight must not count through the driver")

            def plan_files(self) -> Any:
                from types import SimpleNamespace

                return [SimpleNamespace(file=SimpleNamespace(record_count=count))]

        return _Scan()


_ICEBERG_CONFIG = {
    "catalog_type": "rest",
    "catalog_uri": "http://localhost:8181",
    "warehouse": "wh",
}


def test_iceberg_count_preflight_and_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        dataset_reader,
        "load_iceberg_table",
        lambda cfg, secret, ident, catalog_secret=None: FakeIcebergTable(4),
    )
    monkeypatch.setattr(
        dataset_reader, "read_iceberg_dataframe", lambda cfg, secret, ident, **kw: _frame(4)
    )
    df = read_dataset(
        _conn("iceberg", secret_ref=None, **_ICEBERG_CONFIG),
        DatasetSpec(table="retail.orders"),
        max_rows=10,
        secret_store=FakeSecretStore(default="s3cret"),
    )
    assert len(df) == 4


def test_iceberg_over_cap_fails_before_materializing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dataset_reader,
        "load_iceberg_table",
        lambda cfg, secret, ident, catalog_secret=None: FakeIcebergTable(11),
    )

    def _explode(*a: Any, **kw: Any) -> Any:
        raise AssertionError("must not materialize an over-cap iceberg table")

    monkeypatch.setattr(dataset_reader, "read_iceberg_dataframe", _explode)
    with pytest.raises(DatasetTooLargeError, match=re.escape("retail.orders")):
        read_dataset(
            _conn("iceberg", secret_ref=None, **_ICEBERG_CONFIG),
            DatasetSpec(table="retail.orders"),
            max_rows=10,
            secret_store=FakeSecretStore(default="s3cret"),
        )


def _refuse_iceberg(monkeypatch: pytest.MonkeyPatch, table: Any) -> DatasetTooLargeError:
    monkeypatch.setattr(
        dataset_reader, "load_iceberg_table", lambda cfg, secret, ident, catalog_secret=None: table
    )
    with pytest.raises(DatasetTooLargeError) as info:
        read_dataset(
            _conn("iceberg", secret_ref=None, **_ICEBERG_CONFIG),
            DatasetSpec(table="retail.orders"),
            max_rows=10,
            secret_store=FakeSecretStore(default="s3cret"),
        )
    return info.value


def test_an_iceberg_refusal_with_row_deletes_does_not_state_the_planned_count_as_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#2018: the planned count includes rows a row-level delete removed, so "has 15 rows" would
    misstate a table whose live count may be under the cap.
    """
    err = _refuse_iceberg(
        monkeypatch, FakeIcebergTable(15, {**_NO_DELETES, "total-position-deletes": "6"})
    )

    assert "has 15 rows" not in err.message
    assert "reads 15 rows from its data files" in err.message
    assert "That count includes rows removed by row-level deletes" in err.message
    assert "compact the table" in err.message
    assert err.detail["count"] == "planned"
    assert err.detail["row_deletes"] == "row-level deletes present (total-position-deletes=6)"


def test_an_iceberg_refusal_that_cannot_prove_no_deletes_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    err = _refuse_iceberg(monkeypatch, FakeIcebergTable(15, {}))

    assert "has 15 rows" not in err.message
    assert "may include rows removed by row-level deletes" in err.message
    assert "That count includes" not in err.message
    assert "cannot prove no row-level deletes" in err.detail["row_deletes"]


def test_an_iceberg_refusal_survives_unreadable_snapshot_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Broken(FakeIcebergTable):
        def current_snapshot(self) -> Any:
            raise OSError("metadata file vanished")

    err = _refuse_iceberg(monkeypatch, _Broken(15))

    assert err.detail["row_deletes"] == "snapshot metadata unreadable (OSError)"
    assert "may include rows removed" in err.message


def test_a_real_delete_free_iceberg_table_is_refused_with_its_exact_count(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without row-level deletes the planned count IS the live count, so the plain message
    stands — on a real pyiceberg snapshot summary, not a hand-built one.
    """
    import pyarrow as pa
    from pyiceberg.catalog.sql import SqlCatalog

    catalog = SqlCatalog("t", uri=f"sqlite:///{tmp_path}/c.db", warehouse=f"file://{tmp_path}/wh")
    catalog.create_namespace("retail")
    data = pa.table({"id": pa.array(range(15), pa.int64())})
    catalog.create_table("retail.orders", schema=data.schema).append(data)

    err = _refuse_iceberg(monkeypatch, catalog.load_table("retail.orders"))

    assert "has 15 rows" in err.message
    assert "row_deletes" not in err.detail


def test_iceberg_requires_identifier() -> None:
    with pytest.raises(DatasetReadUnsupportedError, match=re.escape("namespace.table")):
        read_dataset(
            _conn("iceberg", secret_ref=None, **_ICEBERG_CONFIG),
            DatasetSpec(),
            max_rows=10,
            secret_store=FakeSecretStore(default="s3cret"),
        )


# ───────────────────────── SQL Server derived tables (#2138) ─────────────


def test_a_sql_server_query_with_a_trailing_order_by_is_read_with_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-SQL refuses a top-level ORDER BY in a derived table without TOP/OFFSET (Msg 1033)."""
    fake = _patch_sql(monkeypatch, count=2, frame=_frame(2))
    read_dataset(
        _conn("mssql"),
        DatasetSpec(query="SELECT id FROM dbo.t ORDER BY id -- newest first"),
        max_rows=10,
        secret_store=FakeSecretStore(default="s3cret"),
    )
    assert "ORDER BY id -- newest first\nOFFSET 0 ROWS\n) __dataq_src" in fake.statements[0]


def test_a_sql_server_query_starting_with_a_cte_is_refused_with_the_fix() -> None:
    with pytest.raises(DatasetReadUnsupportedError, match="as a subquery"):
        read_dataset(
            _conn("mssql"),
            DatasetSpec(query="WITH c AS (SELECT id FROM dbo.t) SELECT id FROM c"),
            max_rows=10,
            secret_store=FakeSecretStore(default="s3cret"),
        )


def test_an_unnamed_sql_server_column_explains_the_alias_it_needs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _patch_sql(monkeypatch, count=1, frame=_frame(1))

    def refuse(stmt: Any) -> Any:
        raise RuntimeError(
            "(pytds.tds_base.ProgrammingError) No column name was specified for column 2 of "
            "'__dataq_src'."
        )

    monkeypatch.setattr(fake, "execute", refuse)
    with pytest.raises(DatasetReadUnsupportedError, match="alias each computed column"):
        read_dataset(
            _conn("mssql"),
            DatasetSpec(query="SELECT id, id * 2 FROM dbo.t"),
            max_rows=10,
            secret_store=FakeSecretStore(default="s3cret"),
        )


def test_other_engines_read_the_query_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch_sql(monkeypatch, count=2, frame=_frame(2))
    read_dataset(
        _conn("postgres"),
        DatasetSpec(query="WITH c AS (SELECT 1 AS id) SELECT id FROM c ORDER BY id"),
        max_rows=10,
        secret_store=FakeSecretStore(default="s3cret"),
    )
    assert "OFFSET" not in fake.statements[0]
