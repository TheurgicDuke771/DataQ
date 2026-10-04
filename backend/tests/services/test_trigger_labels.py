"""`runs.triggered_by` -> display label, against a real Postgres (#1735)."""

import uuid
from typing import Any

import pytest
from sqlalchemy import event

from backend.app.db.models import User
from backend.app.orchestration.registry import _PROVIDERS
from backend.app.services.trigger_labels import PROVIDER_LABELS, trigger_labels


def _user(db_session: Any, email: str, display_name: str | None = None) -> User:
    user = User(aad_object_id=uuid.uuid4().hex, email=email, display_name=display_name)
    db_session.add(user)
    db_session.flush()
    return user


@pytest.mark.parametrize(
    ("kind", "prefix"), [("manual", "Manual"), ("mcp", "MCP"), ("probe", "Probe")]
)
def test_user_marker_shows_the_display_name(db_session: Any, kind: str, prefix: str) -> None:
    user = _user(db_session, "olivia@ex", "Olivia Admin")
    marker = f"{kind}:{user.id}"

    assert trigger_labels(db_session, [marker]) == {marker: f"{prefix} — Olivia Admin"}


def test_user_without_a_display_name_shows_the_email(db_session: Any) -> None:
    user = _user(db_session, "otp-user@ex")
    marker = f"manual:{user.id}"

    assert trigger_labels(db_session, [marker]) == {marker: "Manual — otp-user@ex"}


@pytest.mark.parametrize("rest", [str(uuid.uuid4()), "u1", ""])
def test_unresolvable_user_marker_shows_the_kind_only(db_session: Any, rest: str) -> None:
    marker = f"manual:{rest}"

    assert trigger_labels(db_session, [marker]) == {marker: "Manual"}


def test_schedule_marker_hides_the_schedule_id(db_session: Any) -> None:
    marker = f"schedule:{uuid.uuid4()}"

    assert trigger_labels(db_session, [marker]) == {marker: "Schedule"}


def test_orchestration_marker_keeps_the_remainder_whole(db_session: Any) -> None:
    # An Airflow run id carries colons, so the remainder is never split.
    marker = "airflow:nightly_etl:scheduled__2026-06-01T00:00:00+00:00"

    assert trigger_labels(db_session, [marker]) == {
        marker: "Airflow — nightly_etl:scheduled__2026-06-01T00:00:00+00:00"
    }


def test_every_registered_provider_has_a_label() -> None:
    assert set(PROVIDER_LABELS) == set(_PROVIDERS)


@pytest.mark.parametrize("marker", ["seed:run:failed", "airflow", "gibberish"])
def test_unknown_shape_gets_no_label(db_session: Any, marker: str) -> None:
    assert trigger_labels(db_session, [marker, None]) == {}


def test_a_page_of_user_markers_costs_one_query(db_session: Any) -> None:
    markers = [f"manual:{_user(db_session, f'u{i}@ex').id}" for i in range(5)]
    statements: list[str] = []
    bind = db_session.get_bind()

    def _count(*args: Any) -> None:
        statements.append(args[2])

    event.listen(bind, "before_cursor_execute", _count)
    try:
        labels = trigger_labels(db_session, markers)
    finally:
        event.remove(bind, "before_cursor_execute", _count)

    assert len(labels) == 5
    assert len(statements) == 1
