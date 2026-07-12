"""DatasetReader seam tests (ADR 0015, #792) — no live datasources.

The live I/O seams (`_open_connection`, the flat-file/iceberg read functions)
are monkeypatched exactly like the profiler's tests; what's under test is the
dispatch, the fail-fast row-cap discipline (COUNT preflight + post-read race
guard, never truncation), the read-time read-only re-validation, and the
credential handling.
"""

import re
import uuid
from contextlib import contextmanager
from typing import Any

import pandas as pd
import pytest

from backend.app.db.models import Connection
from backend.app.services import dataset_reader
from backend.app.services.custom_sql import CustomSqlInvalidError
from backend.app.services.dataset_reader import (
    DatasetReadUnsupportedError,
    DatasetSpec,
    DatasetTooLargeError,
    read_dataset,
)


class FakeSecretStore:
    def __init__(self, secret: str | None = "s3cret") -> None:
        self._secret = secret
        self.requested: list[str] = []

    def get(self, ref: str) -> str:
        self.requested.append(ref)
        assert self._secret is not None
        return self._secret


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
    monkeypatch.setattr(pd, "read_sql", lambda stmt, conn: frame)
    return fake


# ───────────────────────── dispatch ─────────────────────────────────


def test_orchestration_type_has_no_reader() -> None:
    with pytest.raises(DatasetReadUnsupportedError, match="orchestration"):
        read_dataset(
            _conn("airflow"),
            DatasetSpec(table="t"),
            max_rows=10,
            secret_store=FakeSecretStore(),  # type: ignore[arg-type]
        )


def test_non_positive_cap_rejected() -> None:
    with pytest.raises(DatasetReadUnsupportedError, match="max_rows"):
        read_dataset(
            _conn("snowflake"),
            DatasetSpec(table="t", schema="s"),
            max_rows=0,
            secret_store=FakeSecretStore(),  # type: ignore[arg-type]
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
        secret_store=FakeSecretStore(),  # type: ignore[arg-type]
    )
    assert len(df) == 3
    # COUNT preflight ran before the read.
    assert "count" in fake.statements[0].lower()


def test_sql_count_preflight_fails_fast_without_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _explode(stmt: Any, conn: Any) -> Any:
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
            secret_store=FakeSecretStore(),  # type: ignore[arg-type]
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
            secret_store=FakeSecretStore(),  # type: ignore[arg-type]
        )


def test_sql_query_spec_wraps_and_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch_sql(monkeypatch, count=2, frame=_frame(2))
    df = read_dataset(
        _conn("snowflake"),
        DatasetSpec(query="SELECT id FROM RETAIL.ORDERS;"),
        max_rows=10,
        secret_store=FakeSecretStore(),  # type: ignore[arg-type]
    )
    assert len(df) == 2
    assert "SELECT COUNT(*) FROM (SELECT id FROM RETAIL.ORDERS) __dataq_src" in fake.statements[0]


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
            secret_store=FakeSecretStore(),  # type: ignore[arg-type]
        )


# ───────────────────────── flat-file path ───────────────────────────


def test_flatfile_read_and_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dataset_reader, "read_flatfile_dataframe", lambda **kw: _frame(5))
    store = FakeSecretStore()
    df = read_dataset(
        _conn("s3", bucket="b"),
        DatasetSpec(path="orders.csv"),
        max_rows=5,
        secret_store=store,  # type: ignore[arg-type]
    )
    assert len(df) == 5 and store.requested == ["conn-x"]

    monkeypatch.setattr(dataset_reader, "read_flatfile_dataframe", lambda **kw: _frame(6))
    with pytest.raises(DatasetTooLargeError, match=re.escape("orders.csv")):
        read_dataset(
            _conn("adls_gen2"),
            DatasetSpec(path="orders.csv"),
            max_rows=5,
            secret_store=store,  # type: ignore[arg-type]
        )


def test_flatfile_requires_secret_and_path() -> None:
    with pytest.raises(DatasetReadUnsupportedError, match="credential"):
        read_dataset(
            _conn("s3", secret_ref=None),
            DatasetSpec(path="orders.csv"),
            max_rows=5,
            secret_store=FakeSecretStore(),  # type: ignore[arg-type]
        )
    with pytest.raises(DatasetReadUnsupportedError, match="path"):
        read_dataset(
            _conn("s3"),
            DatasetSpec(),
            max_rows=5,
            secret_store=FakeSecretStore(),  # type: ignore[arg-type]
        )


# ───────────────────────── iceberg path ─────────────────────────────


class FakeIcebergTable:
    def __init__(self, count: int) -> None:
        self._count = count

    def scan(self) -> Any:
        count = self._count

        class _Scan:
            def count(self) -> int:
                return count

        return _Scan()


_ICEBERG_CONFIG = {
    "catalog_type": "rest",
    "catalog_uri": "http://localhost:8181",
    "warehouse": "wh",
}


def test_iceberg_count_preflight_and_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        dataset_reader, "load_iceberg_table", lambda cfg, secret, ident: FakeIcebergTable(4)
    )
    monkeypatch.setattr(
        dataset_reader, "read_iceberg_dataframe", lambda cfg, secret, ident, **kw: _frame(4)
    )
    df = read_dataset(
        _conn("iceberg", secret_ref=None, **_ICEBERG_CONFIG),
        DatasetSpec(table="retail.orders"),
        max_rows=10,
        secret_store=FakeSecretStore(),  # type: ignore[arg-type]
    )
    assert len(df) == 4


def test_iceberg_over_cap_fails_before_materializing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dataset_reader, "load_iceberg_table", lambda cfg, secret, ident: FakeIcebergTable(11)
    )

    def _explode(*a: Any, **kw: Any) -> Any:
        raise AssertionError("must not materialize an over-cap iceberg table")

    monkeypatch.setattr(dataset_reader, "read_iceberg_dataframe", _explode)
    with pytest.raises(DatasetTooLargeError, match=re.escape("retail.orders")):
        read_dataset(
            _conn("iceberg", secret_ref=None, **_ICEBERG_CONFIG),
            DatasetSpec(table="retail.orders"),
            max_rows=10,
            secret_store=FakeSecretStore(),  # type: ignore[arg-type]
        )


def test_iceberg_requires_identifier() -> None:
    with pytest.raises(DatasetReadUnsupportedError, match=re.escape("namespace.table")):
        read_dataset(
            _conn("iceberg", secret_ref=None, **_ICEBERG_CONFIG),
            DatasetSpec(),
            max_rows=10,
            secret_store=FakeSecretStore(),  # type: ignore[arg-type]
        )
