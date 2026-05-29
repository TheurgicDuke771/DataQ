"""Seed idempotency for the probe fixtures (real Postgres via db_session)."""

from typing import Any

from sqlalchemy import select

from backend.app.core.config import get_settings
from backend.app.db.models import Check, Connection, Suite, User
from backend.app.services.probe import ensure_probe_fixtures


def _user(db: Any) -> User:
    user = User(aad_object_id="seed-user", email="seed@example.com", display_name=None)
    db.add(user)
    db.commit()
    return user


def test_seed_creates_fixtures(db_session: Any) -> None:
    user = _user(db_session)
    connection, suite, checks = ensure_probe_fixtures(
        db_session, user=user, settings=get_settings()
    )
    assert connection.id is not None
    assert suite.connection_id == connection.id
    assert len(checks) == 1
    assert checks[0].expectation_type == "expect_table_row_count_to_be_between"


def test_seed_is_idempotent(db_session: Any) -> None:
    user = _user(db_session)
    settings = get_settings()
    c1, s1, _ = ensure_probe_fixtures(db_session, user=user, settings=settings)
    c2, s2, _ = ensure_probe_fixtures(db_session, user=user, settings=settings)

    assert c1.id == c2.id
    assert s1.id == s2.id
    assert len(db_session.scalars(select(Connection)).all()) == 1
    assert len(db_session.scalars(select(Suite)).all()) == 1
    assert len(db_session.scalars(select(Check)).all()) == 1  # check not duplicated
