from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import get_settings
from backend.app.db.pg_connect_args import psycopg_connect_args

# No statement waits forever for a LOCK. Postgres' default (`lock_timeout = 0`) means
# "block indefinitely", and that default took production down (#854): one contended
# `connections` row hung a poll task, the hung task wedged the Celery worker's prefork
# child, and a wedged pool silently stopped EVERY periodic task — orchestration polling,
# scheduled-suite dispatch, gap recovery, the purge. The container reported Healthy
# throughout and raised nothing; only the database told the truth.
#
# Set on the ENGINE, not at the call site, deliberately (#855 review): the defect was
# never "these two functions lock a row" — it was that anything sharing the beat can block
# forever and take everything down with it. A per-callsite guard leaves that property
# intact for the next `with_for_update` someone adds. This makes the whole class
# impossible, and a blocked lock now fails fast and loudly instead.
#
# Deliberately NOT `statement_timeout`: a long-running query is legitimate here (GX
# profiling, large batch reads), so capping every statement would break real work. Waiting
# minutes for a *lock*, by contrast, is never legitimate — it means someone else is
# holding the row and we should say so, not hang.
_LOCK_TIMEOUT_MS = 5_000

# Same posture, one layer lower (#1102): `lock_timeout` bounds how long a statement waits
# on a CONTENDED row once connected — it says nothing about the initial TCP connect. If the
# DB is fully unreachable (network partition, not a locked row), psycopg2's connect falls
# back to the OS/driver default, which can block for minutes. That hangs every
# `get_session()` caller, including the #1052 staleness loop's graceful-shutdown await
# (`staleness_stop.set(); await staleness_task`) — an unreachable DB would delay API
# shutdown by the same unbounded amount. `connect_timeout` is a psycopg2-native
# `connect_args` key (seconds, unlike `lock_timeout`'s milliseconds GUC), so a dead DB
# fails fast and loudly instead of hanging every caller.
_CONNECT_TIMEOUT_SECONDS = 10

# One layer lower still (#1221, follow-up to #1102's review): `connect_timeout` only
# bounds establishing a BRAND-NEW connection. It does nothing for a connection that was
# already open and pooled when a network partition happens LATER — route drops silently,
# no TCP RST. `pool_pre_ping=True` then checks out that already-connected pooled socket
# and issues `SELECT 1` on it; with no data ever arriving, that read can hang
# indefinitely, including inside the same #1052 graceful-shutdown await #1102 was
# protecting. TCP keepalives make the OS detect and reap a dead socket instead of
# hanging a read forever. Deliberately NOT `statement_timeout` (see the module-level
# comment above `_LOCK_TIMEOUT_MS`): a long-running query is legitimate here.
#
# `keepalives_idle` (30s) + `keepalives_interval` (10s) * `keepalives_count` (3) bounds
# worst-case detection at ~60s after the last successful exchange — an OS-level floor
# under `pool_pre_ping`'s own read, not a substitute for it. These are psycopg2-native
# `connect_args` keys (like `connect_timeout`), passed straight to libpq.
#
# NOT verified against a live black-holed connection (firewall rule dropping packets
# silently) — that crosses the same "only a live/real-network test is evidence" class as
# the driver-boundary lesson in CLAUDE.md, and this environment has no way to simulate
# it. What's tested is that the configuration reaches the engine; see
# `test_poll_lock_timeout.py`.
_KEEPALIVES_IDLE_SECONDS = 30
_KEEPALIVES_INTERVAL_SECONDS = 10
_KEEPALIVES_COUNT = 3


def _build_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        future=True,
        connect_args={
            # `options` (the lock_timeout GUC) is NOT portable across drivers, despite
            # what an earlier version of this comment claimed: asyncpg and pg8000 both
            # have fixed `connect()` keyword signatures with no `options` parameter
            # (asyncpg uses an entirely different `server_settings` mechanism), so it
            # needs the same #1266 driver guard as `connect_timeout`/`keepalives*`
            # below — otherwise a non-psycopg driver would still hit `TypeError` on
            # the first real connect, just on a different kwarg, defeating the guard's
            # whole purpose. `database_url` has no driver validator and is
            # env-overridable, so this guard degrades to {} instead of `create_engine`
            # raising `TypeError` if it ever resolves to a non-psycopg driver.
            **psycopg_connect_args(
                settings.database_url,
                options=f"-c lock_timeout={int(_LOCK_TIMEOUT_MS)}",
                connect_timeout=_CONNECT_TIMEOUT_SECONDS,
                keepalives=1,
                keepalives_idle=_KEEPALIVES_IDLE_SECONDS,
                keepalives_interval=_KEEPALIVES_INTERVAL_SECONDS,
                keepalives_count=_KEEPALIVES_COUNT,
            ),
        },
    )


engine: Engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_session() -> Session:
    return SessionLocal()


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    except Exception:
        # Explicit rollback so a failed request can't leave a poisoned transaction
        # for the (pooled) connection's next user. `close()` rolls back implicitly,
        # but being explicit documents the intent and is the read-modify-write
        # convention (see CONTRIBUTING).
        db.rollback()
        raise
    finally:
        db.close()
