"""Zero-sample mode as a workspace setting (#1887): the resolver truth table, the
one-way env override, and the guard that keeps every reader on the resolver.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.db.models import AuditEvent, PrivacySetting, User
from backend.app.services import privacy_settings_service as svc


@pytest.fixture
def user(db_session: Any) -> User:
    row = User(
        id=uuid.uuid4(),
        aad_object_id=None,
        email=f"privacy-admin-{uuid.uuid4().hex[:8]}@example.com",
        role="admin",
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _set_env(monkeypatch: pytest.MonkeyPatch, value: bool) -> None:
    monkeypatch.setenv("PRIVACY_ZERO_SAMPLE_MODE", "true" if value else "false")
    get_settings.cache_clear()


def _write_row(session: Session, value: bool) -> None:
    session.add(PrivacySetting(id=1, zero_sample_mode=value))
    session.flush()


@pytest.mark.parametrize(
    ("env", "row", "expected", "expected_source"),
    [
        (False, None, False, "off"),
        (False, False, False, "off"),
        (False, True, True, "db"),
        (True, None, True, "env"),
        (True, False, True, "env"),
        (True, True, True, "env"),
    ],
)
def test_resolver_truth_table(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    env: bool,
    row: bool | None,
    expected: bool,
    expected_source: str,
) -> None:
    """`env OR row`, with `env` reported as the source whenever it is on — the
    (env=True, row=False) line is the whole point: the row says off and the
    effective answer is still on.
    """
    _set_env(monkeypatch, env)
    if row is not None:
        _write_row(db_session, row)
    assert svc.zero_sample_mode(db_session) is expected
    assert svc.source(db_session) == expected_source


def test_stored_value_is_reported_separately_from_the_effective_one(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The admin read model needs both: what the toggle holds, and what applies."""
    _set_env(monkeypatch, True)
    _write_row(db_session, False)
    assert svc.stored_zero_sample_mode(db_session) is False
    assert svc.zero_sample_mode(db_session) is True


def test_resolver_is_not_cached_across_a_row_change(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The restart-free requirement: a write is visible to the very next read."""
    _set_env(monkeypatch, False)
    assert svc.zero_sample_mode(db_session) is False
    _write_row(db_session, True)
    assert svc.zero_sample_mode(db_session) is True


def test_set_writes_the_row_and_an_audit_event(db_session: Session, user: User) -> None:
    row = svc.set_zero_sample_mode(db_session, enabled=True, actor=user)
    db_session.flush()
    assert row.zero_sample_mode is True
    assert row.updated_by == user.id
    event = db_session.query(AuditEvent).filter_by(action="privacy_setting.update").one()
    assert event.after is not None and event.after["zero_sample_mode"] is True


def test_set_records_the_before_state_on_a_second_change(db_session: Session, user: User) -> None:
    svc.set_zero_sample_mode(db_session, enabled=True, actor=user)
    svc.set_zero_sample_mode(db_session, enabled=False, actor=user)
    db_session.flush()
    events = (
        db_session.query(AuditEvent)
        .filter_by(action="privacy_setting.update")
        .order_by(AuditEvent.occurred_at)
        .all()
    )
    assert [(e.after or {})["zero_sample_mode"] for e in events] == [True, False]
    assert events[1].before is not None and events[1].before["zero_sample_mode"] is True


def test_turning_it_off_is_refused_while_the_env_forces_it_on(
    db_session: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Storing `false` under an env-pinned deployment would report a state the
    resolver ignores, so the write is refused instead.
    """
    _set_env(monkeypatch, True)
    with pytest.raises(svc.ZeroSampleEnvForcedError):
        svc.set_zero_sample_mode(db_session, enabled=False, actor=user)
    assert svc.get_row(db_session) is None


def test_turning_it_on_is_allowed_while_the_env_forces_it_on(
    db_session: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the OFF direction is refused — the override is one-way, not read-only."""
    _set_env(monkeypatch, True)
    row = svc.set_zero_sample_mode(db_session, enabled=True, actor=user)
    assert row.zero_sample_mode is True


# The guard-at-one-door test: a new reader of the raw setting must fail here.
_APP_ROOT = Path(__file__).resolve().parents[2] / "app"
_RESOLVER = _APP_ROOT / "services" / "privacy_settings_service.py"
_CONFIG = _APP_ROOT / "core" / "config.py"
_SETTING_READ = re.compile(r"privacy_zero_sample_mode")


def test_only_the_resolver_reads_the_raw_setting() -> None:
    """`settings.privacy_zero_sample_mode` is the fail-safe FLOOR, not the
    effective value — a path that reads it directly ignores the workspace toggle
    and silently keeps writing samples after an admin turned the mode on. The two
    files allowed to name it are the declaration and the resolver itself.
    """
    offenders = [
        str(path.relative_to(_APP_ROOT))
        for path in _APP_ROOT.rglob("*.py")
        if path not in {_RESOLVER, _CONFIG} and _SETTING_READ.search(path.read_text())
    ]
    assert offenders == [], (
        "these modules name privacy_zero_sample_mode directly; read the effective "
        "value through privacy_settings_service.zero_sample_mode(session) instead: "
        f"{offenders}"
    )
