"""The retention sweep versus the migration's own `REVOKE` — ADR 0041 §2.7 (#1318)."""

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
# Test-only, never a real credential: this role exists for the length of one test on a throwaway
# database, and is dropped in teardown.
_PASSWORD = uuid.uuid4().hex


def _revoke_statement(role: str) -> str:
    """The migration's OWN statement, loaded from the migration."""
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
    """Property 1 — the precondition every other assertion here depends on."""
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
    statement.
    """
    _seed(owner_session, age_days=500)
    _seed(owner_session, age_days=1)
    owner_session.execute(text(_revoke_statement(_ROLE)))
    owner_session.commit()

    deleted = audit_read_service.purge_expired_events(owner_session, retention_days=365)

    assert deleted == 3
    remaining = owner_session.scalars(AuditEvent.__table__.select()).all()
    assert len(remaining) == 3, "the recent events must survive — this is a cutoff, not a wipe"


def test_the_sweep_leaves_the_guard_back_in_place(owner_session: Session) -> None:
    """Property 3 — and the one most likely to rot silently."""
    _seed(owner_session, age_days=500)
    owner_session.execute(text(_revoke_statement(_ROLE)))
    owner_session.commit()

    audit_read_service.purge_expired_events(owner_session, retention_days=365)
    assert _has_delete(owner_session) is False


def test_a_database_level_failure_still_restores_the_guard(
    owner_session: Session, monkeypatch: Any
) -> None:
    """The failure path, provoked by a REAL database error — which is the whole
    point, and which the first version of this test could not do.
    """
    _seed(owner_session, age_days=500)
    owner_session.execute(text(_revoke_statement(_ROLE)))
    owner_session.commit()

    real = audit_read_service._set_delete_privilege

    def _skip_the_grant(session: Session, *, granted: bool) -> None:
        if granted:
            return
        real(session, granted=granted)

    monkeypatch.setattr(audit_read_service, "_set_delete_privilege", _skip_the_grant)
    with pytest.raises(ProgrammingError):
        audit_read_service.purge_expired_events(owner_session, retention_days=365)
    monkeypatch.undo()

    # The session must be usable — i.e. it was rolled back rather than left
    # poisoned — and the guard must still be in place.
    assert _has_delete(owner_session) is False
    assert len(owner_session.scalars(AuditEvent.__table__.select()).all()) == 3


def test_a_crash_mid_sweep_cannot_leave_delete_granted(owner_session: Session) -> None:
    """The property the one-transaction-per-batch design exists for."""
    owner_session.execute(text(_revoke_statement(_ROLE)))
    owner_session.commit()

    audit_read_service._set_delete_privilege(owner_session, granted=True)
    assert _has_delete(owner_session) is True, "precondition: the grant took effect"
    owner_session.rollback()  # stands in for the process dying

    assert _has_delete(owner_session) is False


def test_a_failure_before_the_revoke_does_not_leave_the_grant_committed(
    owner_session: Session, monkeypatch: Any
) -> None:
    """The property the one-transaction-per-batch design exists for, asserted
    against the SWEEP rather than the primitive.
    """
    _seed(owner_session, age_days=500)
    owner_session.execute(text(_revoke_statement(_ROLE)))
    owner_session.commit()

    real = audit_read_service._set_delete_privilege

    def _explode_on_revoke(session: Session, *, granted: bool) -> None:
        if not granted:
            raise RuntimeError("worker died before revoking")
        real(session, granted=granted)

    monkeypatch.setattr(audit_read_service, "_set_delete_privilege", _explode_on_revoke)
    with pytest.raises(RuntimeError):
        audit_read_service.purge_expired_events(owner_session, retention_days=365)
    monkeypatch.undo()

    assert _has_delete(owner_session) is False, (
        "the grant was committed separately from the revoke — a crash in this "
        "window leaves DELETE granted permanently"
    )
    # The delete must have gone with it: the batch is one transaction, so a
    # failure anywhere in it undoes the whole batch, not just the privilege.
    assert len(owner_session.scalars(AuditEvent.__table__.select()).all()) == 3


def test_an_unknown_rowcount_terminates_instead_of_spinning(
    owner_session: Session, monkeypatch: Any
) -> None:
    """Some DB-API drivers return -1 for "unknown rowcount", which is TRUTHY."""

    class _UnknownRowcount:
        rowcount = -1

    real_execute = owner_session.execute
    seen = {"deletes": 0}

    def _fake_execute(statement: Any, *args: Any, **kwargs: Any) -> Any:
        text_sql = str(statement)
        if text_sql.strip().upper().startswith("DELETE"):
            seen["deletes"] += 1
            if seen["deletes"] > 3:  # a spin guard for the test itself
                raise AssertionError("the loop did not terminate on an unknown rowcount")
            return _UnknownRowcount()
        return real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(owner_session, "execute", _fake_execute)
    total = audit_read_service.purge_expired_events(owner_session, retention_days=365)
    monkeypatch.undo()

    assert total == 0
    assert seen["deletes"] == 1, "an unknown rowcount must read as zero and stop"


def test_a_non_positive_retention_is_an_off_switch_not_a_wipe(owner_session: Session) -> None:
    """The cutoff is `now - retention_days`, so without the guard a value of 0
    collapses it to "now" and EVERY row matches — including the event written a
    moment ago.
    """
    _seed(owner_session, age_days=500)
    owner_session.execute(text(_revoke_statement(_ROLE)))
    owner_session.commit()

    for retention in (0, -1):
        assert audit_read_service.purge_expired_events(owner_session, retention_days=retention) == 0
    assert len(owner_session.scalars(AuditEvent.__table__.select()).all()) == 3
