"""Health-score weights as a workspace setting (#1559): the resolver, the
validation, the audit trail, and the proof that the score actually moves."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.db.models import AuditEvent, ScoringSetting, User
from backend.app.services import scoring_settings_service as svc
from backend.app.services.rollup import health_score


@pytest.fixture
def user(db_session: Any) -> User:
    row = User(
        id=uuid.uuid4(),
        aad_object_id=None,
        email=f"scoring-admin-{uuid.uuid4().hex[:8]}@example.com",
        role="admin",
    )
    db_session.add(row)
    db_session.commit()
    return row


def test_no_row_means_the_adr_0005_defaults(db_session: Session) -> None:
    assert svc.weights(db_session) == svc.DEFAULT_WEIGHTS
    assert [svc.DEFAULT_WEIGHTS.penalty(s) for s in ("pass", "warn", "fail", "critical")] == [
        0.0,
        0.5,
        1.0,
        2.0,
    ]


def test_set_writes_the_row_and_an_audit_event(db_session: Session, user: User) -> None:
    row = svc.set_weights(db_session, warn=0.25, fail=1.0, critical=4.0, actor=user)
    db_session.flush()
    assert (row.warn_weight, row.fail_weight, row.critical_weight) == (
        Decimal("0.25"),
        Decimal("1.0"),
        Decimal("4.0"),
    )
    assert row.updated_by == user.id
    assert svc.weights(db_session) == svc.Weights(warn=0.25, fail=1.0, critical=4.0)
    event = db_session.query(AuditEvent).filter_by(action="scoring_setting.update").one()
    assert event.before is None
    assert event.after is not None and float(event.after["critical_weight"]) == 4.0


def test_a_change_moves_the_score_read_through_the_resolver(
    db_session: Session, user: User
) -> None:
    """The point of the feature: the same histogram scores differently after a save."""
    counts = {"pass": 2, "fail": 2}
    assert health_score(counts, svc.weights(db_session)) == 75.0
    svc.set_weights(db_session, warn=0.5, fail=1.0, critical=1.0, actor=user)
    assert health_score(counts, svc.weights(db_session)) == 50.0


def test_reset_deletes_the_row_and_audits_it(db_session: Session, user: User) -> None:
    svc.set_weights(db_session, warn=0.5, fail=1.0, critical=3.0, actor=user)
    svc.reset_weights(db_session, actor=user)
    db_session.flush()
    assert svc.get_row(db_session) is None
    assert svc.weights(db_session) == svc.DEFAULT_WEIGHTS
    events = (
        db_session.query(AuditEvent)
        .filter_by(action="scoring_setting.update")
        .order_by(AuditEvent.occurred_at)
        .all()
    )
    assert len(events) == 2
    assert events[1].before is not None and float(events[1].before["critical_weight"]) == 3.0
    assert events[1].after is None


def test_reset_with_no_row_writes_nothing(db_session: Session, user: User) -> None:
    svc.reset_weights(db_session, actor=user)
    assert db_session.query(AuditEvent).filter_by(action="scoring_setting.update").count() == 0


@pytest.mark.parametrize(
    ("warn", "fail", "critical"),
    [
        (1.0, 0.5, 2.0),  # warn > fail
        (0.5, 2.0, 1.0),  # fail > critical
        (0.0, 0.0, 0.0),  # critical = 0 divides the normaliser
        (-0.1, 1.0, 2.0),
        (0.5, 1.0, 101.0),
    ],
)
def test_invalid_orderings_are_refused_before_the_write(
    db_session: Session, user: User, warn: float, fail: float, critical: float
) -> None:
    with pytest.raises(svc.ScoringWeightsInvalidError):
        svc.set_weights(db_session, warn=warn, fail=fail, critical=critical, actor=user)
    assert svc.get_row(db_session) is None


def test_the_table_check_backs_the_validator(db_session: Session) -> None:
    """A direct write that bypasses `set_weights` still cannot store a bad ordering."""
    db_session.add(
        ScoringSetting(
            id=1, warn_weight=Decimal("2"), fail_weight=Decimal("1"), critical_weight=Decimal("3")
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()
