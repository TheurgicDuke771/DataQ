from logging.config import fileConfig

from backend.app.core.config import get_settings
from backend.app.db import models  # noqa: F401  (register models on Base.metadata)
from backend.app.db.base import Base
from backend.app.db.pg_connect_args import psycopg_connect_args
from sqlalchemy import engine_from_config, pool

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# A migration must never wait forever for a lock (#753 migration-safety audit).
# `session.py` set this on the APP engine after #854 — an unbounded lock wait took
# production down — but alembic builds its own engine here, so the migrate job was
# still exposed: `dataq-app-migrate` runs BEFORE the api/worker roll, while the old
# containers' beat is still writing every 10 minutes, so any DDL taking ACCESS
# EXCLUSIVE can collide with an in-flight write. Unbounded, that collision hangs the
# deploy (and holds locks while it hangs); bounded, it fails fast, visibly, and
# retryably. Set on the ENGINE for the same reason session.py does — a per-migration
# `SET LOCAL` leaves the next migration exposed.
#
# Longer than the app's 5s: a migration is rarer, more important, and legitimately
# may wait out a short transaction rather than fail a deploy on a brush.
_MIGRATION_LOCK_TIMEOUT_MS = 15_000

# Same gap #1102 closed on the app engine (`backend/app/db/session.py`), on the OTHER
# engine that builds its own `connect_args`: `lock_timeout` only bounds a statement
# waiting on a contended row once connected, not the initial TCP connect. Unbounded, an
# unreachable DB at migrate time (the `dataq-app-migrate` job runs before the api/worker
# roll) would hang the deploy on the OS/driver connect default instead of failing fast.
# Same 10s value as the app engine — reachability has no reason to differ by caller.
_MIGRATION_CONNECT_TIMEOUT_SECONDS = 10

# Same gap #1221 closed on the app engine, mirrored here for the same reason
# _MIGRATION_CONNECT_TIMEOUT_SECONDS mirrors #1102: `connect_timeout` only bounds the
# initial connect, not a connection that's already open and running a migration
# statement when a network partition happens silently (route drops, no TCP RST).
# `NullPool` means this engine never reuses a connection across migrations, but it
# still holds ONE connection open for the whole migration's duration — long enough for
# a mid-migration partition to leave a read hanging forever with no timeout, again
# blocking the whole deploy. TCP keepalives make the OS detect and reap a dead socket
# instead. Same values as the app engine (`backend/app/db/session.py`) — detection
# latency has no reason to differ by caller.
_MIGRATION_KEEPALIVES_IDLE_SECONDS = 30
_MIGRATION_KEEPALIVES_INTERVAL_SECONDS = 10
_MIGRATION_KEEPALIVES_COUNT = 3


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={
            # Portable across drivers — see the mirrored comment in
            # `backend/app/db/session.py`'s `_build_engine()`.
            "options": f"-c lock_timeout={int(_MIGRATION_LOCK_TIMEOUT_MS)}",
            # `connect_timeout`/`keepalives*` are psycopg-only libpq params (#1266):
            # `database_url` has no driver validator and is env-overridable, so this
            # guard degrades to {} instead of `engine_from_config` raising
            # `TypeError` on the first real connect if it ever resolves to a
            # non-psycopg driver. Shared with `session.py`'s app engine so the two
            # don't drift.
            **psycopg_connect_args(
                get_settings().database_url,
                connect_timeout=_MIGRATION_CONNECT_TIMEOUT_SECONDS,
                keepalives=1,
                keepalives_idle=_MIGRATION_KEEPALIVES_IDLE_SECONDS,
                keepalives_interval=_MIGRATION_KEEPALIVES_INTERVAL_SECONDS,
                keepalives_count=_MIGRATION_KEEPALIVES_COUNT,
            ),
        },
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # Each migration gets its own transaction, so one revision using
            # `op.get_context().autocommit_block()` (the supported way to step out
            # for a statement) does not drag the rest of the chain with it.
            # Postgres refuses
            # `CREATE INDEX CONCURRENTLY` inside a transaction block, and a plain
            # `CREATE INDEX` takes a lock that blocks writes on the table for its
            # whole duration — which on a hot table is the #748 incident again,
            # just self-inflicted. Without this hook the only options are "lock the
            # table" or "don't add the index".
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
