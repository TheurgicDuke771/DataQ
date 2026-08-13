"""Unit tests for `backend/app/db/session.py`: the `get_db` dependency's transaction
teardown (C3), and `_build_engine()`'s psycopg-only connect_args driver guard (#1266).

Pure-unit (no DB): a fake session spies `rollback`/`close`. Asserts a failed
request rolls back (so a poisoned transaction never reaches the pooled
connection's next user) and re-raises, while a clean request just closes.
"""

from collections.abc import Generator
from typing import Any, cast

import pytest

from backend.app.db import session as session_module


class _FakeSession:
    def __init__(self) -> None:
        self.rolled_back = False
        self.closed = False

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def test_get_db_rolls_back_and_reraises_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeSession()
    monkeypatch.setattr(session_module, "SessionLocal", lambda: fake)

    gen = cast(Generator[Any, Any, Any], session_module.get_db())
    assert next(gen) is fake
    with pytest.raises(RuntimeError):
        gen.throw(RuntimeError("boom"))

    assert fake.rolled_back is True
    assert fake.closed is True


def test_get_db_closes_without_rollback_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeSession()
    monkeypatch.setattr(session_module, "SessionLocal", lambda: fake)

    gen = cast(Generator[Any, Any, Any], session_module.get_db())
    next(gen)
    with pytest.raises(StopIteration):
        next(gen)  # resume past the yield → no exception → finally closes

    assert fake.closed is True
    assert fake.rolled_back is False


def _captured_build_engine_connect_args(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> dict[str, Any]:
    """Shared scaffold: swaps in a fake `create_engine` that captures its kwargs
    instead of dialing a real host, and a fake `get_settings()` returning
    `database_url`, then calls `_build_engine()` and hands back the resulting
    `connect_args`. Mirrors `tests/services/test_poll_lock_timeout.py`'s
    `_captured_build_engine_connect_args` scaffold, parameterized on the URL so the
    #1266 driver-guard tests below can exercise both a psycopg and a non-psycopg
    `database_url` without touching the real `DATABASE_URL` env var / settings cache.
    """
    captured: dict[str, Any] = {}

    def _fake_create_engine(url: Any, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "not-a-real-engine"

    class _FakeSettings:
        def __init__(self, database_url: str) -> None:
            self.database_url = database_url

    fake_settings = _FakeSettings(database_url)

    monkeypatch.setattr(session_module, "create_engine", _fake_create_engine)
    monkeypatch.setattr(session_module, "get_settings", lambda: fake_settings)
    session_module._build_engine()

    connect_args = captured.get("connect_args")
    assert connect_args is not None, "_build_engine no longer passes connect_args at all"
    return dict(connect_args)


def test_build_engine_keeps_psycopg_only_connect_args_for_the_psycopg2_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The existing/default path: a `postgresql+psycopg2://` `database_url` (the
    `Settings` default) must still get the full connect_args set — the #1266 driver
    guard must not accidentally drop them for the driver they were designed for."""
    connect_args = _captured_build_engine_connect_args(
        monkeypatch, "postgresql+psycopg2://user:pw@localhost:5432/dataq"
    )

    assert connect_args.get("connect_timeout") == session_module._CONNECT_TIMEOUT_SECONDS
    assert connect_args.get("keepalives") == 1
    assert connect_args.get("keepalives_idle") == session_module._KEEPALIVES_IDLE_SECONDS
    assert connect_args.get("keepalives_interval") == session_module._KEEPALIVES_INTERVAL_SECONDS
    assert connect_args.get("keepalives_count") == session_module._KEEPALIVES_COUNT
    assert f"lock_timeout={session_module._LOCK_TIMEOUT_MS}" in connect_args.get("options", "")


def test_build_engine_drops_psycopg_only_connect_args_for_a_non_psycopg_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#1266: `settings.database_url` has no scheme/driver validator and is
    env-overridable via `DATABASE_URL`. If it ever resolved to a non-psycopg driver,
    `create_engine()`'s first real connection attempt would raise `TypeError` on the
    unrecognized `connect_timeout`/`keepalives*` kwargs — not gracefully.
    `_build_engine()` must degrade to omitting them.

    `options` is included here too: it is NOT portable across drivers (asyncpg has no
    `options` connect kwarg at all — it uses `server_settings` instead), so it must be
    dropped for a non-psycopg driver exactly like the other psycopg-only keys, rather
    than reaching `create_engine` unconditionally and raising `TypeError` on a
    different kwarg than before."""
    connect_args = _captured_build_engine_connect_args(
        monkeypatch, "postgresql+asyncpg://user:pw@localhost:5432/dataq"
    )

    for key in (
        "options",
        "connect_timeout",
        "keepalives",
        "keepalives_idle",
        "keepalives_interval",
        "keepalives_count",
    ):
        assert key not in connect_args, f"{key} leaked into connect_args for a non-psycopg driver"
