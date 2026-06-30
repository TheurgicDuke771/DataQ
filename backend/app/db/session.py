from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import get_settings


def _build_engine() -> Engine:
    settings = get_settings()
    return create_engine(settings.database_url, pool_pre_ping=True, future=True)


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
