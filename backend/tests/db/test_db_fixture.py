"""Validates the db_session fixture: real persistence + per-test rollback isolation.

These run only when TEST_DATABASE_URL is set (CI Postgres service / local compose);
they skip otherwise.
"""

from typing import Any

from sqlalchemy import select

from backend.app.db.models import User


def test_db_session_persists_with_server_default_id(db_session: Any) -> None:
    user = User(aad_object_id="fixture-1", email="f1@example.com", display_name="F1")
    db_session.add(user)
    db_session.commit()

    got = db_session.scalars(select(User).where(User.aad_object_id == "fixture-1")).one()
    assert got.email == "f1@example.com"
    assert got.id is not None  # gen_random_uuid() server default executed
    assert got.created_at is not None


def test_isolation_write(db_session: Any) -> None:
    db_session.add(User(aad_object_id="iso", email="iso@example.com", display_name=None))
    db_session.commit()
    assert db_session.scalars(select(User).where(User.aad_object_id == "iso")).one_or_none()


def test_isolation_prior_test_rolled_back(db_session: Any) -> None:
    # The user committed in test_isolation_write must not survive into this test.
    assert db_session.scalars(select(User).where(User.aad_object_id == "iso")).one_or_none() is None
