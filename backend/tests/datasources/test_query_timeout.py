"""The per-query limit on every generic SQL engine (#2496)."""

from __future__ import annotations

import dataclasses
import os
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text

from backend.app.core.config import Settings
from backend.app.datasources import athena as athena_mod
from backend.app.datasources import mssql as mssql_mod
from backend.app.datasources import mysql as mysql_mod
from backend.app.datasources import postgres as postgres_mod
from backend.app.datasources import trino as trino_mod
from backend.app.datasources.base import CheckOutcome
from backend.app.datasources.query_timeout import (
    QueryTimeoutError,
    is_query_timeout,
    query_timeout_message,
)
from backend.app.datasources.sql_engines import SQL_ENGINES
from backend.app.db.models import Check
from backend.app.services import run_service
from backend.app.services.failure_classifier import classify_failure_reason, safe_failure_reason


@pytest.fixture
def limit(monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setenv("DATASOURCE_QUERY_TIMEOUT_S", "7")
    return 7


def _args(conn_type: str, raw: dict[str, Any], secret: str | None = "pw") -> dict[str, Any]:
    spec = SQL_ENGINES[conn_type]
    return spec.session_connect_args(spec.validate_config(raw), secret, None)


_PG = {"host": "db.example.com", "database": "warehouse", "user": "dq", "sslmode": "require"}


def test_setting_is_bounded() -> None:
    with pytest.raises(ValidationError):
        Settings(datasource_query_timeout_s=0)
    assert Settings().datasource_query_timeout_s == 1800


@pytest.mark.parametrize("read_only", [True, False])
def test_postgres_leaves_the_limit_out_of_the_startup_options(limit: int, read_only: bool) -> None:
    # A startup option would replace a lower limit the role or database sets.
    spec = SQL_ENGINES["postgres"]
    args = spec.connect_args(spec.validate_config(_PG), None, read_only=read_only)
    assert "statement_timeout" not in args["options"]


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="needs Postgres")
@pytest.mark.parametrize(
    ("inherited", "expected_ms"),
    [("0", "7000"), ("1s", "1000"), ("1h", "7000")],
)
def test_postgres_keeps_a_lower_inherited_limit(
    limit: int, inherited: str, expected_ms: str
) -> None:
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    try:
        raw = engine.raw_connection()
        try:
            cursor = raw.cursor()
            cursor.execute("SELECT set_config('statement_timeout', %s, false)", (inherited,))
            raw.commit()
            postgres_mod._query_timeout_setup(raw, limit)
            raw.rollback()  # what the pool does on return: the setting must survive it
            cursor.execute("SELECT setting FROM pg_settings WHERE name = 'statement_timeout'")
            row = cursor.fetchone()
            assert row is not None and row[0] == expected_ms
        finally:
            raw.close()
    finally:
        engine.dispose()


def test_redshift_sets_statement_timeout_in_ms(limit: int) -> None:
    raw = {
        "host": "wg.123456789012.us-east-1.redshift-serverless.amazonaws.com",
        "database": "dev",
        "user": "dq",
        "sslmode": "verify-full",
    }
    assert "-c statement_timeout=7000" in _args("redshift", raw)["options"]


def test_trino_leaves_the_limit_to_the_connect_hook(limit: int) -> None:
    raw = {"host": "trino.example.com", "database": "hive", "user": "dq", "auth_type": "none"}
    assert "session_properties" not in _args("trino", raw, None)


def test_mssql_python_tds_takes_the_query_timeout(limit: int) -> None:
    raw = {"host": "sql.example.com", "database": "db", "user": "dq"}
    args = _args("mssql", raw)
    assert args["timeout"] == 7
    assert args["login_timeout"] == 60  # the login timeout stays its own knob


def test_athena_gets_the_limited_cursor() -> None:
    raw = {
        "region": "us-east-1",
        "database": "awsdatacatalog",
        "schema": "sales",
        "work_group": "primary",
        "user": "AKIAEXAMPLEEXAMPLE00",
    }
    args = _args("athena", raw, "secret")
    assert args["cursor_class"] is athena_mod.limited_cursor_class()


def test_athena_override_targets_methods_the_pinned_pyathena_still_has() -> None:
    # The override replaces a private pyathena method: a bump that renames it would silently
    # restore the unbounded poll.
    from pyathena.common import BaseCursor

    for name in ("_poll_until_terminal", "_get_query_execution", "_cancel", "_wait_poll_interval"):
        assert callable(getattr(BaseCursor, name)), name


class _Execution:
    def __init__(self, state: str) -> None:
        self.state = state


def _fake_cursor(states: list[str], cancelled: list[str], *, cancel_error: bool = False) -> Any:
    cls = athena_mod.limited_cursor_class()
    cursor = object.__new__(cls)
    it = iter(states)
    cursor._get_query_execution = lambda _qid: _Execution(next(it))
    cursor._on_poll = None
    cursor._wait_poll_interval = lambda: None

    def _cancel(qid: str) -> None:
        cancelled.append(qid)
        if cancel_error:
            raise RuntimeError("network down")

    cursor._cancel = _cancel
    return cursor


@pytest.mark.parametrize("cancel_error", [False, True])
def test_athena_poll_cancels_and_fails_past_the_limit(
    limit: int, monkeypatch: pytest.MonkeyPatch, cancel_error: bool
) -> None:
    clock = iter([0.0, 3.0, 8.0])
    monkeypatch.setattr(
        "backend.app.datasources.athena.time", SimpleNamespace(monotonic=lambda: next(clock))
    )
    cancelled: list[str] = []
    cursor = _fake_cursor(["RUNNING"] * 3, cancelled, cancel_error=cancel_error)
    with pytest.raises(QueryTimeoutError) as excinfo:
        cursor._poll_until_terminal("q-1")
    assert cancelled == ["q-1"]
    assert safe_failure_reason(excinfo.value) == query_timeout_message()


def test_athena_poll_returns_a_query_that_finishes_in_time(
    limit: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = iter([0.0, 3.0])
    monkeypatch.setattr(
        "backend.app.datasources.athena.time", SimpleNamespace(monotonic=lambda: next(clock))
    )
    cancelled: list[str] = []
    cursor = _fake_cursor(["RUNNING", "SUCCEEDED"], cancelled)
    assert cursor._poll_until_terminal("q-1").state == "SUCCEEDED"
    assert cancelled == []


class _RecordingConnection:
    """Answers every SELECT with ``row`` and records each statement."""

    def __init__(self, server_version: str = "", rows: list[tuple[Any, ...]] | None = None) -> None:
        self.server_version = server_version
        self.rows = rows or []
        self.executed: list[str] = []

    def cursor(self) -> Any:
        conn = self

        class _Cursor:
            def execute(self, statement: str) -> None:
                conn.executed.append(statement)

            def fetchone(self) -> Any:
                return conn.rows[0] if conn.rows else None

            def fetchall(self) -> list[tuple[Any, ...]]:
                return conn.rows if conn.executed[-1].startswith(("SELECT", "SHOW")) else []

            def close(self) -> None:
                pass

        return _Cursor()


@pytest.mark.parametrize(
    ("server_version", "inherited", "expected"),
    [
        ("8.4.11", 0, "SET SESSION max_execution_time = 7000"),
        ("8.4.11", 1000, "SET SESSION max_execution_time = 1000"),
        ("8.4.11", 3_600_000, "SET SESSION max_execution_time = 7000"),
        ("11.4.13-MariaDB-ubu2404", 0.0, "SET SESSION max_statement_time = 7.000000"),
        ("11.4.13-MariaDB-ubu2404", 1.5, "SET SESSION max_statement_time = 1.500000"),
        ("5.5.5-10.6.18-MariaDB", 0.0, "SET SESSION max_statement_time = 7.000000"),
    ],
)
def test_mysql_keeps_a_lower_inherited_limit(
    server_version: str, inherited: float, expected: str
) -> None:
    conn = _RecordingConnection(server_version, [(inherited,)])
    mysql_mod._query_timeout_setup(conn, 7)
    assert conn.executed[-1] == expected


def test_mysql_never_writes_a_large_limit_in_exponent_form() -> None:
    conn = _RecordingConnection("8.4.11", [(0,)])
    mysql_mod._query_timeout_setup(conn, 1800)
    assert conn.executed[-1] == "SET SESSION max_execution_time = 1800000"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("100.00d", "SET SESSION query_max_run_time = '7.000s'"),
        ("3.00s", "SET SESSION query_max_run_time = '3.000s'"),
        ("500.00ms", "SET SESSION query_max_run_time = '0.500s'"),
        ("not a duration", "SET SESSION query_max_run_time = '7.000s'"),
    ],
)
def test_trino_bounds_the_whole_query_and_keeps_a_lower_cluster_limit(
    value: str, expected: str
) -> None:
    # `query_max_run_time` counts resource-group queueing; `query_max_execution_time` does not.
    conn = _RecordingConnection(rows=[("query_max_run_time", value, "100.00d", "varchar", "")])
    trino_mod._query_timeout_setup(conn, 7)
    assert conn.executed == ["SHOW SESSION LIKE 'query_max_run_time'", expected]


def test_mssql_odbc_sets_the_connection_timeout_attribute() -> None:
    odbc_conn: Any = type("Connection", (), {"__module__": "pyodbc"})()
    mssql_mod._query_timeout_setup(odbc_conn, 7)
    assert odbc_conn.timeout == 7

    tds_conn: Any = type("Connection", (), {"__module__": "pytds.tds_base"})()
    mssql_mod._query_timeout_setup(tds_conn, 7)
    assert not hasattr(tds_conn, "timeout")  # python-tds took it as a connect arg


def test_prepare_engine_runs_the_setup_on_every_new_connection(limit: int) -> None:
    seen: list[int] = []
    spec = dataclasses.replace(
        SQL_ENGINES["postgres"],
        session_statements=None,
        query_timeout_setup=lambda _conn, seconds: seen.append(seconds),
    )
    engine = spec.prepare_engine(create_engine("sqlite://"))
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    assert seen == [7]


def test_the_engines_with_a_connect_hook_declare_it() -> None:
    assert SQL_ENGINES["postgres"].query_timeout_setup is postgres_mod._query_timeout_setup
    assert SQL_ENGINES["mysql"].query_timeout_setup is mysql_mod._query_timeout_setup
    assert SQL_ENGINES["trino"].query_timeout_setup is trino_mod._query_timeout_setup
    assert SQL_ENGINES["mssql"].query_timeout_setup is mssql_mod._query_timeout_setup


# Each engine's own text, as the live runs (PostgreSQL 16, MariaDB 11.4, MySQL 8.4, Trino 476)
# produced it, wrapped the way SQLAlchemy wraps a DBAPI error.
_ENGINE_ERRORS = [
    "(psycopg2.errors.QueryCanceled) canceling statement due to statement timeout",
    (
        "(pymysql.err.OperationalError) (1969, 'Query execution was interrupted "
        "(max_statement_time exceeded)')"
    ),
    (
        "(pymysql.err.OperationalError) (3024, 'Query execution was interrupted, maximum "
        "statement execution time exceeded')"
    ),
    (
        "(trino.exceptions.TrinoQueryError) TrinoQueryError(type=INSUFFICIENT_RESOURCES, "
        'name=EXCEEDED_TIME_LIMIT, message="Query exceeded the maximum execution time limit '
        'of 2.00s")'
    ),
    "('HYT00', '[HYT00] [Microsoft][ODBC Driver 18 for SQL Server]Query timeout expired (0)')",
    "Job execution was cancelled: Job timed out after 7 sec",
    (
        "(snowflake.connector.errors.ProgrammingError) 000630 (57014): Statement reached its "
        "statement or warehouse timeout of 2 second(s) and was canceled."
    ),
    (
        "(databricks.sql.exc.ServerOperationError) [QUERY_EXECUTION_TIMEOUT_EXCEEDED] Query "
        "execution was cancelled due to exceeding the timeout (2s). You can increase the limit "
        "in seconds by setting STATEMENT_TIMEOUT. SQLSTATE: 57KD0"
    ),
]


@pytest.mark.parametrize("message", _ENGINE_ERRORS)
def test_an_engine_timeout_is_classified_as_one(message: str) -> None:
    exc = RuntimeError(message)
    assert classify_failure_reason(exc) == query_timeout_message()
    assert safe_failure_reason(exc) == query_timeout_message()


def test_a_timeout_deep_in_the_cause_chain_is_found() -> None:
    try:
        try:
            raise RuntimeError("canceling statement due to statement timeout")
        except RuntimeError as inner:
            raise ValueError("GX metric failed") from inner
    except ValueError as outer:
        assert is_query_timeout(outer)


@pytest.mark.parametrize(
    "message",
    [
        "connection to server at db.example.com timed out",
        "TimeoutError: timed out",  # python-tds: also its login timeout, so not claimed
        "could not connect to server: Connection refused",
    ],
)
def test_a_connect_timeout_is_not_called_a_query_timeout(message: str) -> None:
    exc = RuntimeError(message)
    assert not is_query_timeout(exc)
    assert classify_failure_reason(exc) != query_timeout_message()


def test_the_message_names_the_setting_and_its_value(limit: int) -> None:
    message = query_timeout_message()
    assert "DATASOURCE_QUERY_TIMEOUT_S = 7 seconds" in message


def test_a_per_check_timeout_is_stored_as_the_fixed_reason(limit: int) -> None:
    check = Check(
        id=uuid.uuid4(),
        kind="expectation",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "id"},
    )
    outcome = CheckOutcome(
        expectation_type="expect_column_values_to_not_be_null",
        success=False,
        errored=True,
        error_message="(psycopg2.errors.QueryCanceled) canceling statement due to statement "
        "timeout\n[SQL: SELECT count(*) FROM orders WHERE id IS NULL]",
    )
    result = run_service._build_result(uuid.uuid4(), check, outcome, zero_sample=False)
    assert result.observed_value == {"error": query_timeout_message()}
