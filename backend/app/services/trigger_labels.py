"""Display form of `runs.triggered_by` (#1735). The marker stays the correlation key."""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import User

_USER_KINDS = {"manual": "Manual", "mcp": "MCP", "probe": "Probe"}
PROVIDER_LABELS = {"adf": "Azure Data Factory", "airflow": "Airflow", "dbt": "dbt"}
_SCHEDULE_LABEL = "Schedule"


def _user_id(marker: str) -> uuid.UUID | None:
    kind, _, rest = marker.partition(":")
    if kind not in _USER_KINDS:
        return None
    try:
        return uuid.UUID(rest)
    except ValueError:
        return None


def trigger_labels(session: Session, markers: Iterable[str | None]) -> dict[str, str]:
    """Marker -> label, one user query for the whole set. A marker of an unknown shape
    is absent, so the caller shows it as stored.
    """
    wanted = {m for m in markers if m}
    user_ids = {uid for m in wanted if (uid := _user_id(m)) is not None}
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
        elif kind in PROVIDER_LABELS and sep:
            # The pipeline id and the provider's run id cannot be split apart (see
            # `orchestration.markers`), so the remainder is shown whole.
            labels[marker] = f"{PROVIDER_LABELS[kind]} — {rest}"
    return labels
