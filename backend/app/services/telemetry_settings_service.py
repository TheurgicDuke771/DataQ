"""The usage-telemetry opt-in as a workspace setting (ADR 0050, #1707).

Two gates, both required: `TELEMETRY_SINK` (the operator, env) and this row (the admin, UI).
`opt_in` is what the emitter reads on every event, through a short per-process cache so a
worker task or MCP call pays no query per event, and the admin's toggle still lands within
the cache window without a restart.
"""

from __future__ import annotations

import time
from typing import Final

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.db.models import TelemetrySetting, User
from backend.app.services import audit_service

log = get_logger(__name__)

_SETTINGS_ROW_ID: Final = 1
CACHE_TTL_S: Final = 30.0

_cached: tuple[bool, float] | None = None


def get_row(session: Session) -> TelemetrySetting | None:
    return session.get(TelemetrySetting, _SETTINGS_ROW_ID)


def stored_enabled(session: Session) -> bool:
    """The admin's stored opt-in — NOT the effective value, which also needs a sink."""
    row = get_row(session)
    return bool(row is not None and row.enabled)


def sink_active() -> bool:
    return get_settings().telemetry_sink != "off"


def sink_ready() -> bool:
    """The sink is configured AND this process built it; false when setup failed (logged)."""
    from backend.app.core import usage_telemetry

    return sink_active() and usage_telemetry.configured()


def effective(session: Session) -> bool:
    return sink_ready() and stored_enabled(session)


def invalidate() -> None:
    global _cached
    _cached = None


def opt_in(session: Session | None = None) -> bool:
    """The stored opt-in, cached for `CACHE_TTL_S`. Reads through `session` when the caller
    has one (so a toggle in the same transaction is seen), otherwise through a session of
    its own. A read that fails is `False` for this window: never emit on doubt."""
    global _cached
    now = time.monotonic()
    if _cached is not None and _cached[1] > now:
        return _cached[0]
    try:
        if session is not None:
            value = stored_enabled(session)
        else:
            from backend.app.db.session import get_session

            own = get_session()
            try:
                value = stored_enabled(own)
            finally:
                own.close()
    except Exception:
        log.warning("usage_telemetry_opt_in_unreadable", exc_info=True)
        value = False
    _cached = (value, now + CACHE_TTL_S)
    return value


def set_enabled(session: Session, *, enabled: bool, actor: User) -> TelemetrySetting:
    """Write the toggle, audited; the cache is dropped so the emitter sees it at once."""
    created = session.scalar(
        pg_insert(TelemetrySetting)
        .values(id=_SETTINGS_ROW_ID, enabled=False)
        .on_conflict_do_nothing(index_elements=["id"])
        .returning(TelemetrySetting.id)
    )
    row = session.get(TelemetrySetting, _SETTINGS_ROW_ID, with_for_update=True)
    if row is None:  # unreachable: just inserted or already present, and now locked
        raise RuntimeError("telemetry_settings row vanished between insert and lock")
    before = None if created is not None else audit_service.snapshot("telemetry_setting", row)
    row.enabled = enabled
    row.updated_by = actor.id
    session.flush()
    audit_service.record(
        session,
        action="telemetry_setting.update",
        entity_type="telemetry_setting",
        entity_id=None,
        actor=actor,
        before=before,
        after=audit_service.snapshot("telemetry_setting", row),
    )
    invalidate()
    log.info("usage_telemetry_opt_in_saved", enabled=enabled, sink_active=sink_active())
    return row
