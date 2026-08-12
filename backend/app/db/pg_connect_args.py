"""Shared driver guard for psycopg-only `connect_args` keys (#1266).

`connect_timeout` and the `keepalives*` family (`keepalives`, `keepalives_idle`,
`keepalives_interval`, `keepalives_count`) are libpq connection parameters, not
portable SQLAlchemy `connect_args` — the psycopg2 and psycopg (psycopg3) drivers
both pass them straight through to libpq, but any other driver's `connect()`
raises `TypeError` on the unrecognized kwarg. That raise happens at the FIRST REAL
CONNECTION ATTEMPT, not at `create_engine()`/`engine_from_config()` call time — so
it would surface as an opaque crash deep in app/worker/migrate-job startup, not a
clear config error.

`Settings.database_url` has no scheme/driver validator and is env-overridable via
`DATABASE_URL`, so both real engine-building call sites —
`backend/app/db/session.py`'s `_build_engine()` and `backend/alembic/env.py`'s
`run_migrations_online()` — need this guard, not just one of them. This mirrors
the driver check `backend/tests/conftest.py`'s `_probe_postgres` already uses for
the identical reason (guarding its own, narrower, `connect_timeout`-only probe).
"""

from sqlalchemy.engine import make_url


def psycopg_connect_args(database_url: str, **driver_only_args: object) -> dict[str, object]:
    """Return `driver_only_args` unchanged when `database_url` resolves to a
    psycopg-family SQLAlchemy driver (`postgresql+psycopg2` or the psycopg3
    `postgresql+psycopg`), else `{}`.

    A non-psycopg driver then degrades to no keepalives/connect_timeout instead of
    `create_engine()`/`engine_from_config()` raising `TypeError` on the first real
    connection attempt.
    """
    try:
        is_psycopg = make_url(database_url).drivername.startswith("postgresql+psycopg")
    except Exception:
        # An unparseable URL isn't this guard's story to tell — `create_engine`
        # itself raises on it soon enough, with a clearer error than we'd add here.
        is_psycopg = False
    return dict(driver_only_args) if is_psycopg else {}
