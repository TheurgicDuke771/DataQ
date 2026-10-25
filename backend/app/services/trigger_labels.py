"""Display form of `runs.triggered_by` (#1735). The marker stays the correlation key."""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import SUITE_TRIGGER_PROVIDER, Suite, User
from backend.app.orchestration.markers import parse_suite_marker
from backend.app.services import suite_service

_USER_KINDS = {"manual": "Manual", "mcp": "MCP", "probe": "Probe"}
_SUITE_LABEL = "Suite"
PROVIDER_LABELS = {"adf": "Azure Data Factory", "airflow": "Airflow", "dbt": "dbt"}
_SCHEDULE_LABEL = "Schedule"


def _suite_id(marker: str) -> uuid.UUID | None:
    parsed = parse_suite_marker(marker)
    return parsed[0] if parsed else None


def _user_id(marker: str) -> uuid.UUID | None:
    kind, _, rest = marker.partition(":")
    if kind not in _USER_KINDS:
        return None
    try:
        return uuid.UUID(rest)
    except ValueError:
        return None


def trigger_labels(
    session: Session,
    markers: Iterable[str | None],
    *,
    viewer_id: uuid.UUID | None = None,
    include_all: bool = False,
) -> dict[str, str]:
    """Marker -> label, one user query for the whole set. A marker of an unknown shape
    is absent, so the caller shows it as stored. A ``suite:`` marker names its upstream
    suite only when ``viewer_id`` can see that suite (ADR 0051 — a run the viewer may
    read must not leak the name of a suite they hold no grant on); with no viewer it is
    labelled "Suite" alone.
    """
    wanted = {m for m in markers if m}
    user_ids = {uid for m in wanted if (uid := _user_id(m)) is not None}
    suite_ids = {sid for m in wanted if (sid := _suite_id(m)) is not None}
    suite_names: dict[uuid.UUID, str] = {}
    if suite_ids and viewer_id is not None:
        visible = suite_service.accessible_suite_ids(viewer_id, include_all=include_all)
        rows = session.execute(
            select(Suite.id, Suite.name).where(Suite.id.in_(suite_ids), Suite.id.in_(visible))
        )
        suite_names = {row.id: row.name for row in rows}
    names: dict[uuid.UUID, str] = {}
    if user_ids:
        names = {
            uid: display_name or email
            for uid, display_name, email in session.execute(
                select(User.id, User.display_name, User.email).where(User.id.in_(user_ids))
            )
        }
    labels: dict[str, str] = {}
    for marker in wanted:
        kind, sep, rest = marker.partition(":")
        if kind in _USER_KINDS:
            uid = _user_id(marker)
            name = names.get(uid) if uid is not None else None
            labels[marker] = f"{_USER_KINDS[kind]} — {name}" if name else _USER_KINDS[kind]
        elif kind == "schedule":
            labels[marker] = _SCHEDULE_LABEL
        elif kind == SUITE_TRIGGER_PROVIDER:
            # ADR 0051: `suite:<upstream suite>:<upstream run>` — the one marker that splits.
            sid = _suite_id(marker)
            name = suite_names.get(sid) if sid is not None else None
            labels[marker] = f"{_SUITE_LABEL} — {name}" if name else _SUITE_LABEL
        elif kind in PROVIDER_LABELS and sep:
            # The pipeline id and the provider's run id cannot be split apart (see
            # `orchestration.markers`), so the remainder is shown whole.
            labels[marker] = f"{PROVIDER_LABELS[kind]} — {rest}"
    return labels
