"""Deleting a user must not 500 — #1319, and the prerequisite for #432 erasure."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from backend.app.db.models import Connection, Schedule, Suite, User


def test_deleting_a_user_nulls_provenance_and_keeps_the_rows(db_session: Any) -> None:
    """The children survive with `created_by` NULL."""
    author = User(aad_object_id=uuid.uuid4().hex, email=f"a-{uuid.uuid4().hex[:8]}@example.com")
    db_session.add(author)
    db_session.flush()

    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "x"},
        created_by=author.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(name=f"s-{uuid.uuid4().hex[:8]}", connection_id=conn.id, created_by=author.id)
    db_session.add(suite)
    db_session.flush()
    schedule = Schedule(
        suite_id=suite.id,
        cron="0 1 * * *",
        timezone="UTC",
        next_run_at=datetime(2026, 9, 1, tzinfo=UTC),
        created_by=author.id,
    )
    db_session.add(schedule)
    db_session.commit()
    conn_id, suite_id, schedule_id = conn.id, suite.id, schedule.id

    # The operation that raised ForeignKeyViolation before this change.
    db_session.delete(author)
    db_session.commit()
    db_session.expire_all()

    for model, row_id, label in (
        (Connection, conn_id, "connection"),
        (Suite, suite_id, "suite"),
        (Schedule, schedule_id, "schedule"),
    ):
        row = db_session.get(model, row_id)
        assert row is not None, (
            f"the {label} was destroyed with its author — `created_by` is provenance, "
            "so the row must outlive the user (SET NULL, not CASCADE)"
        )
        assert row.created_by is None, f"the {label}'s stale author id survived the delete"


def test_a_suite_with_no_author_grants_nobody_ownership(db_session: Any) -> None:
    """The authz consequence, asserted rather than assumed."""
    from backend.app.services.suite_authz import effective_permission

    author = User(aad_object_id=uuid.uuid4().hex, email=f"a-{uuid.uuid4().hex[:8]}@example.com")
    other = User(aad_object_id=uuid.uuid4().hex, email=f"o-{uuid.uuid4().hex[:8]}@example.com")
    db_session.add_all([author, other])
    db_session.flush()
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "x"},
        created_by=author.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(name=f"s-{uuid.uuid4().hex[:8]}", connection_id=conn.id, created_by=author.id)
    db_session.add(suite)
    db_session.commit()
    suite_id = suite.id

    db_session.delete(author)
    db_session.commit()
    db_session.expire_all()

    reloaded = db_session.get(Suite, suite_id)
    assert reloaded.created_by is None, "precondition: the author really was erased"
    assert effective_permission(db_session, reloaded, other.id) is None


def test_an_ownerless_suite_stays_visible_in_the_admin_overview(db_session: Any) -> None:
    """The consequence one PR over, and the reason it matters more than it looks."""
    from backend.app.services import admin_service

    author = User(aad_object_id=uuid.uuid4().hex, email=f"a-{uuid.uuid4().hex[:8]}@example.com")
    db_session.add(author)
    db_session.flush()
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "x"},
        created_by=author.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(
        name=f"orphan-{uuid.uuid4().hex[:6]}", connection_id=conn.id, created_by=author.id
    )
    db_session.add(suite)
    db_session.commit()
    suite_id, suite_name = suite.id, suite.name

    assert any(
        r.id == suite_id for r in admin_service.list_all_suites(db_session)
    ), "precondition: the suite is listed while its author exists"

    db_session.delete(author)
    db_session.commit()
    db_session.expire_all()

    listed = {r.id: r for r in admin_service.list_all_suites(db_session)}
    assert suite_id in listed, (
        f"the suite {suite_name!r} vanished from the admin overview when its author "
        "was erased — it still runs, so it must still be visible"
    )
    row = listed[suite_id]
    assert (
        row.owner_id is None and row.owner_email is None
    ), "an erased author must read as absent, not as a stale or invented owner"


def test_an_ownerless_suite_reports_no_owner_grant(db_session: Any) -> None:
    """`list_all_access` is the other side, and it wants the OPPOSITE handling."""
    from backend.app.services import admin_service

    author = User(aad_object_id=uuid.uuid4().hex, email=f"a-{uuid.uuid4().hex[:8]}@example.com")
    db_session.add(author)
    db_session.flush()
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "x"},
        created_by=author.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(name=f"s-{uuid.uuid4().hex[:8]}", connection_id=conn.id, created_by=author.id)
    db_session.add(suite)
    db_session.commit()
    suite_id = suite.id

    db_session.delete(author)
    db_session.commit()
    db_session.expire_all()

    rows = admin_service.list_all_access(db_session)
    assert not [r for r in rows if r.suite_id == suite_id], (
        "an erased author must leave no access row — a null user would render as a "
        "grant to nobody"
    )
