"""One warehouse login for list + profile (#1645).

The unit half drives `shared_connection` through a counting fake of the fresh-open
seam; the real-driver half counts actual DBAPI connects (the login) against Postgres,
standing in for the warehouse behind the same SQLAlchemy engine path.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import Pool

from backend.app.db.models import Connection, LlmInvocation, Suite, User
from backend.app.services import llm_checksuggest, llm_sqlgen, profile_service
from backend.app.services.profile_service import (
    ProfileFailedError,
    shared_connection,
    suggest_policy_for_target,
)
from backend.tests.support.fake_secret_store import FakeSecretStore
from backend.tests.support.llm_helpers import admin_user, make_sql_suite

# ── unit: the scope's bookkeeping, through a counting fake of the fresh-open seam ──


class _FakeConn:
    def __init__(self, n: int) -> None:
        self.n = n
        self.invalidated = False
        self.closed = False
        self.dead = False
        self.rollbacks = 0
        self.pings = 0
        self.invalidations = 0

    def rollback(self) -> None:
        self.rollbacks += 1

    def invalidate(self) -> None:
        self.invalidations += 1
        self.invalidated = True

    def execute(self, _stmt: Any) -> Any:
        self.pings += 1
        if self.dead:
            raise OperationalError("select 1", {}, Exception("session expired"))
        return type("Result", (), {"close": lambda _self: None})()


class _Opener:
    """Counts fresh opens and closes of `_open_connection`."""

    def __init__(self, *, fail_opens: int = 0, fail_close: bool = False) -> None:
        self.opened: list[_FakeConn] = []
        self.closed: list[_FakeConn] = []
        self._fail_opens = fail_opens
        self._fail_close = fail_close

    @contextmanager
    def __call__(self, connection: Any, secret_store: Any) -> Iterator[_FakeConn]:
        if self._fail_opens:
            self._fail_opens -= 1
            raise OperationalError("connect", {}, Exception("login failed"))
        conn = _FakeConn(len(self.opened) + 1)
        self.opened.append(conn)
        yield conn
        self.closed.append(conn)
        if self._fail_close:
            raise RuntimeError("close failed")


def _connection(conn_id: uuid.UUID | None = None) -> Any:
    return type("Conn", (), {"id": conn_id or uuid.uuid4(), "type": "snowflake"})()


@pytest.fixture
def opener(monkeypatch: pytest.MonkeyPatch) -> _Opener:
    fake = _Opener()
    monkeypatch.setattr(profile_service, "_open_connection", fake)
    return fake


def _use(connection: Any) -> _FakeConn:
    with profile_service._datasource_connection(connection, FakeSecretStore()) as conn:
        return conn  # type: ignore[no-any-return]


def test_outside_a_scope_every_call_opens_its_own_connection(opener: _Opener) -> None:
    connection = _connection()
    assert _use(connection) is not _use(connection)
    assert len(opener.opened) == 2
    assert opener.closed == opener.opened


def test_inside_a_scope_the_same_connection_is_opened_once_and_closed_at_exit(
    opener: _Opener,
) -> None:
    connection = _connection()
    with shared_connection():
        first, second = _use(connection), _use(connection)
        assert first is second
        assert opener.closed == []  # held open between operations
    assert len(opener.opened) == 1
    assert opener.closed == [first]


def test_the_scope_keys_on_the_datasource_not_the_python_object(opener: _Opener) -> None:
    conn_id = uuid.uuid4()
    with shared_connection():
        assert _use(_connection(conn_id)) is _use(_connection(conn_id))
    assert len(opener.opened) == 1


def test_different_datasources_in_one_scope_each_get_their_own(opener: _Opener) -> None:
    with shared_connection():
        assert _use(_connection()) is not _use(_connection())
    assert len(opener.opened) == 2
    assert len(opener.closed) == 2


def test_a_nested_scope_joins_the_outer_one(opener: _Opener) -> None:
    connection = _connection()
    with shared_connection():
        outer = _use(connection)
        with shared_connection():
            assert _use(connection) is outer
        assert opener.closed == []  # the inner exit must not close the outer's connection
    assert opener.closed == [outer]


def test_the_scope_ends_at_exit(opener: _Opener) -> None:
    connection = _connection()
    with shared_connection():
        inside = _use(connection)
    assert _use(connection) is not inside
    assert len(opener.opened) == 2


def test_a_failed_statement_rolls_back_and_keeps_the_login(opener: _Opener) -> None:
    connection = _connection()
    with shared_connection():
        with pytest.raises(ValueError):
            with profile_service._datasource_connection(connection, FakeSecretStore()):
                raise ValueError("statement rejected")
        again = _use(connection)
    assert len(opener.opened) == 1
    assert again.rollbacks == 1
    assert again.pings == 1  # a live connection is probed, then kept


def test_a_dead_connection_is_dropped_and_the_next_call_logs_in_again(opener: _Opener) -> None:
    connection = _connection()
    with shared_connection():
        with pytest.raises(OperationalError):
            with profile_service._datasource_connection(connection, FakeSecretStore()) as conn:
                conn.invalidated = True
                raise OperationalError("select", {}, Exception("connection reset"))
        again = _use(connection)
        assert again is not conn
        assert opener.closed == [conn]  # the dead one was released, not leaked to scope exit
    assert len(opener.opened) == 2
    assert opener.closed == [conn, again]


def test_a_dead_session_the_dialect_never_flagged_is_still_dropped(opener: _Opener) -> None:
    """Snowflake and Databricks don't classify disconnects, so `invalidated` stays False."""
    connection = _connection()
    with shared_connection():
        with pytest.raises(OperationalError):
            with profile_service._datasource_connection(connection, FakeSecretStore()) as conn:
                conn.dead = True
                raise OperationalError("select", {}, Exception("session expired"))
        again = _use(connection)
        assert again is not conn
        assert conn.pings == 1
        assert conn.invalidations == 1
    assert len(opener.opened) == 2


def test_a_failed_login_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Opener(fail_opens=1)
    monkeypatch.setattr(profile_service, "_open_connection", fake)
    connection = _connection()
    with shared_connection():
        with pytest.raises(OperationalError):
            _use(connection)
        _use(connection)
    assert len(fake.opened) == 1


def test_the_body_exception_survives_the_scope_closing(opener: _Opener) -> None:
    connection = _connection()
    with pytest.raises(KeyError, match="body"):
        with shared_connection():
            _use(connection)
            raise KeyError("body")
    assert len(opener.closed) == 1


def test_a_close_failure_at_scope_exit_is_logged_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Opener(fail_close=True)
    monkeypatch.setattr(profile_service, "_open_connection", fake)
    with shared_connection():
        _use(_connection())
        _use(_connection())
    assert len(fake.closed) == 2  # one failing close does not strand the other


# ── real driver: count DBAPI connects (= logins) against Postgres ──

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
_needs_pg = pytest.mark.skipif(not TEST_DATABASE_URL, reason="requires TEST_DATABASE_URL")


@pytest.fixture
def warehouse_table(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A real table, and `_engine_args` pointed at it — the SQL profiler's real engine path."""
    assert TEST_DATABASE_URL is not None
    name = f"shared_conn_{uuid.uuid4().hex[:8]}"
    admin = create_engine(TEST_DATABASE_URL)
    with admin.begin() as conn:
        conn.execute(text(f"CREATE TABLE public.{name} (id integer, email text, qty integer)"))
        conn.execute(
            text(
                f"INSERT INTO public.{name} VALUES "
                "(1, 'a@example.com', 3), (2, 'b@example.com', 4), (3, NULL, NULL)"
            )
        )
    monkeypatch.setattr(profile_service, "_engine_args", lambda *_a: (TEST_DATABASE_URL, {}))
    try:
        yield name
    finally:
        with admin.begin() as conn:
            conn.execute(text(f"DROP TABLE public.{name}"))
        admin.dispose()


@pytest.fixture
def logins() -> Iterator[list[object]]:
    """Every new DBAPI connection made from here on — each is a warehouse login."""
    seen: list[object] = []

    def _on_connect(dbapi_conn: object, _record: object) -> None:
        seen.append(dbapi_conn)

    event.listen(Pool, "connect", _on_connect)
    try:
        yield seen
    finally:
        event.remove(Pool, "connect", _on_connect)


def _sql_suite(db_session: Any, owner: User, table: str, **kw: Any) -> Suite:
    suite = make_sql_suite(db_session, owner, target={"table": table, "schema": "public"}, **kw)
    connection = db_session.get(Connection, suite.connection_id)
    connection.secret_ref = "ref"
    connection.config = {"schema": "public"}
    db_session.commit()
    return suite


def _connection_of(db_session: Any, suite: Suite) -> Connection:
    connection = db_session.get(Connection, suite.connection_id)
    assert connection is not None
    return connection  # type: ignore[no-any-return]


@_needs_pg
def test_list_then_profile_without_the_scope_costs_two_logins(
    db_session: Any, warehouse_table: str, logins: list[object]
) -> None:
    """The baseline the fix is measured against — and proof the counter counts logins."""
    owner = admin_user(db_session, prefix="shared")
    connection = _connection_of(db_session, _sql_suite(db_session, owner, warehouse_table))
    store = FakeSecretStore({"ref": "pw"})
    target = {"table": warehouse_table, "schema": "public"}
    columns = profile_service.list_columns(
        connection, session=db_session, secret_store=store, **target
    )
    profile_service.profile_connection(
        connection, session=db_session, columns=columns, top_n=5, secret_store=store, **target
    )
    assert len(logins) == 2


@_needs_pg
def test_suggest_policy_lists_and_profiles_on_one_login(
    db_session: Any, warehouse_table: str, logins: list[object]
) -> None:
    owner = admin_user(db_session, prefix="shared")
    connection = _connection_of(db_session, _sql_suite(db_session, owner, warehouse_table))
    policy = suggest_policy_for_target(
        connection,
        session=db_session,
        table=warehouse_table,
        schema="public",
        secret_store=FakeSecretStore({"ref": "pw"}),
    )
    assert policy["pii_columns"] == ["email"]
    assert len(logins) == 1


@_needs_pg
def test_suggest_policy_profile_failure_still_raises_and_releases_the_login(
    db_session: Any,
    warehouse_table: str,
    logins: list[object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server-rejected profile statement surfaces as before, on the same one login."""
    owner = admin_user(db_session, prefix="shared")
    connection = _connection_of(db_session, _sql_suite(db_session, owner, warehouse_table))
    real_list = profile_service.list_columns

    def _list_plus_ghost(*args: Any, **kwargs: Any) -> list[str]:
        return [*real_list(*args, **kwargs), "no_such_column"]

    monkeypatch.setattr(profile_service, "list_columns", _list_plus_ghost)
    with pytest.raises(ProfileFailedError):
        suggest_policy_for_target(
            connection,
            session=db_session,
            table=warehouse_table,
            schema="public",
            secret_store=FakeSecretStore({"ref": "pw"}),
        )
    assert len(logins) == 1
    assert profile_service._SHARED_CONNECTIONS.get() is None


def _sqlgen_invocation(db_session: Any, suite: Suite, owner: User, **request: Any) -> Any:
    invocation = LlmInvocation(
        kind="sql_generation",
        requested_by_user_id=owner.id,
        suite_id=suite.id,
        request={"description": "no null emails", **request},
    )
    db_session.add(invocation)
    db_session.commit()
    return invocation


@contextmanager
def _extra_table(ddl: str) -> Iterator[str]:
    assert TEST_DATABASE_URL is not None
    name = f"shared_conn_x_{uuid.uuid4().hex[:8]}"
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE TABLE public.{name} ({ddl})"))
    try:
        yield name
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP TABLE public.{name}"))
        engine.dispose()


@_needs_pg
def test_sqlgen_prompt_with_profile_and_an_extra_table_is_one_login(
    db_session: Any, warehouse_table: str, logins: list[object]
) -> None:
    """Primary list + profile, then an additional table's list + profile: four logins before."""
    owner = admin_user(db_session, prefix="shared")
    suite = _sql_suite(db_session, owner, warehouse_table)
    with _extra_table("id integer, note text") as other:
        logins.clear()
        invocation = _sqlgen_invocation(
            db_session, suite, owner, include_profile=True, additional_tables=[{"table": other}]
        )
        prompt, _, _ = llm_sqlgen.build_prompt(
            db_session, invocation, FakeSecretStore({"ref": "pw"})
        )
    assert "- email" in prompt and "- note" in prompt
    assert prompt.count("Aggregate profile (value-bearing") == 2
    assert len(logins) == 1


@_needs_pg
def test_sqlgen_degraded_profile_leaves_the_shared_login_usable(
    db_session: Any, logins: list[object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The primary profile is server-rejected (MIN over json) and degrades; Postgres has
    now aborted the transaction, so the extra table's listing on the SAME login only
    works if the scope rolled it back.
    """
    assert TEST_DATABASE_URL is not None
    monkeypatch.setattr(profile_service, "_engine_args", lambda *_a: (TEST_DATABASE_URL, {}))
    owner = admin_user(db_session, prefix="shared")
    with _extra_table("id integer, payload json") as primary, _extra_table("note text") as other:
        suite = _sql_suite(db_session, owner, primary)
        logins.clear()
        invocation = _sqlgen_invocation(
            db_session, suite, owner, include_profile=True, additional_tables=[{"table": other}]
        )
        prompt, _, _ = llm_sqlgen.build_prompt(
            db_session, invocation, FakeSecretStore({"ref": "pw"})
        )
    assert "Aggregate profile: unavailable." in prompt
    assert "- note" in prompt
    assert len(logins) == 1


@_needs_pg
def test_checksuggest_prompt_lists_and_profiles_on_one_login(
    db_session: Any, warehouse_table: str, logins: list[object]
) -> None:
    owner = admin_user(db_session, prefix="shared")
    suite = _sql_suite(db_session, owner, warehouse_table)
    invocation = LlmInvocation(
        kind=llm_checksuggest.CHECKSUGGEST_KIND,
        requested_by_user_id=owner.id,
        suite_id=suite.id,
        request={},
    )
    db_session.add(invocation)
    db_session.commit()
    prompt, _, _ = llm_checksuggest.build_prompt(
        db_session, invocation, FakeSecretStore({"ref": "pw"})
    )
    assert "Row count: 3" in prompt
    assert len(logins) == 1


@_needs_pg
def test_a_session_killed_mid_scope_is_replaced_by_a_fresh_login(
    db_session: Any, warehouse_table: str, logins: list[object]
) -> None:
    """The server drops the shared session; the next call must log in again, not reuse it."""
    owner = admin_user(db_session, prefix="shared")
    connection = _connection_of(db_session, _sql_suite(db_session, owner, warehouse_table))
    store = FakeSecretStore({"ref": "pw"})
    target = {"table": warehouse_table, "schema": "public"}
    killer = create_engine(TEST_DATABASE_URL or "")
    with shared_connection():
        profile_service.list_columns(connection, session=db_session, secret_store=store, **target)
        pid = logins[-1].info.backend_pid  # type: ignore[attr-defined]
        with killer.begin() as conn:
            conn.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        with pytest.raises(ProfileFailedError):
            profile_service.list_columns(
                connection, session=db_session, secret_store=store, **target
            )
        columns = profile_service.list_columns(
            connection, session=db_session, secret_store=store, **target
        )
    killer.dispose()
    assert columns == ["id", "email", "qty"]
    assert len(logins) == 3  # the scope's login, the killer's, and the replacement


@_needs_pg
def test_a_dead_session_the_dialect_never_flags_is_discarded_without_a_reset_rollback(
    db_session: Any,
    warehouse_table: str,
    logins: list[object],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The Snowflake shape, on a real driver: the dialect doesn't classify the disconnect, so
    `invalidated` stays False. Eviction must discard the connection rather than hand it back
    to the pool, whose reset-on-return would ROLLBACK on the dead session and log an error.
    """
    from sqlalchemy.dialects.postgresql.psycopg2 import PGDialect_psycopg2

    monkeypatch.setattr(PGDialect_psycopg2, "is_disconnect", lambda *_a, **_k: False)
    owner = admin_user(db_session, prefix="shared")
    connection = _connection_of(db_session, _sql_suite(db_session, owner, warehouse_table))
    store = FakeSecretStore({"ref": "pw"})
    target = {"table": warehouse_table, "schema": "public"}
    with shared_connection():
        profile_service.list_columns(connection, session=db_session, secret_store=store, **target)
        shared = profile_service._SHARED_CONNECTIONS.get()
        assert shared is not None
        sa_conn, _ = next(iter(shared._live.values()))
        sa_conn.connection.dbapi_connection.close()
        with pytest.raises(ProfileFailedError):
            profile_service.list_columns(
                connection, session=db_session, secret_store=store, **target
            )
        assert shared._live == {}
        columns = profile_service.list_columns(
            connection, session=db_session, secret_store=store, **target
        )
    assert columns == ["id", "email", "qty"]
    assert len(logins) == 2
    assert not [r for r in caplog.records if r.name.startswith("sqlalchemy.pool")]
