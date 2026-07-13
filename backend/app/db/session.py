from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import get_settings

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


def _build_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        future=True,
        connect_args={"options": f"-c lock_timeout={int(_LOCK_TIMEOUT_MS)}"},
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
