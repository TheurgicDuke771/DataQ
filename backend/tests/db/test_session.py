"""Unit tests for the `get_db` dependency's transaction teardown (C3).

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
