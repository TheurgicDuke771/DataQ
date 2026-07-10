"""Tests for the lineage dispatch choke point — the fail-open contract.

The dark path must touch nothing (proved by a session that raises on any access);
a configured-but-broken emit must be swallowed (proved by a client whose ``emit``
raises). DB-backed cases use the shared ``db_session`` fixture.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from backend.app.db.models import Asset, Check, Connection, Result, Run, Suite, User
from backend.app.lineage import dispatch, emitter


class _ExplodingSession:
    """Any attribute access fails — proves the dark path never queries."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"session was touched on the dark path: .{name}")


class _SpyClient:
    def __init__(self, *, boom: bool = False) -> None:
        self.emitted: list[Any] = []
        self._boom = boom

    def emit(self, event: Any) -> None:
        if self._boom:
            raise RuntimeError("receiver down")
        self.emitted.append(event)


# ───────────────────────────────── dark path ───────────────────────────────────


def test_start_is_noop_and_untouched_when_unconfigured() -> None:
    # Unconfigured → returns False before any session access.
    assert dispatch.emit_run_lineage_start(_ExplodingSession(), run_id=uuid.uuid4()) is False  # type: ignore[arg-type]


def test_terminal_is_noop_and_untouched_when_unconfigured() -> None:
    assert dispatch.emit_run_lineage_terminal(_ExplodingSession(), run_id=uuid.uuid4()) is False  # type: ignore[arg-type]


# ────────────────────────────── fail-open on emit ──────────────────────────────


@pytest.fixture
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENLINEAGE_URL", "http://127.0.0.1:1")
    emitter.reset_openlineage_client_cache()


def _seed_run(db: Any, *, with_asset: bool = True) -> Run:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:6]}@x.io")
    db.add(owner)
    db.flush()
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "a"},
        secret_ref="kv",
        created_by=owner.id,
    )
    db.add(conn)
    db.flush()
    asset = None
    if with_asset:
        asset = Asset(namespace="snowflake://a", name="DB.S.T", env="dev", connection_id=conn.id)
        db.add(asset)
        db.flush()
    suite = Suite(name="s", connection_id=conn.id, created_by=owner.id, target={"table": "T"})
    if asset is not None:
        suite.asset_id = asset.id
    db.add(suite)
    db.flush()
    run = Run(suite_id=suite.id, status="succeeded", asset_id=asset.id if asset else None)
    db.add(run)
    db.flush()
    check = Check(suite_id=suite.id, name="c", expectation_type="e", config={})
    db.add(check)
    db.flush()
    db.add(Result(run_id=run.id, check_id=check.id, status="fail"))
    db.commit()
    return run


def test_start_emits_when_configured(
    db_session: Any, _configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = _SpyClient()
    monkeypatch.setattr(emitter, "get_openlineage_client", lambda: spy)
    run = _seed_run(db_session)

    assert dispatch.emit_run_lineage_start(db_session, run_id=run.id) is True
    assert len(spy.emitted) == 1


def test_terminal_emits_when_configured(
    db_session: Any, _configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = _SpyClient()
    monkeypatch.setattr(emitter, "get_openlineage_client", lambda: spy)
    run = _seed_run(db_session)

    assert dispatch.emit_run_lineage_terminal(db_session, run_id=run.id) is True
    assert len(spy.emitted) == 1


def test_emit_exception_is_swallowed(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(emitter, "get_openlineage_client", lambda: _SpyClient(boom=True))
    run = _seed_run(db_session)

    # A broken receiver can't fail the run.
    assert dispatch.emit_run_lineage_start(db_session, run_id=run.id) is False
    assert dispatch.emit_run_lineage_terminal(db_session, run_id=run.id) is False


def test_missing_run_is_a_noop(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _SpyClient()
    monkeypatch.setattr(emitter, "get_openlineage_client", lambda: spy)

    assert dispatch.emit_run_lineage_start(db_session, run_id=uuid.uuid4()) is False
    assert dispatch.emit_run_lineage_terminal(db_session, run_id=uuid.uuid4()) is False
    assert spy.emitted == []


class _RunOnlySession:
    """Serves a Run but no Suite — the defensive missing-suite guard path."""

    def __init__(self, run: Run) -> None:
        self._run = run

    def get(self, model: type, _pk: Any) -> Any:
        return self._run if model is Run else None


def test_missing_suite_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _SpyClient()
    monkeypatch.setattr(emitter, "get_openlineage_client", lambda: spy)
    run = Run(id=uuid.uuid4(), suite_id=uuid.uuid4(), status="succeeded")
    session: Any = _RunOnlySession(run)

    assert dispatch.emit_run_lineage_start(session, run_id=run.id) is False
    assert dispatch.emit_run_lineage_terminal(session, run_id=run.id) is False
    assert spy.emitted == []
