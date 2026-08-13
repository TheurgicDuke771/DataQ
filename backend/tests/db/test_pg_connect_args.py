"""Unit tests for the shared psycopg driver guard (#1266).

`connect_timeout` and the `keepalives*` family are libpq connection parameters that
only psycopg2 and psycopg (psycopg3) — both libpq-backed — accept. Any other
SQLAlchemy driver's `connect()` raises `TypeError` on them, and `Settings.database_url`
has no scheme/driver validator (env-overridable via `DATABASE_URL`), so
`backend/app/db/pg_connect_args.py`'s `psycopg_connect_args` is the guard both
`backend/app/db/session.py` and `backend/alembic/env.py` route these keys through
instead of passing them unconditionally. Mirrors the driver check
`backend/tests/conftest.py`'s `_probe_postgres` already uses for the same reason.
"""

from backend.app.db.pg_connect_args import psycopg_connect_args


def test_psycopg2_driver_keeps_the_driver_only_kwargs() -> None:
    result = psycopg_connect_args(
        "postgresql+psycopg2://user:pw@localhost:5432/dataq",
        connect_timeout=10,
        keepalives=1,
    )
    assert result == {"connect_timeout": 10, "keepalives": 1}


def test_psycopg3_driver_keeps_the_driver_only_kwargs() -> None:
    """`postgresql+psycopg` (no trailing `2`) is psycopg3 — also libpq-backed, so
    the same libpq connect params apply. The guard classifies by the resolved
    dialect driver (`"psycopg"`), which is in the explicit psycopg-family allowlist,
    precisely so this driver name is covered too."""
    result = psycopg_connect_args(
        "postgresql+psycopg://user:pw@localhost:5432/dataq",
        connect_timeout=10,
        keepalives=1,
    )
    assert result == {"connect_timeout": 10, "keepalives": 1}


def test_bare_postgresql_scheme_is_treated_as_psycopg() -> None:
    """A bare `postgresql://` URL resolves to SQLAlchemy's own DEFAULT DBAPI choice
    at `create_engine` time — and that default IS psycopg2
    (`make_url(...).get_dialect().driver == "psycopg2"`). A prefix check on
    `drivername` (the pre-fix bug) misses this because `drivername` itself is just
    `"postgresql"`, with no `+psycopg2` suffix — silently dropping
    `connect_timeout`/`keepalives*` for the single most common `DATABASE_URL` shape
    and reintroducing the #1102/#1221 stale-connection-hang regressions."""
    result = psycopg_connect_args(
        "postgresql://user:pw@localhost:5432/dataq",
        connect_timeout=10,
        keepalives=1,
    )
    assert result == {"connect_timeout": 10, "keepalives": 1}


def test_pg8000_driver_drops_the_driver_only_kwargs() -> None:
    """pg8000 is a pure-Python driver, not libpq-backed — it does not understand
    `connect_timeout`/`keepalives*` as libpq connect params."""
    result = psycopg_connect_args(
        "postgresql+pg8000://user:pw@localhost:5432/dataq",
        connect_timeout=10,
        keepalives=1,
    )
    assert result == {}


def test_non_psycopg_driver_drops_the_driver_only_kwargs() -> None:
    """The #1266 case: if `database_url` ever resolved to a non-psycopg driver (no
    validator stops it, and it's env-overridable), the psycopg-only kwargs must be
    dropped instead of reaching `create_engine`/`engine_from_config` and raising
    `TypeError` on the first real connection attempt."""
    result = psycopg_connect_args(
        "postgresql+asyncpg://user:pw@localhost:5432/dataq",
        connect_timeout=10,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=3,
    )
    assert result == {}


def test_unparseable_url_drops_the_driver_only_kwargs_rather_than_raising() -> None:
    """An unparseable URL isn't this guard's story to tell — `create_engine` itself
    will raise on it soon enough, with a clearer error. The guard must not itself
    crash `_build_engine()`/`run_migrations_online()` before that point."""
    result = psycopg_connect_args("not a valid url at all", connect_timeout=10)
    assert result == {}


def test_options_is_guarded_exactly_like_connect_timeout_and_keepalives() -> None:
    """`options` (the lock_timeout GUC, `-c lock_timeout=...`) is NOT portable across
    every driver — asyncpg has no `options` connect kwarg at all (it uses
    `server_settings` instead) — so it must flow through this same guard as a
    driver-only kwarg, not stay an unconditional dict key at the call site. Kept for a
    psycopg driver, dropped for a non-psycopg one, exactly like `connect_timeout`."""
    psycopg_result = psycopg_connect_args(
        "postgresql+psycopg2://user:pw@localhost:5432/dataq",
        options="-c lock_timeout=5000",
        connect_timeout=10,
    )
    assert psycopg_result == {"options": "-c lock_timeout=5000", "connect_timeout": 10}

    non_psycopg_result = psycopg_connect_args(
        "postgresql+asyncpg://user:pw@localhost:5432/dataq",
        options="-c lock_timeout=5000",
        connect_timeout=10,
    )
    assert non_psycopg_result == {}


def test_no_driver_only_kwargs_passed_is_still_a_no_op_dict() -> None:
    """Passing zero driver-only kwargs (e.g. a future call site with nothing
    psycopg-specific to guard) is a harmless empty dict either way."""
    assert psycopg_connect_args("postgresql+psycopg2://localhost:5432/dataq") == {}
    assert psycopg_connect_args("postgresql+asyncpg://localhost:5432/dataq") == {}
