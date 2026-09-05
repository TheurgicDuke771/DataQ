"""In-app workspace membership — ADR 0043 service behaviour."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, cast

import pytest
from sqlalchemy import Table, func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import Settings
from backend.app.db.models import AuditEvent, User, WorkspaceMember
from backend.app.services import membership_service as svc
from backend.tests.support.scratch_db import scratch_engine

# A deployment where dev bypass is NOT the selected mode, so the gate actually runs.
_ENFORCING = Settings(
    environment="prod",
    auth_dev_bypass=False,
    oidc_issuer="https://issuer.example/",
    oidc_audience="dataq",
)


# A deployment whose env vars name one address outright, so the members list
# has an env row to show.
_ENV_LISTED = Settings(
    environment="prod",
    auth_dev_bypass=False,
    oidc_issuer="https://issuer.example/",
    oidc_audience="dataq",
    oidc_allowed_emails="on-the-allowlist@acme.io",
    oidc_allowed_domains="acme.io",
)


# The same deployment WITH an app-side access allowlist, so the env half of the
# predicate has something to say.
_GATED = Settings(
    environment="prod",
    auth_dev_bypass=False,
    oidc_issuer="https://issuer.example/",
    oidc_audience="dataq",
    oidc_allowed_domains="acme.io",
)


def _user(db: Any, email: str, role: str = "member") -> User:
    row = User(id=uuid.uuid4(), aad_object_id=uuid.uuid4().hex, email=email, role=role)
    db.add(row)
    db.flush()
    return row


def _require(user: User | None) -> User:
    """Seeded a line above; `Session.get` is Optional-typed and this says so once."""
    assert user is not None
    return user


def _addr(prefix: str = "p") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


# ── the predicate ─────────────────────────────────────────────────────────────


def test_an_empty_table_returns_the_door_s_own_verdict(db_session: Any) -> None:
    """The switch itself: while nothing is managed, this module has no opinion."""
    assert svc.enforcement_active(db_session) is False
    assert svc.is_member(db_session, _addr(), settings=_ENFORCING) is True
    # A deployment that DOES configure an access allowlist still refuses the
    # addresses it omits, table or no table.
    assert svc.is_member(db_session, _addr(), settings=_GATED) is False


def test_a_populated_table_admits_only_listed_addresses(db_session: Any) -> None:
    admin = _user(db_session, _addr("admin"), role="admin")
    listed = _addr("listed")
    svc.add_member(db_session, email=listed, initial_role="member", actor=admin)

    assert svc.enforcement_active(db_session) is True
    assert svc.is_member(db_session, listed, settings=_ENFORCING) is True
    assert svc.is_member(db_session, _addr("other"), settings=_ENFORCING) is False


def test_the_env_allowlist_is_grant_only(db_session: Any) -> None:
    """An env entry admits somebody the table does not name; it can never remove one."""
    admin = _user(db_session, _addr("admin"), role="admin")
    svc.add_member(db_session, email=_addr("listed"), initial_role="member", actor=admin)

    unlisted = "on-the-allowlist@acme.io"
    assert svc.is_member(db_session, unlisted, settings=_GATED) is True


def test_matching_ignores_casing_and_surrounding_space(db_session: Any) -> None:
    admin = _user(db_session, _addr("admin"), role="admin")
    svc.add_member(db_session, email="Ada.Lovelace@Example.COM", initial_role="member", actor=admin)

    assert svc.is_member(db_session, "  ADA.LOVELACE@example.com ", settings=_ENFORCING) is True


def test_dev_bypass_is_exempt_so_the_local_stack_stays_bootable(db_session: Any) -> None:
    admin = _user(db_session, _addr("admin"), role="admin")
    svc.add_member(db_session, email=_addr("listed"), initial_role="member", actor=admin)
    bypass = Settings(environment="dev", auth_dev_bypass=True)

    assert bypass.dev_bypass_active is True
    assert svc.is_member(db_session, _addr("stranger"), settings=bypass) is True


def test_dev_bypass_beside_email_otp_is_NOT_exempt() -> None:
    """The trap this narrowing exists for: `dev_bypass_allowed` stays true on an
    OTP stack, where the ladder picks OTP and never mints the bypass identity.
    """
    otp = Settings(
        environment="dev",
        auth_dev_bypass=True,
        auth_email_smtp_host="smtp.example.com",
        auth_email_username="dataq@example.com",
        auth_email_from="dataq@example.com",
        auth_email_password_secret_name="auth-email-password",
        auth_otp_allowed_domains="acme.io",
    )
    assert otp.dev_bypass_allowed is True
    assert otp.dev_bypass_active is False


def test_require_member_raises_403_with_a_membership_reason(db_session: Any) -> None:
    admin = _user(db_session, _addr("admin"), role="admin")
    svc.add_member(db_session, email=_addr("listed"), initial_role="member", actor=admin)

    with pytest.raises(svc.MembershipDeniedError) as exc:
        svc.require_member(db_session, _addr("out"), door="probe", settings=_ENFORCING)
    assert exc.value.status_code == 403
    assert exc.value.code == "not_a_workspace_member"


def test_the_denial_log_line_carries_no_address(db_session: Any, capsys: Any, caplog: Any) -> None:
    admin = _user(db_session, _addr("admin"), role="admin")
    svc.add_member(db_session, email=_addr("listed"), initial_role="member", actor=admin)
    denied = "secret.person@private.example"

    with caplog.at_level("WARNING"), pytest.raises(svc.MembershipDeniedError):
        svc.require_member(db_session, denied, door="probe", settings=_ENFORCING)

    # Both sinks: structlog goes to stdout on its own and through `logging` when
    # pytest's log capture is active, and which one applies depends on how the
    # file is invoked.
    logged = capsys.readouterr().out + caplog.text
    assert denied not in logged
    assert "private.example" in logged  # domain and digest only, never the address


# ── initial_role (decision 9) ─────────────────────────────────────────────────


def test_initial_role_is_reported_for_a_listed_address(db_session: Any) -> None:
    admin = _user(db_session, _addr("admin"), role="admin")
    email = _addr("viewer")
    svc.add_member(db_session, email=email, initial_role="viewer", actor=admin)

    assert svc.initial_role_for(db_session, email.upper()) == "viewer"
    assert svc.initial_role_for(db_session, _addr("nobody")) is None


# ── auto-import (decision 8) ──────────────────────────────────────────────────


def test_the_first_add_imports_every_existing_user_provisionally(db_session: Any) -> None:
    admin = _user(db_session, _addr("admin"), role="admin")
    other = _user(db_session, _addr("other"), role="viewer")

    outcome = svc.add_member(db_session, email=_addr("new"), initial_role="member", actor=admin)

    assert outcome.auto_imported_count == 2
    rows = {m.email: m for m in svc.list_members(db_session).members}
    assert rows[admin.email].source == "auto_import"
    assert rows[other.email].source == "auto_import"
    # The imported row carries the user's CURRENT role, not a guessed default.
    assert rows[other.email].initial_role == "viewer"
    assert outcome.member.source == "admin"


def test_a_later_add_imports_nothing(db_session: Any) -> None:
    admin = _user(db_session, _addr("admin"), role="admin")
    svc.add_member(db_session, email=_addr("first"), initial_role="member", actor=admin)

    second = svc.add_member(db_session, email=_addr("second"), initial_role="member", actor=admin)
    assert second.auto_imported_count == 0


def test_adding_an_address_that_already_has_a_user_row_does_not_double_admit(
    db_session: Any,
) -> None:
    admin = _user(db_session, _addr("admin"), role="admin")
    existing = _user(db_session, _addr("existing"))

    outcome = svc.add_member(db_session, email=existing.email, initial_role="admin", actor=admin)

    assert outcome.member.source == "admin"
    assert outcome.member.initial_role == "admin"
    emails = [m.email for m in svc.list_members(db_session).members]
    assert emails.count(existing.email) == 1


def test_adding_the_same_address_twice_is_refused(db_session: Any) -> None:
    admin = _user(db_session, _addr("admin"), role="admin")
    email = _addr("dup")
    svc.add_member(db_session, email=email, initial_role="member", actor=admin)

    with pytest.raises(svc.MembershipChangeRejectedError):
        svc.add_member(db_session, email=email.upper(), initial_role="member", actor=admin)


@pytest.mark.parametrize(
    "bad", ["", "no-at-sign", "@nolocal.example", "nodomain@", "a b@c.example"]
)
def test_an_unusable_address_is_refused_before_it_reaches_the_driver(
    db_session: Any, bad: str
) -> None:
    admin = _user(db_session, _addr("admin"), role="admin")
    with pytest.raises(svc.MembershipChangeRejectedError):
        svc.add_member(db_session, email=bad, initial_role="member", actor=admin)


def test_an_unknown_role_is_refused(db_session: Any) -> None:
    admin = _user(db_session, _addr("admin"), role="admin")
    with pytest.raises(svc.MembershipChangeRejectedError):
        svc.add_member(db_session, email=_addr(), initial_role="superuser", actor=admin)


# ── removal + the last-admin guard ────────────────────────────────────────────


def test_removing_a_member_deletes_the_row(db_session: Any) -> None:
    admin = _user(db_session, _addr("admin"), role="admin")
    _user(db_session, _addr("second-admin"), role="admin")
    target = svc.add_member(db_session, email=_addr("go"), initial_role="member", actor=admin)

    svc.remove_member(db_session, target.member.id, actor=admin)

    assert target.member.email not in [m.email for m in svc.list_members(db_session).members]


def test_removing_the_last_stored_role_admin_is_refused(db_session: Any) -> None:
    admin = _user(db_session, _addr("admin"), role="admin")
    _user(db_session, _addr("member"), role="member")
    svc.add_member(db_session, email=_addr("new"), initial_role="member", actor=admin)
    own = next(m for m in svc.list_members(db_session).members if m.email == admin.email)

    with pytest.raises(svc.MembershipChangeRejectedError) as exc:
        svc.remove_member(db_session, own.id, actor=admin, confirm_self=True)
    assert "last admin" in str(exc.value).lower()


def test_a_second_stored_role_admin_makes_the_removal_allowed(db_session: Any) -> None:
    admin = _user(db_session, _addr("admin"), role="admin")
    other_admin = _user(db_session, _addr("other-admin"), role="admin")
    svc.add_member(db_session, email=_addr("new"), initial_role="member", actor=admin)
    row = next(m for m in svc.list_members(db_session).members if m.email == other_admin.email)

    svc.remove_member(db_session, row.id, actor=admin)

    assert other_admin.email not in [m.email for m in svc.list_members(db_session).members]


def test_removing_yourself_needs_an_explicit_confirmation(db_session: Any) -> None:
    admin = _user(db_session, _addr("admin"), role="admin")
    _user(db_session, _addr("other-admin"), role="admin")
    svc.add_member(db_session, email=_addr("new"), initial_role="member", actor=admin)
    own = next(m for m in svc.list_members(db_session).members if m.email == admin.email)

    with pytest.raises(svc.MembershipChangeRejectedError) as exc:
        svc.remove_member(db_session, own.id, actor=admin)
    assert "confirm_self" in str(exc.value)

    svc.remove_member(db_session, own.id, actor=admin, confirm_self=True)
    assert admin.email not in [m.email for m in svc.list_members(db_session).members]


def test_removing_an_unknown_member_is_a_404(db_session: Any) -> None:
    admin = _user(db_session, _addr("admin"), role="admin")
    with pytest.raises(svc.MemberNotFoundError):
        svc.remove_member(db_session, uuid.uuid4(), actor=admin)


# ── confirm ───────────────────────────────────────────────────────────────────


def test_confirming_clears_the_provisional_flag(db_session: Any) -> None:
    admin = _user(db_session, _addr("admin"), role="admin")
    svc.add_member(db_session, email=_addr("new"), initial_role="member", actor=admin)
    imported = next(m for m in svc.list_members(db_session).members if m.source == "auto_import")

    confirmed = svc.confirm_member(db_session, imported.id, actor=admin)

    assert confirmed.source == "admin"
    assert confirmed.invited_by_email == admin.email


def test_confirming_an_already_confirmed_row_is_idempotent(db_session: Any) -> None:
    admin = _user(db_session, _addr("admin"), role="admin")
    added = svc.add_member(db_session, email=_addr("new"), initial_role="member", actor=admin)

    again = svc.confirm_member(db_session, added.member.id, actor=admin)
    assert again.source == "admin"


# ── the list's computed fields ────────────────────────────────────────────────


def test_status_distinguishes_a_signed_in_member_from_a_pending_one(db_session: Any) -> None:
    admin = _user(db_session, _addr("admin"), role="admin")
    added = svc.add_member(db_session, email=_addr("pending"), initial_role="member", actor=admin)

    view = svc.list_members(db_session)
    rows = {m.email: m for m in view.members}
    assert rows[added.member.email].status == "pending"
    assert rows[added.member.email].user_id is None
    assert rows[admin.email].status == "active"
    assert rows[admin.email].stored_role == "admin"


def test_unmanaged_user_count_is_what_the_switch_on_warning_states(db_session: Any) -> None:
    _user(db_session, _addr("a"))
    _user(db_session, _addr("b"))

    view = svc.list_members(db_session)
    assert view.status.enforcement_active is False
    assert view.unmanaged_user_count == 2


# ── audit ─────────────────────────────────────────────────────────────────────


def _actions(db: Any) -> list[str]:
    return list(db.scalars(select(AuditEvent.action).order_by(AuditEvent.occurred_at)).all())


def test_every_membership_mutation_records_an_audit_event(db_session: Any) -> None:
    admin = _user(db_session, _addr("admin"), role="admin")
    _user(db_session, _addr("other-admin"), role="admin")
    added = svc.add_member(db_session, email=_addr("new"), initial_role="member", actor=admin)
    imported = next(m for m in svc.list_members(db_session).members if m.source == "auto_import")
    svc.confirm_member(db_session, imported.id, actor=admin)
    svc.remove_member(db_session, added.member.id, actor=admin)

    actions = _actions(db_session)
    assert "workspace_member.add" in actions
    assert "workspace_member.confirm" in actions
    assert "workspace_member.remove" in actions


def test_the_add_event_records_the_address_and_the_import_count(db_session: Any) -> None:
    """The audit payload DOES carry the address — it is the record. Log lines do not."""
    admin = _user(db_session, _addr("admin"), role="admin")
    email = _addr("new")
    svc.add_member(db_session, email=email, initial_role="viewer", actor=admin)

    event = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "workspace_member.add")
    ).one()
    assert event.after["email"] == email
    assert event.after["initial_role"] == "viewer"
    assert event.after["auto_imported_count"] == 1


# ── the auto-import commits with the first row, or not at all ─────────────────

_PROBE_DB = "dataq_membership_import_probe"


@pytest.fixture
def probe_engine() -> Any:
    """A REAL database, outside the fixture transaction: the suite's own session
    reads through its uncommitted transaction and so cannot see whether anything
    committed at all.
    """
    with scratch_engine(_PROBE_DB) as engine:
        yield engine


def test_the_switch_and_the_import_commit_together(probe_engine: Any) -> None:
    maker = sessionmaker(bind=probe_engine)
    with maker() as setup:
        actor = _user(setup, _addr("admin"), role="admin")
        _user(setup, _addr("other"))
        setup.commit()
        actor_id = actor.id

    with maker() as writer:
        actor = _require(writer.get(User, actor_id))
        svc.add_member(writer, email=_addr("new"), initial_role="member", actor=actor)

    # A SEPARATE connection: only committed rows are visible here.
    with maker() as reader:
        rows = reader.scalars(select(WorkspaceMember)).all()
        assert len(rows) == 3
        assert sum(1 for r in rows if r.source == "auto_import") == 2


def test_a_failing_first_add_leaves_no_imported_rows_behind(probe_engine: Any) -> None:
    """The import is part of the first insert, or it is a race — never a partial."""
    maker = sessionmaker(bind=probe_engine)
    with maker() as setup:
        actor = _user(setup, _addr("admin"), role="admin")
        _user(setup, _addr("other"))
        setup.commit()
        actor_id = actor.id

    with maker() as writer:
        actor = _require(writer.get(User, actor_id))
        with pytest.raises(svc.MembershipChangeRejectedError):
            svc.add_member(writer, email="not-an-address", initial_role="member", actor=actor)

    with maker() as reader:
        assert reader.scalars(select(WorkspaceMember)).all() == []


def test_the_last_admin_guard_holds_under_interleaved_sessions(probe_engine: Any) -> None:
    """Two admins removed at the same moment from two connections.

    The interleaving is FORCED, not raced: session A holds its locks at the point
    of commit until B has entered `remove_member`. Driven with a plain barrier
    instead, both orderings occur and the test passes against code with no row
    lock at all — which is the whole failure mode being pinned here.
    """
    maker = sessionmaker(bind=probe_engine)
    with maker() as setup:
        first = _user(setup, _addr("admin-a"), role="admin")
        second = _user(setup, _addr("admin-b"), role="admin")
        svc.add_member(setup, email=_addr("seed"), initial_role="member", actor=first)
        ids = {m.email: m.id for m in svc.list_members(setup).members}
        first_member_id, second_member_id = ids[first.email], ids[second.email]
        first_id, second_id = first.id, second.id

    a_holds_locks = threading.Event()
    b_entered = threading.Event()
    outcomes: list[str] = []
    lock = threading.Lock()

    def record(result: str) -> None:
        with lock:
            outcomes.append(result)

    def run_a() -> None:
        session: Session = maker()
        real_commit = session.commit

        def commit_after_b_tries() -> None:
            a_holds_locks.set()
            b_entered.wait(timeout=5)
            # B is inside `remove_member`; give it a moment to reach the lock it
            # either does or does not take.
            time.sleep(0.5)
            real_commit()

        session.commit = commit_after_b_tries  # type: ignore[method-assign]
        try:
            actor = _require(session.get(User, first_id))
            svc.remove_member(session, second_member_id, actor=actor)
            record("removed")
        except svc.MembershipChangeRejectedError:
            a_holds_locks.set()
            record("refused")
        finally:
            session.close()

    def run_b() -> None:
        session: Session = maker()
        try:
            actor = _require(session.get(User, second_id))
            a_holds_locks.wait(timeout=5)
            b_entered.set()
            svc.remove_member(session, first_member_id, actor=actor, confirm_self=True)
            record("removed")
        except svc.MembershipChangeRejectedError:
            record("refused")
        finally:
            session.close()

    threads = [threading.Thread(target=run_a), threading.Thread(target=run_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert outcomes.count("removed") == 1
    assert outcomes.count("refused") == 1
    with maker() as reader:
        admin_emails = {
            r.email.lower() for r in reader.scalars(select(User).where(User.role == "admin")).all()
        }
        remaining = {
            r.email.lower()
            for r in reader.scalars(select(WorkspaceMember)).all()
            if r.email.lower() in admin_emails
        }
    # The workspace still has an admin who can get back in.
    assert len(remaining) == 1


# ── the cross-table admin invariant ───────────────────────────────────────────


def test_the_two_tables_cannot_be_emptied_of_admins_one_step_at_a_time(db_session: Any) -> None:
    """The gap a per-table guard leaves: each step is legal on its own table.

    Remove B's membership (A is still a stored admin, so `users` is fine), then
    demote A (`workspace_members` is not consulted by the role editor) — and
    nobody who can sign in is an admin any more.
    """
    from backend.app.services import admin_service

    admin_a = _user(db_session, _addr("admin-a"), role="admin")
    admin_b = _user(db_session, _addr("admin-b"), role="admin")
    # The first add turns enforcement on and imports B as a provisional member.
    svc.add_member(db_session, email=admin_a.email, initial_role="admin", actor=admin_a)
    b_member_id = next(
        m.id for m in svc.list_members(db_session).members if m.email == admin_b.email
    )

    # Step one is allowed: A remains an admin AND a member.
    svc.remove_member(db_session, b_member_id, actor=admin_a)

    # Step two must NOT be. B still carries `users.role = admin`, but B can no
    # longer sign in, so demoting A leaves the workspace with no usable admin.
    with pytest.raises(admin_service.RoleChangeRejectedError):
        admin_service.set_user_role(db_session, admin_a.id, new_role="viewer", actor=admin_a)


def test_a_non_member_stored_admin_does_not_satisfy_the_invariant(db_session: Any) -> None:
    """The half that makes the guard cross-table rather than a longer count."""
    from backend.app.services import admin_service

    admin_a = _user(db_session, _addr("admin-a"), role="admin")
    svc.add_member(db_session, email=admin_a.email, initial_role="admin", actor=admin_a)
    # Created AFTER the switch, so the import never saw it: a stored admin who
    # is not a member and therefore cannot get in to fix anything.
    _user(db_session, _addr("admin-ghost"), role="admin")

    with pytest.raises(admin_service.RoleChangeRejectedError):
        admin_service.set_user_role(db_session, admin_a.id, new_role="member", actor=admin_a)


# ── env-listed addresses are shown, and cannot be removed here ────────────────


def test_env_listed_addresses_are_listed_as_read_only_rows(db_session: Any) -> None:
    """Leaving them out reads as "not admitted", and removing the managed row of
    somebody the environment also names would look like a completed removal.
    """
    admin = _user(db_session, _addr("admin"), role="admin")
    svc.add_member(db_session, email=_addr("managed"), initial_role="member", actor=admin)

    view = svc.list_members(db_session, _ENV_LISTED)
    by_email = {m.email: m for m in view.members}
    env_row = by_email["on-the-allowlist@acme.io"]
    assert env_row.source == svc.ENV_MEMBER_SOURCE
    assert env_row.removable is False
    assert env_row.env_listed is True
    assert view.env_allowed_domains == ("acme.io",)


def test_removing_an_env_listed_row_names_the_variable_instead_of_404ing(
    db_session: Any,
) -> None:
    admin = _user(db_session, _addr("admin"), role="admin")
    svc.add_member(db_session, email=_addr("managed"), initial_role="member", actor=admin)
    env_row = next(
        m for m in svc.list_members(db_session, _ENV_LISTED).members if m.source == "env"
    )

    with pytest.raises(svc.MembershipChangeRejectedError) as exc:
        svc.remove_member(db_session, env_row.id, actor=admin, settings=_ENV_LISTED)
    assert "OIDC_ALLOWED_EMAILS" in str(exc.value)


def test_a_managed_row_the_environment_also_names_is_flagged(db_session: Any) -> None:
    """Removing this row does NOT revoke access, and the row has to say so."""
    admin = _user(db_session, _addr("admin"), role="admin")
    svc.add_member(db_session, email="on-the-allowlist@acme.io", initial_role="member", actor=admin)
    row = next(
        m
        for m in svc.list_members(db_session, _ENV_LISTED).members
        if m.email == "on-the-allowlist@acme.io"
    )
    assert row.source == "admin" and row.env_listed is True and row.removable is True


# ── enforcement status is honest about dev bypass ─────────────────────────────


def test_a_non_empty_table_under_dev_bypass_reports_enforced_false(db_session: Any) -> None:
    """`enforcement_active` is about the table; `enforced` is about the doors."""
    admin = _user(db_session, _addr("admin"), role="admin")
    svc.add_member(db_session, email=_addr("listed"), initial_role="member", actor=admin)
    bypass = Settings(environment="dev", auth_dev_bypass=True)

    status = svc.enforcement_status(db_session, bypass)
    assert status.enforcement_active is True
    assert status.enforced is False
    assert status.enforced_reason is not None


# ── an image ahead of its migration must not 500 every request ────────────────


def test_a_missing_table_reads_as_not_enforced() -> None:
    """Two-step deploys mean a revision can run before the table exists. The
    doors must degrade to "not enforced", which is what an empty table means.
    """
    from sqlalchemy.orm import sessionmaker

    with scratch_engine("dataq_membership_no_table_probe") as engine:
        cast(Table, WorkspaceMember.__table__).drop(engine)
        with sessionmaker(bind=engine)() as session:
            assert svc.enforcement_active(session) is False
            assert svc.is_member(session, _addr("anyone"), settings=_ENFORCING) is True
            # A configured allowlist still decides, exactly as with an empty table.
            assert svc.is_member(session, _addr("anyone"), settings=_GATED) is False


# ── the switch-on cannot miss a concurrent first sign-in ──────────────────────


def test_the_switch_on_import_cannot_miss_a_concurrent_first_sign_in(
    probe_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """READ COMMITTED lets `add_member` read `users`, then a first-ever sign-in
    commit, then `add_member` commit — leaving that person neither imported nor
    a member, and 403 on their next request. The two must serialize.

    Driven with genuinely interleaved sessions: run sequentially, the ordering
    never occurs and this passes against code holding no lock at all.
    """
    from backend.app.core import auth as auth_mod

    monkeypatch.setattr(auth_mod, "_settings", Settings(environment="prod", auth_dev_bypass=False))
    maker = sessionmaker(bind=probe_engine)
    newcomer = _addr("newcomer")
    with maker() as setup:
        actor = _user(setup, _addr("admin"), role="admin")
        setup.commit()
        actor_id = actor.id

    reached_import = threading.Event()
    signin_done = threading.Event()
    errors: list[str] = []

    def switch_on() -> None:
        session: Session = maker()
        try:
            real = session.commit

            def commit_after_the_signin_tries() -> None:
                reached_import.set()
                # The sign-in thread is now inside `_upsert_user`. If it can
                # commit while we hold nothing, its row is invisible to the
                # import that already ran.
                signin_done.wait(timeout=5)
                real()

            session.commit = commit_after_the_signin_tries  # type: ignore[method-assign]
            admin = _require(session.get(User, actor_id))
            svc.add_member(session, email=_addr("seed"), initial_role="member", actor=admin)
        except Exception as exc:  # pragma: no cover - reported through `errors`
            errors.append(f"switch_on: {exc}")
        finally:
            session.close()

    def first_sign_in() -> None:
        session: Session = maker()
        try:
            reached_import.wait(timeout=5)
            auth_mod._upsert_user(
                session,
                aad_object_id=uuid.uuid4().hex,
                email=newcomer,
                display_name=None,
            )
        except Exception as exc:  # the honest alternative outcome, see below
            errors.append(f"sign_in: {type(exc).__name__}")
        finally:
            signin_done.set()
            session.close()

    threads = [threading.Thread(target=switch_on), threading.Thread(target=first_sign_in)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    with maker() as reader:
        members = {m.email.lower() for m in reader.scalars(select(WorkspaceMember)).all()}
        signed_in = reader.scalars(select(User).where(func.lower(User.email) == newcomer)).first()

    # Two outcomes are correct, and the wedged third one is what this pins:
    # either the sign-in landed first and the import admitted it, or it was held
    # until enforcement was on and refused outright. Never "row exists, not a
    # member".
    if signed_in is not None:
        assert newcomer in members, "a user row exists that the import never admitted"
