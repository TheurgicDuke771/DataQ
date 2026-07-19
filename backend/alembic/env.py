from logging.config import fileConfig

from backend.app.core.config import get_settings
from backend.app.db import models  # noqa: F401  (register models on Base.metadata)
from backend.app.db.base import Base
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


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"options": f"-c lock_timeout={int(_MIGRATION_LOCK_TIMEOUT_MS)}"},
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
