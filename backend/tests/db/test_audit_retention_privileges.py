"""The retention sweep versus the migration's own `REVOKE` — ADR 0041 §2.7 (#1318).

**This file exists because the ordinary test database cannot see the bug.** The
suite connects as a Postgres **superuser**, and a superuser bypasses ACL checks
entirely — so `REVOKE DELETE` has no effect there, and a sweep that forgot to
re-grant would pass every test while failing every night in production. That is
the "only live is evidence" class the project has hit repeatedly, arriving as a
*privilege* rather than a driver type.

So this builds the real shape: a **non-superuser role** that **owns** the table,
exactly as production does (the deployed stack creates the database `OWNER
dataq_app` and hands the migrate job the same `DATABASE_URL` as the api and
worker, so Alembic creates `audit_events` owned by `dataq_app`).

Three properties are asserted, and the middle one is the whole point:

1. With `DELETE` revoked and no re-grant, the delete really is refused — i.e. the
   guard the migration installs is load-bearing, not decorative.
2. The sweep nonetheless deletes, because it re-grants around its own statement.
3. `DELETE` is revoked **again** afterwards, including when the sweep raises. A
   sweep that left the privilege granted would silently disable the guard for
   every later request on that connection — a worse outcome than not sweeping.

Needs a Postgres role able to `CREATE ROLE`/`CREATE DATABASE`; skips otherwise.
"""

from __future__ import annotations

import importlib.util
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import Table, create_engine, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.models import AuditEvent, Base, User
from backend.app.services import audit_read_service
from backend.tests.conftest import TEST_DATABASE_URL

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "ecda713656ac_add_audit_events.py"
)
_ROLE = "dataq_audit_retention_probe"
_DB = "dataq_audit_retention_probe"
# Test-only, never a real credential: this role exists for the length of one test
# on a throwaway database, and is dropped in teardown. It is generated rather than
# written down so nothing resembling a password is committed.
_PASSWORD = uuid.uuid4().hex


def _revoke_statement(role: str) -> str:
    """The migration's OWN statement, loaded from the migration.

    Not a copy. A second hand-written copy of the only thing standing between the
    audit log and a silent rewrite is the "guard at one door and not its sibling"
    shape, and that divergence is invisible until it matters.
    """
    spec = importlib.util.spec_from_file_location("_audit_migration_for_privs", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.revoke_statement(role))


@pytest.fixture
def owner_session() -> Iterator[Session]:
    """A session connected as a NON-superuser role that owns `audit_events`."""
    if not TEST_DATABASE_URL:
        pytest.skip("needs TEST_DATABASE_URL")

    admin = create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            try:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{_DB}"'))
                conn.execute(text(f'DROP ROLE IF EXISTS "{_ROLE}"'))
                conn.execute(text(f"CREATE ROLE \"{_ROLE}\" LOGIN PASSWORD '{_PASSWORD}'"))
                conn.execute(text(f'CREATE DATABASE "{_DB}" OWNER "{_ROLE}"'))
            except ProgrammingError as exc:  # pragma: no cover - permission-dependent
                pytest.skip(f"cannot create a probe role/database: {exc}")
    finally:
        admin.dispose()

    url = TEST_DATABASE_URL.rsplit("/", 1)[0].split("://")[0]
    host = TEST_DATABASE_URL.rsplit("@", 1)[-1].rsplit("/", 1)[0]
    owner_url = f"{url}://{_ROLE}:{_PASSWORD}@{host}/{_DB}"
    engine = create_engine(owner_url)
    try:
        # Created BY the probe role, so the role owns them — the production shape.
        # `__table__` is typed `FromClause` on a declarative class while
        # `create_all` wants `Table`. It IS a `Table` at runtime; the cast is
        # purely to narrow it for mypy.
        tables = [cast(Table, User.__table__), cast(Table, AuditEvent.__table__)]
        Base.metadata.create_all(engine, tables=tables)
        session = sessionmaker(bind=engine)()
        try:
            yield session
        finally:
            session.close()
    finally:
        engine.dispose()
        admin = create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{_DB}"'))
            conn.execute(text(f'DROP ROLE IF EXISTS "{_ROLE}"'))
        admin.dispose()


def _seed(session: Session, *, age_days: int, count: int = 3) -> None:
    occurred = datetime.now(UTC) - timedelta(days=age_days)
    for i in range(count):
        session.add(
            AuditEvent(
                occurred_at=occurred,
                action_class="config",
                action=f"check.update.{i}",
                entity_type="check",
                entity_id=uuid.uuid4(),
                actor_kind="user",
            )
        )
    session.commit()


def _has_delete(session: Session) -> bool:
    return bool(
        session.execute(
            text("SELECT has_table_privilege(CURRENT_USER, 'audit_events', 'DELETE')")
        ).scalar()
    )


def test_the_revoke_really_blocks_a_delete_for_this_role(owner_session: Session) -> None:
    """Property 1 — the precondition every other assertion here depends on.

    Without this, the two tests below would pass against a role that never had
    the privilege revoked, and would be proving nothing at all. This is the check
    the ordinary suite cannot make, because its role is a superuser.
    """
    _seed(owner_session, age_days=500)
    owner_session.execute(text(_revoke_statement(_ROLE)))
    owner_session.commit()

    assert _has_delete(owner_session) is False
    with pytest.raises(ProgrammingError):
        owner_session.execute(text("DELETE FROM audit_events"))
    owner_session.rollback()


def test_the_sweep_deletes_despite_the_revoke(owner_session: Session) -> None:
    """Property 2 — ADR 0041 §2.7 requires the sweep to run as this very role, and
    the migration revokes DELETE from it. The sweep re-grants around its own
    statement."""
    _seed(owner_session, age_days=500)
    _seed(owner_session, age_days=1)
    owner_session.execute(text(_revoke_statement(_ROLE)))
    owner_session.commit()

    deleted = audit_read_service.purge_expired_events(owner_session, retention_days=365)

    assert deleted == 3
    remaining = owner_session.scalars(AuditEvent.__table__.select()).all()
    assert len(remaining) == 3, "the recent events must survive — this is a cutoff, not a wipe"


def test_the_sweep_leaves_the_guard_back_in_place(owner_session: Session, monkeypatch: Any) -> None:
    """Property 3 — and the one most likely to rot silently.

    A sweep that left DELETE granted would disable the guard for every later
    request on that connection while still passing a "did it delete?" test. Also
    asserted for the failure path: the re-revoke is in a `finally`, because a
    sweep that raises mid-batch must not leave the privilege behind.
    """
    _seed(owner_session, age_days=500)
    owner_session.execute(text(_revoke_statement(_ROLE)))
    owner_session.commit()

    audit_read_service.purge_expired_events(owner_session, retention_days=365)
    assert _has_delete(owner_session) is False

    # And on the failure path: force the sweep to raise mid-batch. The re-revoke
    # lives in a `finally` precisely so a crash cannot leave the privilege behind,
    # and that is the branch a happy-path test never reaches.
    _seed(owner_session, age_days=500)

    def _boom(*_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError("batch exploded")

    monkeypatch.setattr(audit_read_service, "chunked_dml", _boom)
    with pytest.raises(RuntimeError):
        audit_read_service.purge_expired_events(owner_session, retention_days=365)
    monkeypatch.undo()
    assert _has_delete(owner_session) is False


def test_a_non_positive_retention_is_an_off_switch_not_a_wipe(owner_session: Session) -> None:
    """The cutoff is `now - retention_days`, so without the guard a value of 0
    collapses it to "now" and EVERY row matches — including the event written a
    moment ago.

    On this table that is the difference between a disabled sweep and erasing the
    entire audit trail, which is why the guard is asserted rather than trusted.
    """
    _seed(owner_session, age_days=500)
    owner_session.execute(text(_revoke_statement(_ROLE)))
    owner_session.commit()

    for retention in (0, -1):
        assert audit_read_service.purge_expired_events(owner_session, retention_days=retention) == 0
    assert len(owner_session.scalars(AuditEvent.__table__.select()).all()) == 3
