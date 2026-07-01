"""Shared pytest fixtures."""

import os
from collections.abc import Callable, Iterator

# Set test-mode env vars BEFORE any backend.app.* import resolves. The auth
# module computes its mode at import time from settings; without these the
# TestClient lifespan would raise 'Auth not configured'.
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("AUTH_DEV_BYPASS", "true")

import pytest

from backend.app.alerting.registry import reset_result_publisher_cache
from backend.app.core import secrets
from backend.app.core.config import get_settings


@pytest.fixture(autouse=True)
def _reset_caches() -> Iterator[None]:
    """Clear cached singletons between tests so settings + secret store + the
    result publisher rebuild."""
    get_settings.cache_clear()
    secrets.reset_secret_store_cache()
    reset_result_publisher_cache()
    yield
    get_settings.cache_clear()
    secrets.reset_secret_store_cache()
    reset_result_publisher_cache()


@pytest.fixture
def make_workspace_admin(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Return a callable that puts the given emails in WORKSPACE_ADMIN_EMAILS for
    the current test (making those users workspace-admins). The autouse
    `_reset_caches` fixture clears the cached Settings afterwards."""

    def _make(*emails: str) -> None:
        monkeypatch.setenv("WORKSPACE_ADMIN_EMAILS", ",".join(emails))
        get_settings.cache_clear()

    return _make


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

        def _fake_dispatch(run_id: object) -> str:
            calls.append(str(run_id))
            return f"task-{run_id}"  # the captured celery_task_id

        monkeypatch.setattr(run_dispatch, "dispatch_run", _fake_dispatch)
        # revoke goes to the broker (control bus); no-op it so cancel tests don't
        # need a live Celery.
        monkeypatch.setattr(run_dispatch, "revoke_run", lambda task_id: None)
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
# gen_random_uuid(), which SQLite can't host).
#
# Resolution order for the test DB:
#   1. TEST_DATABASE_URL if set explicitly (this is what CI does).
#   2. Otherwise, the docker-compose Postgres using the .env creds, on a dedicated
#      `dataq_test` database (auto-created if missing). This is what makes a plain
#      `pytest` — including editors like VS Code / PyCharm whose test runners invoke
#      pytest directly, NOT via scripts/test-backend.sh — run the DB-backed tests
#      instead of skipping, whenever the local Postgres is up.
#   3. Neither available → the db_session fixture skips, so `pytest` still runs the
#      pure-unit suite anywhere.


def _read_env_file() -> dict[str, str]:
    """Best-effort parse of the gitignored repo-root .env (the POSTGRES_* creds that
    docker-compose + scripts/setup.sh use). Returns {} if it's absent."""
    from pathlib import Path

    env: dict[str, str] = {}
    path = Path(__file__).resolve().parents[2] / ".env"
    if path.exists():
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def _resolve_test_database_url() -> str | None:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit
    env = _read_env_file()
    user, password = env.get("POSTGRES_USER"), env.get("POSTGRES_PASSWORD")
    if not (user and password):
        return None
    return f"postgresql+psycopg2://{user}:{password}@localhost:5432/dataq_test"


def _ensure_local_test_database() -> None:
    """When we defaulted to the local `dataq_test` DB (TEST_DATABASE_URL unset),
    create it if missing — so a direct `pytest` works with only the compose Postgres
    up, no manual createdb. No-op when TEST_DATABASE_URL is set explicitly (CI: the
    DB is provisioned by the workflow)."""
    if os.environ.get("TEST_DATABASE_URL"):
        return
    env = _read_env_file()
    user, password, admin_db = (
        env.get("POSTGRES_USER"),
        env.get("POSTGRES_PASSWORD"),
        env.get("POSTGRES_DB"),
    )
    if not (user and password and admin_db):
        return
    from sqlalchemy import create_engine, text

    admin_url = f"postgresql+psycopg2://{user}:{password}@localhost:5432/{admin_db}"
    try:
        # AUTOCOMMIT: CREATE DATABASE can't run inside a transaction.
        admin = create_engine(admin_url, future=True, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = 'dataq_test'")
            ).scalar()
            if not exists:
                conn.execute(text("CREATE DATABASE dataq_test"))
        admin.dispose()
    except Exception:  # pragma: no cover - environment-dependent
        pass  # Postgres down / no perms — _db_engine's connect below skips cleanly.


TEST_DATABASE_URL = _resolve_test_database_url()
# If we defaulted to the local compose Postgres, export it so per-test skipif guards
# that read os.environ['TEST_DATABASE_URL'] directly (e.g. the custom-SQL GX tests)
# also run — not only the db_session fixture. setdefault → CI's explicit value wins.
# We deliberately do NOT set DATABASE_URL / REDIS_URL here, so the real-infra E2E
# test (needs a live broker + worker) stays opt-in.
if TEST_DATABASE_URL:
    os.environ.setdefault("TEST_DATABASE_URL", TEST_DATABASE_URL)


@pytest.fixture(scope="session")
def _db_engine() -> "Iterator[object]":
    from sqlalchemy import create_engine, text

    import backend.app.db.models  # noqa: F401 — registers tables on Base.metadata
    from backend.app.db.base import Base

    if not TEST_DATABASE_URL:
        pytest.skip(
            "No TEST_DATABASE_URL and no local .env Postgres creds — "
            "run scripts/test-backend.sh (or `docker compose up -d postgres redis`)."
        )

    _ensure_local_test_database()

    engine = create_engine(TEST_DATABASE_URL, future=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # pragma: no cover - environment-dependent
        engine.dispose()
        pytest.skip(
            "Local Postgres not reachable — start it with "
            "`docker compose up -d postgres redis` (or scripts/test-backend.sh)."
        )

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
