"""Offboarding at the service level (#1699) — the last-admin guard, and the proof
that the whole pass is one transaction.

Both live here rather than beside the API tests because the API's dev-bypass
caller is re-upserted as a stored-role admin on EVERY request, so "the target is
the only stored-role admin" is unreachable through the client; and because
atomicity cannot be observed from inside the transaction doing the writing.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as SASession

from backend.app.db.models import (
    ApiKey,
    AuditEvent,
    Connection,
    Suite,
    User,
    UserSession,
    WorkspaceMember,
)
from backend.app.services import offboarding_service


def _user(session: Any, role: str = "member") -> User:
    user = User(
        id=uuid.uuid4(),
        aad_object_id=None,
        email=f"{role}-{uuid.uuid4().hex[:8]}@example.com",
        role=role,
    )
    session.add(user)
    session.flush()
    return user


# ── The last-admin guard ─────────────────────────────────────────────────────


def test_the_only_stored_role_admin_cannot_be_offboarded(db_session: Any) -> None:
    db_session.execute(delete(User))
    sole = _user(db_session, "admin")
    actor = sole
    db_session.commit()

    assert offboarding_service.preview(db_session, sole.id, actor=actor).is_last_admin is True
    with pytest.raises(offboarding_service.OffboardBlockedError):
        offboarding_service.offboard(
            db_session, sole.id, new_owner_user_id=None, confirm_email=sole.email, actor=actor
        )


def test_a_second_admin_unblocks_the_pass(db_session: Any) -> None:
    db_session.execute(delete(User))
    leaver = _user(db_session, "admin")
    keeper = _user(db_session, "admin")
    db_session.commit()

    assert offboarding_service.preview(db_session, leaver.id, actor=keeper).is_last_admin is False
    receipt = offboarding_service.offboard(
        db_session, leaver.id, new_owner_user_id=None, confirm_email=leaver.email, actor=keeper
    )
    assert receipt.user_id == leaver.id


def test_an_allowlist_admin_does_not_satisfy_the_guard(
    db_session: Any, make_workspace_admin: Any
) -> None:
    """An allowlist-resolved admin can vanish with the next deploy, so it cannot
    stand in for the stored-role admin the guard exists to preserve.
    """
    db_session.execute(delete(User))
    leaver = _user(db_session, "admin")
    standby = _user(db_session, "member")
    db_session.commit()
    make_workspace_admin(standby.email)

    assert offboarding_service.preview(db_session, leaver.id, actor=standby).is_last_admin is True


# ── One transaction, observed from a second connection ───────────────────────


class _Fixture:
    """A leaver whose rows are really COMMITTED, plus an independent connection.

    The shared `db_session` fixture rolls everything back, so a test reading
    through it cannot tell a commit from an uncommitted write — which is exactly
    the question here.
    """

    def __init__(self, engine: Any) -> None:
        self.engine = engine
        self.session = SASession(bind=engine)
        self.actor = _user(self.session, "admin")
        self.leaver = _user(self.session, "member")
        self.heir = _user(self.session, "member")
        self.viewer = _user(self.session, "viewer")
        connection = Connection(
            name=f"sf-{uuid.uuid4().hex[:8]}",
            type="snowflake",
            env="dev",
            config={"account": "ab12345.eu-west-1"},
            secret_ref="kv-sf",
            created_by=self.leaver.id,
        )
        self.session.add(connection)
        self.session.flush()
        self.suite = Suite(
            name=f"suite-{uuid.uuid4().hex[:6]}",
            connection_id=connection.id,
            created_by=self.leaver.id,
        )
        self.session.add(self.suite)
        self.member = WorkspaceMember(
            id=uuid.uuid4(),
            email=self.leaver.email.lower(),
            initial_role="member",
            source="admin",
            invited_by=None,
        )
        self.session.add(self.member)
        self.key = ApiKey(
            id=uuid.uuid4(),
            user_id=self.leaver.id,
            name="laptop",
            key_prefix="dq_live_test",
            key_hash=uuid.uuid4().hex,
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
        self.session.add(self.key)
        self.user_session = UserSession(
            id=uuid.uuid4(),
            user_id=self.leaver.id,
            token_hash=uuid.uuid4().hex,
            expires_at=datetime.now(UTC) + timedelta(hours=4),
        )
        self.session.add(self.user_session)
        self.connection_id = connection.id
        self.session.commit()
        # Plain ids from here on. Every ORM instance above belongs to a session
        # whose rows the pass deletes, and touching one afterwards refreshes it —
        # which RAISES for a deleted row instead of reading as absent.
        self.actor_id = self.actor.id
        self.leaver_id = self.leaver.id
        self.leaver_email = self.leaver.email
        self.heir_id = self.heir.id
        self.viewer_id = self.viewer.id
        self.suite_id = self.suite.id
        self.member_id = self.member.id
        self.key_id = self.key.id
        self.session_id = self.user_session.id

    def observe(self) -> dict[str, Any]:
        """Read the outcomes on a SEPARATE connection, and end its transaction.

        A reader left open holds locks that the cleanup below — and the suite's
        own `drop_all` — then wait on forever.
        """
        with SASession(bind=self.engine) as other:
            snapshot = {
                "suite_owner": other.get(Suite, self.suite_id).created_by,
                "key_revoked": other.get(ApiKey, self.key_id).revoked_at is not None,
                "session_revoked": other.get(UserSession, self.session_id).revoked_at is not None,
                "member_present": other.get(WorkspaceMember, self.member_id) is not None,
                "offboard_events": len(
                    other.scalars(
                        select(AuditEvent).where(
                            AuditEvent.action == offboarding_service.AUDIT_ACTION,
                            AuditEvent.entity_id == self.leaver_id,
                        )
                    ).all()
                ),
            }
            other.rollback()
        return snapshot

    def close(self) -> None:
        """Clean up on the fixture's OWN connection — a second one would queue
        behind whatever locks this one still holds.
        """
        self.session.rollback()
        for stmt in (
            delete(AuditEvent).where(AuditEvent.actor_user_id == self.actor_id),
            delete(AuditEvent).where(AuditEvent.entity_id.in_([self.leaver_id, self.suite_id])),
            delete(UserSession).where(UserSession.user_id == self.leaver_id),
            delete(ApiKey).where(ApiKey.user_id == self.leaver_id),
            delete(WorkspaceMember).where(WorkspaceMember.id == self.member_id),
            delete(Suite).where(Suite.id == self.suite_id),
            delete(Connection).where(Connection.id == self.connection_id),
            delete(User).where(
                User.id.in_([self.actor_id, self.leaver_id, self.heir_id, self.viewer_id])
            ),
        ):
            self.session.execute(stmt)
        self.session.commit()
        self.session.close()


@pytest.fixture
def committed(_db_engine: Any) -> Iterator[_Fixture]:
    fixture = _Fixture(_db_engine)
    try:
        yield fixture
    finally:
        fixture.close()


def test_the_committed_pass_is_visible_on_another_connection(committed: _Fixture) -> None:
    """The positive control for the test below: without it, an all-rollback bug
    would make the failure assertions pass for the wrong reason.
    """
    offboarding_service.offboard(
        committed.session,
        committed.leaver_id,
        new_owner_user_id=committed.heir_id,
        confirm_email=committed.leaver_email,
        actor=committed.actor,
    )

    observed = committed.observe()
    assert observed["suite_owner"] == committed.heir_id
    assert observed["key_revoked"] is True
    assert observed["session_revoked"] is True
    assert observed["member_present"] is False
    assert observed["offboard_events"] == 1


def test_a_transfer_that_fails_leaves_no_credential_revoked_and_no_suite_moved(
    committed: _Fixture,
) -> None:
    """A viewer cannot own a suite, so the FIRST transfer raises and the pass
    stops there. Checked from a connection that can only see committed state.

    This one proves the guard refuses cleanly; the rollback of work that already
    reached its own step is the test below, which is the one that goes red when
    the rollback is mutated away.
    """
    with pytest.raises(Exception, match="viewer cannot own a suite"):
        offboarding_service.offboard(
            committed.session,
            committed.leaver_id,
            new_owner_user_id=committed.viewer_id,
            confirm_email=committed.leaver_email,
            actor=committed.actor,
        )

    observed = committed.observe()
    assert observed["suite_owner"] == committed.leaver_id
    assert observed["key_revoked"] is False
    assert observed["session_revoked"] is False
    assert observed["member_present"] is True
    assert observed["offboard_events"] == 0


def test_a_failure_after_the_transfers_takes_them_back_too(
    committed: _Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The atomicity claim itself: the suites move FIRST and `transfer_ownership`
    ends in its own `commit()`, so a failure at the LAST step has to undo work
    that already looked committed. Mutation-checked — swapping the rollback for a
    commit turns this test red.
    """

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("membership backend is down")

    monkeypatch.setattr(offboarding_service.membership_service, "remove_member", _boom)

    with pytest.raises(RuntimeError, match="membership backend is down"):
        offboarding_service.offboard(
            committed.session,
            committed.leaver_id,
            new_owner_user_id=committed.heir_id,
            confirm_email=committed.leaver_email,
            actor=committed.actor,
        )

    observed = committed.observe()
    assert observed["suite_owner"] == committed.leaver_id
    assert observed["key_revoked"] is False
    assert observed["session_revoked"] is False
    assert observed["member_present"] is True
    assert observed["offboard_events"] == 0
