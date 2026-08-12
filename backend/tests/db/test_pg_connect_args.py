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
    the same libpq connect params apply. The guard matches by prefix, not an exact
    string, precisely so this driver name is covered too."""
    result = psycopg_connect_args(
        "postgresql+psycopg://user:pw@localhost:5432/dataq",
        connect_timeout=10,
        keepalives=1,
    )
    assert result == {"connect_timeout": 10, "keepalives": 1}


def test_bare_postgresql_scheme_is_not_treated_as_psycopg() -> None:
    """A bare `postgresql://` URL resolves to SQLAlchemy's own default DBAPI choice
    at `create_engine` time, which this helper cannot know in advance — so it must
    not assume psycopg and pass the libpq-only kwargs through."""
    result = psycopg_connect_args(
        "postgresql://user:pw@localhost:5432/dataq",
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


def test_no_driver_only_kwargs_passed_is_still_a_no_op_dict() -> None:
    """Passing zero driver-only kwargs (e.g. a future call site with nothing
    psycopg-specific to guard) is a harmless empty dict either way."""
    assert psycopg_connect_args("postgresql+psycopg2://localhost:5432/dataq") == {}
    assert psycopg_connect_args("postgresql+asyncpg://localhost:5432/dataq") == {}
