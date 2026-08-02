"""Shared pytest fixtures."""

import os
from collections.abc import Callable, Iterator

# Set test-mode env vars BEFORE any backend.app.* import resolves. The auth
# module computes its mode at import time from settings; without these the
# TestClient lifespan would raise 'Auth not configured'.
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("AUTH_DEV_BYPASS", "true")
# Rate limiting off by default in the suite (#725): otherwise the whole test
# battery self-429s through the shared `ip:testclient` bucket when a compose
# Redis is up. The dedicated rate-limit tests opt back in per-test.
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

import pytest

from backend.app.alerting.registry import reset_result_publisher_cache
from backend.app.core import rate_limit, secrets
from backend.app.core.config import get_settings
from backend.app.services import otp_service


@pytest.fixture(autouse=True)
def _reset_caches() -> Iterator[None]:
    """Clear cached singletons between tests so settings + secret store + the
    result publisher rebuild."""
    get_settings.cache_clear()
    secrets.reset_secret_store_cache()
    reset_result_publisher_cache()
    rate_limit.reset_rate_limit_state()
    otp_service.reset_counter_state()
    yield
    get_settings.cache_clear()
    secrets.reset_secret_store_cache()
    reset_result_publisher_cache()
    rate_limit.reset_rate_limit_state()
    # The OTP per-email counter store is a module global like the rate limiter's
    # (#734/#1127). Left set, an injected in-memory store would silently carry
    # counts into unrelated tests — and left unset after a test injected one, a
    # later test would reach for a real Redis client on the sign-in path.
    otp_service.reset_counter_state()


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


# ---------------------------------------------------------------------------
# Local test-infra visibility (#977)
#
# Skipping when the compose stack is down is deliberate (resolution order above,
# point 3) — but it must never be QUIET. With the stack down a bare `pytest -q`
# reports "1642 passed, 943 skipped"; with it up, the same commit reports "2586
# passed, 1 skipped". About a third of the suite silently does not run, and the
# summary line reads as success either way.
#
# The per-test skip reasons are already good, but pytest only renders them under
# `-rs`, and a bare skip count is indistinguishable from the live-datasource
# tests that legitimately skip without credentials. Editors (VS Code, PyCharm)
# invoke pytest directly rather than scripts/test-backend.sh, so this is the
# common path, not an edge case.
#
# These hooks report the condition up front and again, loudly, at the end. They
# are visibility only: probes are cheap, failures are swallowed, and nothing here
# can fail a run. In CI both services are up, so the warning never fires.
# ---------------------------------------------------------------------------

# libpq silently clamps connect_timeout below 2, so 2 is the honest floor —
# advertising 1.5 would describe a budget the driver does not honour.
_PROBE_TIMEOUT_S = 2
_FIX_HINT = "docker compose up -d postgres redis  (or scripts/test-backend.sh)"


def _probe_postgres() -> str | None:
    """None when the test DB is reachable, else a short human reason."""
    if not TEST_DATABASE_URL:
        return "no TEST_DATABASE_URL and no local .env Postgres creds"
    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.engine import make_url
    except Exception:  # pragma: no cover - sqlalchemy is a hard dep
        return None  # can't probe → say nothing rather than cry wolf

    # `connect_timeout` is a libpq/psycopg option. Passing it to any other driver
    # raises at connect time, which would be swallowed below and reported as
    # "not reachable" — a false alarm about a perfectly healthy database. Only
    # attach it when the URL actually names a psycopg driver.
    connect_args: dict[str, object] = {}
    try:
        if make_url(TEST_DATABASE_URL).drivername.startswith("postgresql+psycopg"):
            connect_args["connect_timeout"] = _PROBE_TIMEOUT_S
    except Exception:
        return None  # unparseable URL — not our story to tell

    try:
        engine = create_engine(TEST_DATABASE_URL, future=True, connect_args=connect_args)
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        finally:
            engine.dispose()
    except TypeError:
        # Driver rejected our connect_args — the probe could not run. That is
        # NOT evidence the database is down, so never claim that it is.
        return None
    except Exception:
        return "not reachable"
    return None


def _probe_secret_store() -> tuple[str, str | None]:
    """(backend_name, reason_unavailable). Only the redis backend can be *down* —
    `env` is in-process and `azure_key_vault` is never used by the local suite."""
    try:
        settings = get_settings()
        mode = settings.secret_store
    except Exception:
        return ("unknown", None)
    if mode != "redis":
        return (mode, None)
    try:
        import redis

        client = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=_PROBE_TIMEOUT_S,
            socket_timeout=_PROBE_TIMEOUT_S,
        )
        try:
            client.ping()
        finally:
            client.close()
    except Exception:
        return (mode, "not reachable")
    return (mode, None)


_INFRA_STATUS_CACHE: list[tuple[str, str | None]] | None = None


def _infra_status() -> list[tuple[str, str | None]]:
    """[(label, reason_unavailable)] — probed once, cached for the session."""
    global _INFRA_STATUS_CACHE
    if _INFRA_STATUS_CACHE is None:
        store_mode, store_reason = _probe_secret_store()
        _INFRA_STATUS_CACHE = [
            ("postgres (test DB)", _probe_postgres()),
            (f"secret store ({store_mode})", store_reason),
        ]
    return _INFRA_STATUS_CACHE


def pytest_report_header() -> list[str]:
    """State infra reachability up front.

    A convenience for plain `pytest`, NOT the guarantee — pytest gates header
    output on `verbosity >= 0`, so `-q` discards these lines entirely. The
    terminal-summary banner below is what actually holds under `-q`.
    """
    lines = []
    for label, reason in _infra_status():
        lines.append(f"test infra: {label} — {'OK' if reason is None else reason.upper()}")
    if any(reason for _, reason in _infra_status()):
        lines.append(f"test infra: DEGRADED — see the banner at the end. Fix: {_FIX_HINT}")
    return lines


@pytest.hookimpl(trylast=True)
def pytest_terminal_summary(terminalreporter: object) -> None:
    """Re-state a degraded run immediately above the final summary line.

    This is the load-bearing half: `-q` suppresses the header entirely (pytest
    gates it on verbosity >= 0), and `-q` is exactly the invocation that hid the
    problem.

    `trylast=True` is REQUIRED, not tidiness. conftest hookimpls are registered
    last and therefore called FIRST, so without it this banner prints *above*
    pytest-cov's terminal summary — and `addopts` carries
    `--cov-report=term-missing`, whose table is ~140 lines on the full suite.
    The banner would scroll off exactly like the header it exists to back up.
    """
    down = [(label, reason) for label, reason in _infra_status() if reason]
    if not down:
        return
    stats = terminalreporter.stats  # type: ignore[attr-defined]
    skipped = len(stats.get("skipped", []))
    broke = len(stats.get("failed", [])) + len(stats.get("error", []))
    write = terminalreporter.write_line  # type: ignore[attr-defined]
    write("")
    write("!" * 79, red=True, bold=True)
    write("DEGRADED RUN — local test infrastructure was unavailable.", red=True, bold=True)
    for label, reason in down:
        write(f"  unavailable: {label} — {reason}", red=True)
    # Only state what actually happened: a missing Postgres makes DB-backed tests
    # SKIP, while a missing secret store makes them FAIL. Claiming skips on a run
    # that had none would be the exact defect this banner exists to prevent.
    if skipped:
        write(f"  {skipped} test(s) skipped — some of these need the missing service.", red=True)
    if broke:
        write(
            f"  {broke} test(s) failed/errored — likely for this reason, not a code defect.",
            red=True,
        )
    if not skipped and not broke:
        write(
            "  No tests skipped or failed, but coverage of this dependency was not exercised.",
            red=True,
        )
    write(f"  Fix: {_FIX_HINT}", red=True, bold=True)
    write("A pass here does not mean what a full local run means.", red=True)
    write("!" * 79, red=True, bold=True)


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
