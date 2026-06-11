"""Shared pytest fixtures."""

import os
from collections.abc import Iterator

# Set test-mode env vars BEFORE any backend.app.* import resolves. The auth
# module computes its mode at import time from settings; without these the
# TestClient lifespan would raise 'Auth not configured'.
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("AUTH_DEV_BYPASS", "true")

import pytest

from backend.app.core import secrets
from backend.app.core.config import get_settings


@pytest.fixture(autouse=True)
def _reset_caches() -> Iterator[None]:
    """Clear cached singletons between tests so settings + secret store rebuild."""
    get_settings.cache_clear()
    secrets.reset_secret_store_cache()
    yield
    get_settings.cache_clear()
    secrets.reset_secret_store_cache()


@pytest.fixture(autouse=True)
def stub_run_dispatch(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stub `run_dispatch.dispatch_run` so any code path that triggers a suite run
    (the pipeline-success ungate #215, the probe, manual runs) never publishes to
    a real broker.

    Returns the list of dispatched run-ids (as strings), so a test can assert
    dispatch happened. Tests that need bespoke dispatch behaviour (e.g. the
    broker-failure path) re-patch `run_dispatch.dispatch_run` themselves — their
    function-scoped patch is applied after this autouse fixture and so wins. The
    probe e2e test uses `apply_async` (a real publish), which this does not touch.

    `@pytest.mark.real_dispatch` opts out entirely — for tests of `dispatch_run`
    itself, which spy `celery_app.send_task` instead.
    """
    calls: list[str] = []
    if request.node.get_closest_marker("real_dispatch") is None:
        from backend.app.services import run_dispatch

        monkeypatch.setattr(run_dispatch, "dispatch_run", lambda run_id: calls.append(str(run_id)))
    return calls


@pytest.fixture
def clean_kv_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every KV_SECRET_* env var so tests start from a clean slate."""
    import os

    for key in list(os.environ):
        if key.startswith("KV_SECRET_"):
            monkeypatch.delenv(key, raising=False)


# ── DB-backed test support ────────────────────────────────────────────────────
# DB integration tests require a real Postgres (the models use JSONB / UUID /
# gen_random_uuid(), which SQLite can't host). Set TEST_DATABASE_URL to enable
# them; without it the db_session fixture skips, so `pytest` still runs the
# pure-unit suite anywhere. CI provides an ephemeral Postgres service.

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def _db_engine() -> "Iterator[object]":
    from sqlalchemy import create_engine, text

    import backend.app.db.models  # noqa: F401 — registers tables on Base.metadata
    from backend.app.db.base import Base

    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set; skipping DB-backed tests")

    engine = create_engine(TEST_DATABASE_URL, future=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # pragma: no cover - environment-dependent
        engine.dispose()
        pytest.skip(f"TEST_DATABASE_URL not reachable: {TEST_DATABASE_URL}")

    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db_session(_db_engine: object) -> "Iterator[object]":
    """A transactional Session rolled back after each test for isolation.

    join_transaction_mode="create_savepoint" lets code under test call
    commit() freely — those commits land on a savepoint inside the outer
    transaction, which is rolled back here, so tests never persist.
    """
    from sqlalchemy.orm import Session as SASession

    connection = _db_engine.connect()  # type: ignore[attr-defined]
    trans = connection.begin()
    session = SASession(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()
