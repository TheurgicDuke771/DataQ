"""schema_drift engine tests (#592) — the pure diff, the per-datasource
introspection paths (all cloud-independent), and the baseline-diff executor's
capture/diff/re-baseline lifecycle against the real test DB."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.orm import Session

from backend.app.datasources.base import CheckOutcome
from backend.app.db.models import Check, Connection, Suite, User
from backend.app.services import schema_drift
from backend.app.services.schema_drift import (
    SchemaIntrospectionError,
    build_schema_drift_executor,
    diff_schemas,
    get_baseline,
    introspect_columns,
    rebaseline,
)

# ───────────────────────── pure diff ─────────────────────────


def _cols(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"name": n, "type": t} for n, t in pairs]


def test_diff_no_drift() -> None:
    snapshot = _cols(("id", "NUMBER"), ("email", "VARCHAR"))
    diff = diff_schemas(snapshot, list(snapshot))
    assert diff == {"added": [], "removed": [], "type_changed": [], "columns_checked": 2}


def test_diff_added_removed_and_type_changed() -> None:
    baseline = _cols(("id", "NUMBER"), ("email", "VARCHAR"), ("age", "NUMBER"))
    current = _cols(("id", "NUMBER"), ("email", "TEXT"), ("signup_at", "TIMESTAMP_NTZ"))
    diff = diff_schemas(baseline, current)
    assert diff["added"] == ["signup_at"]
    assert diff["removed"] == ["age"]
    assert diff["type_changed"] == [{"column": "email", "from": "VARCHAR", "to": "TEXT"}]
    assert diff["columns_checked"] == 3


def test_diff_ignore_columns_is_case_insensitive() -> None:
    baseline = _cols(("id", "NUMBER"), ("ETL_LOADED_AT", "TIMESTAMP"))
    current = _cols(("id", "NUMBER"))  # the ignored housekeeping column vanished
    diff = diff_schemas(baseline, current, ignore_columns=["etl_loaded_at"])
    assert diff["removed"] == []
    assert diff["columns_checked"] == 1


def test_diff_names_compared_exactly() -> None:
    # Case flips ARE drift (both sides come from one datasource path, so a case
    # change means the object really changed) — only `ignore_columns` is lenient.
    diff = diff_schemas(_cols(("Email", "TEXT")), _cols(("email", "TEXT")))
    assert diff["added"] == ["email"]
    assert diff["removed"] == ["Email"]


# ───────────────────── introspection: SQL via information_schema ─────────────


class _FakeStore:
    def get(self, name: str) -> str:
        return "secret"

    def set(self, name: str, value: str) -> None:
        raise NotImplementedError

    def delete(self, name: str) -> None:
        raise NotImplementedError


def _sql_connection() -> Connection:
    return Connection(
        id=uuid.uuid4(),
        name="sf",
        type="snowflake",
        env="dev",
        config={
            "account": "acct",
            "user": "u",
            "database": "DB",
            "schema": "PUBLIC",
            "warehouse": "WH",
        },
        secret_ref="ref",
        created_by=uuid.uuid4(),
    )


def test_sql_introspection_reads_information_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SQL path issues one bound-parameter information_schema query over the
    profiler's connection seam and maps (column_name, data_type) rows."""
    captured: dict[str, Any] = {}

    class _Conn:
        def execute(self, query: Any, params: dict[str, Any]) -> Any:
            captured["sql"] = str(query)
            captured["params"] = params

            class _Res:
                @staticmethod
                def all() -> list[tuple[str, str, str, str]]:
                    return [
                        ("PUBLIC", "ORDERS", "ID", "NUMBER"),
                        ("PUBLIC", "ORDERS", "EMAIL", "TEXT"),
                    ]

            return _Res()

    from contextlib import contextmanager

    @contextmanager
    def fake_open(connection: Connection, secret_store: Any) -> Any:
        yield _Conn()

    monkeypatch.setattr(schema_drift, "_open_connection", fake_open)
    cols = introspect_columns(
        _sql_connection(), table="ORDERS", schema=None, catalog=None, secret_store=_FakeStore()
    )
    assert cols == [{"name": "ID", "type": "NUMBER"}, {"name": "EMAIL", "type": "TEXT"}]
    assert "information_schema.columns" in captured["sql"]
    assert captured["params"] == {"schema_name": "PUBLIC", "table_name": "ORDERS"}
    # values are BOUND, never interpolated
    assert "ORDERS" not in captured["sql"]


def test_sql_introspection_uc_catalog_prefix_is_validated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import contextmanager

    seen: dict[str, str] = {}

    class _Conn:
        def execute(self, query: Any, params: dict[str, Any]) -> Any:
            seen["sql"] = str(query)

            class _Res:
                @staticmethod
                def all() -> list[tuple[str, str, str, str]]:
                    return [("retail", "orders", "id", "int")]

            return _Res()

    @contextmanager
    def fake_open(connection: Connection, secret_store: Any) -> Any:
        yield _Conn()

    monkeypatch.setattr(schema_drift, "_open_connection", fake_open)
    uc = Connection(
        id=uuid.uuid4(),
        name="uc",
        type="unity_catalog",
        env="dev",
        config={"workspace_url": "https://adb-1.azuredatabricks.net", "warehouse_id": "w"},
        secret_ref="ref",
        created_by=uuid.uuid4(),
    )
    cols = introspect_columns(
        uc, table="orders", schema="retail", catalog="main", secret_store=_FakeStore()
    )
    assert cols == [{"name": "id", "type": "int"}]
    assert "main.information_schema.columns" in seen["sql"]

    # an injection-shaped catalog never reaches the query string
    with pytest.raises(SchemaIntrospectionError):
        introspect_columns(
            uc, table="orders", schema="retail", catalog="main.bad", secret_store=_FakeStore()
        )


def test_sql_introspection_empty_result_is_classified_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import contextmanager

    class _Conn:
        def execute(self, query: Any, params: dict[str, Any]) -> Any:
            class _Res:
                @staticmethod
                def all() -> list[tuple[str, str, str, str]]:
                    return []

            return _Res()

    @contextmanager
    def fake_open(connection: Connection, secret_store: Any) -> Any:
        yield _Conn()

    monkeypatch.setattr(schema_drift, "_open_connection", fake_open)
    with pytest.raises(SchemaIntrospectionError, match="not found in information_schema"):
        introspect_columns(
            _sql_connection(), table="NOPE", schema=None, catalog=None, secret_store=_FakeStore()
        )


def test_sql_introspection_refuses_ambiguous_case_variants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # UPPER() matching can hit TWO quoted case-variant objects; merging their
    # columns would baseline a union schema no real table has (#881 review).
    from contextlib import contextmanager

    class _Conn:
        def execute(self, query: Any, params: dict[str, Any]) -> Any:
            class _Res:
                @staticmethod
                def all() -> list[tuple[str, str, str, str]]:
                    return [
                        ("PUBLIC", "ORDERS", "ID", "NUMBER"),
                        ("PUBLIC", "Orders", "id", "TEXT"),
                    ]

            return _Res()

    @contextmanager
    def fake_open(connection: Connection, secret_store: Any) -> Any:
        yield _Conn()

    monkeypatch.setattr(schema_drift, "_open_connection", fake_open)
    # The exact spelling wins when present…
    cols = introspect_columns(
        _sql_connection(), table="ORDERS", schema=None, catalog=None, secret_store=_FakeStore()
    )
    assert cols == [{"name": "ID", "type": "NUMBER"}]
    # …but a reference matching several objects with NO exact hit is refused.
    with pytest.raises(SchemaIntrospectionError, match="ambiguous"):
        introspect_columns(
            _sql_connection(), table="orders", schema=None, catalog=None, secret_store=_FakeStore()
        )


# ───────────────────── introspection: flat-file + iceberg ─────────────


def _file_connection(conn_type: str = "s3") -> Connection:
    return Connection(
        id=uuid.uuid4(),
        name="files",
        type=conn_type,
        env="dev",
        config={"bucket": "b", "region": "us-east-1"},
        secret_ref="ref",
        created_by=uuid.uuid4(),
    )


def test_file_introspection_csv_types_from_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    csv_bytes = b"id,email,amount\n1,a@x.io,10.5\n2,b@x.io,11.0\n"
    monkeypatch.setattr(schema_drift, "download_bytes", lambda **kw: csv_bytes)
    cols = introspect_columns(
        _file_connection(),
        table="landing/orders.csv",
        schema=None,
        catalog=None,
        secret_store=_FakeStore(),
    )
    by_name = {c["name"]: c["type"] for c in cols}
    assert set(by_name) == {"id", "email", "amount"}
    assert by_name["id"] == "int64"
    assert by_name["amount"] == "float64"


def test_file_introspection_parquet_types_from_footer(monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    buf = io.BytesIO()
    pq.write_table(
        pa.Table.from_pandas(pd.DataFrame({"id": [1], "name": ["x"]}), preserve_index=False), buf
    )
    monkeypatch.setattr(schema_drift, "download_bytes", lambda **kw: buf.getvalue())
    cols = introspect_columns(
        _file_connection("adls_gen2"),
        table="raw/orders.parquet",
        schema=None,
        catalog=None,
        secret_store=_FakeStore(),
    )
    by_name = {c["name"]: c["type"] for c in cols}
    assert by_name == {"id": "int64", "name": "string"}


def test_iceberg_introspection_reads_schema_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """The #859 drift leg: the snapshot comes from table METADATA (schema
    fields), never a data read."""
    from pyiceberg.schema import Schema
    from pyiceberg.types import LongType, NestedField, StringType

    class _Tbl:
        @staticmethod
        def schema() -> Schema:
            return Schema(NestedField(1, "id", LongType()), NestedField(2, "email", StringType()))

    monkeypatch.setattr(schema_drift, "load_iceberg_table", lambda *a, **kw: _Tbl())
    ib = Connection(
        id=uuid.uuid4(),
        name="ib",
        type="iceberg",
        env="dev",
        config={"catalog_type": "rest", "catalog_uri": "http://cat"},
        secret_ref=None,
        created_by=uuid.uuid4(),
    )
    cols = introspect_columns(
        ib, table="retail.orders", schema=None, catalog=None, secret_store=_FakeStore()
    )
    assert cols == [{"name": "id", "type": "long"}, {"name": "email", "type": "string"}]


def test_unsupported_connection_type_is_classified_error() -> None:
    orch = Connection(
        id=uuid.uuid4(),
        name="adf",
        type="adf",
        env="dev",
        config={},
        secret_ref="ref",
        created_by=uuid.uuid4(),
    )
    with pytest.raises(SchemaIntrospectionError, match="not supported"):
        introspect_columns(orch, table="t", schema=None, catalog=None, secret_store=_FakeStore())


def test_introspection_failure_message_is_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    # A raw adapter exception (which can carry DSN/credential fragments) must
    # never surface — the classified reason does.
    def boom(**kw: Any) -> bytes:
        raise RuntimeError("https://user:SECRET@host/path blew up")

    monkeypatch.setattr(schema_drift, "download_bytes", boom)
    with pytest.raises(SchemaIntrospectionError) as excinfo:
        introspect_columns(
            _file_connection(), table="x.csv", schema=None, catalog=None, secret_store=_FakeStore()
        )
    assert "SECRET" not in str(excinfo.value)


# ───────────────────── executor lifecycle (real DB) ─────────────


@pytest.fixture
def graph(db_session: Session) -> tuple[Session, Connection, Check]:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@x.io")
    db_session.add(user)
    db_session.flush()
    conn = _sql_connection()
    conn.created_by = user.id
    db_session.add(conn)
    db_session.flush()
    suite = Suite(
        name=f"s-{uuid.uuid4().hex[:8]}",
        connection_id=conn.id,
        created_by=user.id,
        target={"table": "ORDERS"},
    )
    db_session.add(suite)
    db_session.flush()
    check = Check(
        suite_id=suite.id,
        name="drift",
        kind="schema_drift",
        expectation_type="monitor:schema_drift",
        config={},
    )
    db_session.add(check)
    db_session.flush()
    return db_session, conn, check


def _executor_with_snapshot(
    session: Session,
    conn: Connection,
    snapshot: list[dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    *,
    persist: bool = True,
) -> Any:
    monkeypatch.setattr(schema_drift, "introspect_columns", lambda *a, **kw: list(snapshot))
    return build_schema_drift_executor(
        session,
        connection=conn,
        target_table="ORDERS",
        target_schema=None,
        target_catalog=None,
        secret_store=_FakeStore(),
        persist=persist,
    )


def test_first_run_captures_baseline_and_passes(
    graph: tuple[Session, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, conn, check = graph
    snapshot = _cols(("ID", "NUMBER"), ("EMAIL", "TEXT"))
    outcome = _executor_with_snapshot(session, conn, snapshot, monkeypatch)(check)
    assert outcome.success is True
    assert outcome.metric_value == 0.0
    assert outcome.observed_value is not None
    assert outcome.observed_value["baseline_captured"] is True
    assert outcome.observed_value["columns_checked"] == 2
    session.flush()
    row = get_baseline(session, check.id)
    assert row is not None
    assert row.baseline == {"columns": snapshot}
    assert row.captured_by is None  # run-path capture, not a manual actor


def test_second_run_diffs_against_baseline(
    graph: tuple[Session, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, conn, check = graph
    _executor_with_snapshot(session, conn, _cols(("ID", "NUMBER"), ("EMAIL", "TEXT")), monkeypatch)(
        check
    )
    session.flush()
    drifted = _cols(("ID", "NUMBER"), ("EMAIL", "VARCHAR"), ("NEW_COL", "BOOLEAN"))
    outcome = _executor_with_snapshot(session, conn, drifted, monkeypatch)(check)
    assert outcome.success is False
    assert outcome.metric_value == 2.0  # one type change + one added
    assert outcome.observed_value is not None
    assert outcome.observed_value["added"] == ["NEW_COL"]
    assert outcome.observed_value["type_changed"] == [
        {"column": "EMAIL", "from": "TEXT", "to": "VARCHAR"}
    ]
    assert "baseline_captured_at" in outcome.observed_value


def test_rebaseline_drops_row_and_next_run_recaptures(
    graph: tuple[Session, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, conn, check = graph
    _executor_with_snapshot(session, conn, _cols(("ID", "NUMBER")), monkeypatch)(check)
    session.flush()
    assert rebaseline(session, check) is True
    session.flush()
    assert get_baseline(session, check.id) is None
    assert rebaseline(session, check) is False  # idempotent — nothing left to drop
    # next run recaptures from the (changed) live shape, no drift reported
    outcome = _executor_with_snapshot(session, conn, _cols(("ID", "TEXT")), monkeypatch)(check)
    assert outcome.observed_value is not None
    assert outcome.observed_value["baseline_captured"] is True


def test_dry_run_mode_never_persists(
    graph: tuple[Session, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, conn, check = graph
    outcome = _executor_with_snapshot(
        session, conn, _cols(("ID", "NUMBER")), monkeypatch, persist=False
    )(check)
    assert outcome.observed_value is not None
    assert outcome.observed_value["dry_run"] is True
    session.flush()
    assert get_baseline(session, check.id) is None


def test_concurrent_first_runs_do_not_blow_up_on_the_baseline_unique(
    graph: tuple[Session, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two concurrent first runs both see no baseline and both insert; the loser's
    # write must be a silent no-op (ON CONFLICT DO NOTHING), never an
    # IntegrityError that fails the whole run and discards sibling results.
    session, conn, check = graph
    from backend.app.db.models import MonitorBaseline

    session.add(MonitorBaseline(check_id=check.id, kind="schema_drift", baseline={"columns": []}))
    session.flush()  # the "winner"'s row is already there
    monkeypatch.setattr(schema_drift, "get_baseline", lambda s, cid: None)  # loser's stale read
    outcome = _executor_with_snapshot(session, conn, _cols(("ID", "NUMBER")), monkeypatch)(check)
    session.flush()  # must not raise
    assert outcome.observed_value is not None
    assert outcome.observed_value["baseline_captured"] is True
    monkeypatch.undo()
    row = get_baseline(session, check.id)
    assert row is not None
    assert row.baseline == {"columns": []}  # the winner's baseline is untouched


def test_introspection_failure_is_per_check_error(
    graph: tuple[Session, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, conn, check = graph

    def boom(*a: Any, **kw: Any) -> list[dict[str, str]]:
        raise SchemaIntrospectionError("could not introspect columns: datasource_unreachable")

    monkeypatch.setattr(schema_drift, "introspect_columns", boom)
    executor = build_schema_drift_executor(
        session,
        connection=conn,
        target_table="ORDERS",
        target_schema=None,
        target_catalog=None,
        secret_store=_FakeStore(),
    )
    outcome = executor(check)
    assert isinstance(outcome, CheckOutcome)
    assert outcome.errored is True
    assert outcome.error_message is not None
    assert "datasource_unreachable" in outcome.error_message
    session.flush()
    assert get_baseline(session, check.id) is None  # a failed introspection never baselines


def test_ignore_columns_flow_through_config(
    graph: tuple[Session, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, conn, check = graph
    check.config = {"ignore_columns": ["etl_loaded_at"]}
    _executor_with_snapshot(
        session, conn, _cols(("ID", "NUMBER"), ("ETL_LOADED_AT", "TIMESTAMP")), monkeypatch
    )(check)
    session.flush()
    outcome = _executor_with_snapshot(session, conn, _cols(("ID", "NUMBER")), monkeypatch)(check)
    assert outcome.success is True  # the ignored column's disappearance is not drift
    assert outcome.metric_value == 0.0
