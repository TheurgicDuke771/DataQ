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
