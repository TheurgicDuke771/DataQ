"""Deleting a user must not 500 — #1319, and the prerequisite for #432 erasure.

Three `created_by` foreign keys defaulted to `NO ACTION`, so deleting a user who
had ever created a connection, a suite or a schedule raised `ForeignKeyViolation`.
Latent only because v1 has no user-delete route at all; GDPR Art 17 erasure (#432)
is what makes it live.

Exercised through the ORM against real Postgres rather than by reading the model,
because `ondelete` is enforced by the **database** — a model attribute asserts
what we declared, and only a delete asserts what happens.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from backend.app.db.models import Connection, Schedule, Suite, User


def test_deleting_a_user_nulls_provenance_and_keeps_the_rows(db_session: Any) -> None:
    """The children survive with `created_by` NULL.

    Survival is the point, and it is the half a test could easily get wrong by
    asserting only "no exception": CASCADE would also raise nothing, while
    silently destroying every connection, suite and schedule the departing user
    ever made — for a leaver in a real workspace, that is most of it.
    """
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
    """The authz consequence, asserted rather than assumed.

    `suite_authz` decides ownership with `suite.created_by == user_id`. A NULL
    compares equal to nothing, so an authorless suite simply has no owner — which
    is the safe direction. Asserted because the unsafe direction is a one-character
    difference (`is None` handling that treated absence as a match), and it would
    hand every user ownership of every suite whose author was erased.
    """
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
    """The consequence one PR over, and the reason it matters more than it looks.

    `list_all_suites` inner-joined the author, so a suite whose creator was erased
    dropped out of the Admin control centre **entirely** — while still running on
    its schedules and still holding its shares. Invisible and active is the worst
    combination available: an admin reviewing the workspace would not see the one
    suite with nobody obviously responsible for it.

    Found by review, on a file this change never touched — a widened column ages
    every query that assumed it was NOT NULL.
    """
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
    """`list_all_access` is the other side, and it wants the OPPOSITE handling.

    There, an erased author leaves no grant to report — a row with a null user
    would render as a grant to nobody. Its absence is correct here, while its
    absence from the suites overview above is not, which is exactly why the two
    joins treat the null differently. Asserted so that "make them consistent"
    cannot be applied later as a tidy-up.
    """
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
