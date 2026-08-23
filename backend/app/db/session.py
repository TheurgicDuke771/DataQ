from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import get_settings
from backend.app.db.pg_connect_args import psycopg_connect_args

# No statement waits forever for a LOCK.
_LOCK_TIMEOUT_MS = 5_000

# Same posture, one layer lower (#1102): `lock_timeout` bounds how long a statement waits on a
# CONTENDED row once connected — it says nothing about the initial TCP connect.
_CONNECT_TIMEOUT_SECONDS = 10

# One layer lower still (#1221, follow-up to #1102's review): `connect_timeout` only bounds
# establishing a BRAND-NEW connection.
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
            # `options` (the lock_timeout GUC) is NOT portable across drivers.
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
        # Explicit rollback so a failed request can't leave a poisoned transaction for the (pooled)
        # connection's next user.
        db.rollback()
        raise
    finally:
        db.close()
