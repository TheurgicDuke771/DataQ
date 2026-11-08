"""The query limit on Snowflake and Unity Catalog sessions, GX's included (#2607)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.pool import NullPool, QueuePool

from backend.app.datasources import query_timeout
from backend.app.datasources import snowflake as snowflake_mod
from backend.app.datasources import unity_catalog as uc_mod

_SNOWFLAKE_SHOW = "SHOW PARAMETERS LIKE 'STATEMENT_TIMEOUT_IN_SECONDS' IN SESSION"


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn

    def execute(self, statement: str) -> None:
        if self._conn.fail:
            raise RuntimeError("unsupported parameter")
        self._conn.executed.append(statement)

    def fetchone(self) -> tuple[str, str] | None:
        return self._conn.row

    def close(self) -> None:
        self._conn.cursor_closed = True


class _Conn:
    def __init__(self, row: tuple[str, str] | None = None, *, fail: bool = False) -> None:
        self.row = row
        self.fail = fail
        self.executed: list[str] = []
        self.cursor_closed = False

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def close(self) -> None: ...

    def rollback(self) -> None: ...


def _creator(conn: _Conn) -> Any:
    return lambda: conn


def _conn_from(module: str, conn: _Conn) -> _Conn:
    """`conn` re-typed as a class living under ``module``, as a driver's connection would."""
    conn.__class__ = type("Connection", (_Conn,), {"__module__": module})
    return conn


@pytest.mark.parametrize(
    ("inherited", "expected"),
    [("172800", 900), ("300", 300), ("0", 900), (None, 900)],
)
def test_snowflake_keeps_a_lower_inherited_limit(inherited: str | None, expected: int) -> None:
    conn = _Conn(None if inherited is None else ("STATEMENT_TIMEOUT_IN_SECONDS", inherited))
    snowflake_mod._query_timeout_setup(conn, 900)
    assert conn.executed == [
        _SNOWFLAKE_SHOW,
        f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {expected}",
    ]
    assert conn.cursor_closed


@pytest.mark.parametrize(
    ("inherited", "expected"),
    [("172800", 900), ("300", 300), ("0", 900), ("<undefined>", 900)],
)
def test_databricks_keeps_a_lower_inherited_limit(inherited: str, expected: int) -> None:
    conn = _Conn(("STATEMENT_TIMEOUT", inherited))
    uc_mod._query_timeout_setup(conn, 900)
    assert conn.executed == ["SET STATEMENT_TIMEOUT", f"SET STATEMENT_TIMEOUT = {expected}"]


@pytest.mark.parametrize("pool_class", [QueuePool, NullPool])
@pytest.mark.parametrize(
    ("module", "expected"),
    [
        ("snowflake.connector.connection", "ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 42"),
        ("databricks.sql.client", "SET STATEMENT_TIMEOUT = 42"),
    ],
)
def test_every_new_connection_of_the_engine_gets_the_limit(
    monkeypatch: pytest.MonkeyPatch, pool_class: type, module: str, expected: str
) -> None:
    """The hook sits on the pool class, so an engine GX builds is covered like DataQ's own."""
    monkeypatch.setattr(query_timeout, "query_timeout_s", lambda: 42)
    conn = _conn_from(module, _Conn(("STATEMENT_TIMEOUT", "0")))
    pool = pool_class(_creator(conn))
    pool.connect().close()
    assert conn.executed[-1] == expected


def test_another_drivers_connection_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(query_timeout, "query_timeout_s", lambda: 42)
    conn = _conn_from("psycopg2.extensions", _Conn())
    QueuePool(_creator(conn)).connect().close()
    assert conn.executed == []


def test_registering_twice_runs_the_setup_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(query_timeout, "_REGISTERED", dict(query_timeout._REGISTERED))
    calls: list[int] = []
    for _ in range(2):
        query_timeout.register_session_limit("probe", "probe_driver", lambda c, s: calls.append(s))
    QueuePool(_creator(_conn_from("probe_driver.client", _Conn()))).connect().close()
    assert len(calls) == 1


def test_a_setup_the_warehouse_rejects_leaves_the_session_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings: list[dict[str, Any]] = []
    monkeypatch.setattr(query_timeout.log, "warning", lambda event, **kw: warnings.append(kw))
    conn = _conn_from("databricks.sql.client", _Conn(fail=True))
    QueuePool(_creator(conn)).connect().close()  # no raise
    assert warnings == [{"engine": "unity_catalog", "error_type": "RuntimeError"}]


def test_the_uc_test_connection_carries_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """`sql.connect` is not a SQLAlchemy pool, so the adapter applies the limit itself."""
    from databricks import sql

    monkeypatch.setattr(query_timeout, "query_timeout_s", lambda: 42)
    conn = _Conn(("STATEMENT_TIMEOUT", "0"))
    monkeypatch.setattr(sql, "connect", lambda **_: conn)
    config = {
        "workspace_url": "https://adb-1234.5.azuredatabricks.net",
        "warehouse_id": "abc123def456",
    }
    uc_mod.UnityCatalogConnectionAdapter().test(config, "dapi-token")
    assert conn.executed == ["SET STATEMENT_TIMEOUT", "SET STATEMENT_TIMEOUT = 42", "SELECT 1"]
