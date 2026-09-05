"""A real, single-use database for tests the shared fixture cannot express.

`db_session` reads through its own uncommitted transaction, so it cannot answer
"did this commit?", and it runs as an owner, so it cannot answer "does this
`REVOKE` hold?". Four tests grew their own copy of this create-use-drop dance;
this is the one copy.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import Table, create_engine, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.models import Base
from backend.tests.conftest import TEST_DATABASE_URL


def _admin_engine() -> Any:
    return create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")


@contextmanager
def scratch_engine(name: str, *, tables: list[Table] | None = None) -> Iterator[Any]:
    """Create `name`, yield an engine bound to it, drop it afterwards.

    `tables` limits what is created; omit it for the whole schema.
    """
    if not TEST_DATABASE_URL:
        pytest.skip("needs TEST_DATABASE_URL")
    admin = _admin_engine()
    try:
        with admin.connect() as conn:
            try:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
                conn.execute(text(f'CREATE DATABASE "{name}"'))
            except ProgrammingError as exc:  # pragma: no cover — permission-dependent
                pytest.skip(f"cannot create a scratch database: {exc}")
    finally:
        admin.dispose()

    engine = create_engine(TEST_DATABASE_URL.rsplit("/", 1)[0] + f"/{name}")
    try:
        Base.metadata.create_all(engine, tables=tables)
        yield engine
    finally:
        engine.dispose()
        admin = _admin_engine()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


@contextmanager
def scratch_session(name: str, *, tables: list[Table] | None = None) -> Iterator[Session]:
    """`scratch_engine`, already opened as one session."""
    with scratch_engine(name, tables=tables) as engine:
        session = sessionmaker(bind=engine)()
        try:
            yield session
        finally:
            session.close()
