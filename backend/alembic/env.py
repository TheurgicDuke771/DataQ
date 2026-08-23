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
_MIGRATION_LOCK_TIMEOUT_MS = 15_000

# Same gap #1102 closed on the app engine (`backend/app/db/session.py`).
_MIGRATION_CONNECT_TIMEOUT_SECONDS = 10

# Same gap #1221 closed on the app engine, mirrored here for the same reason
# _MIGRATION_CONNECT_TIMEOUT_SECONDS mirrors #1102: `connect_timeout` only bounds the initial
_MIGRATION_KEEPALIVES_IDLE_SECONDS = 30
_MIGRATION_KEEPALIVES_INTERVAL_SECONDS = 10
_MIGRATION_KEEPALIVES_COUNT = 3


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={
            # `options` (the lock_timeout GUC) is NOT portable across drivers.
            **psycopg_connect_args(
                get_settings().database_url,
                options=f"-c lock_timeout={int(_MIGRATION_LOCK_TIMEOUT_MS)}",
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
            # `op.get_context().autocommit_block()` (the supported way to step out for a statement)
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
