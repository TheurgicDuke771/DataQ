"""Zero-sample privacy mode as a workspace setting (#1887).

`zero_sample_mode` is the ONE resolver every sample-writing and
sample-labelling path reads. `PRIVACY_ZERO_SAMPLE_MODE` stays the fail-safe
floor and the DB row is the switch on top of it:

    effective = env OR row

so an env `true` can be turned ON from the UI but never off — the override runs
in the fail-safe direction only. There is deliberately no cache: the value is
read per request and per task so a toggle takes effect without restarting the
api and the worker (which is the whole point of the issue).
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import PrivacySetting, User
from backend.app.services import audit_service

log = get_logger(__name__)

_SETTINGS_ROW_ID = 1

#: Where the effective value comes from. `env` outranks `db` because it cannot be
#: turned off here; `off` means neither source has it on.
ZeroSampleSource = Literal["env", "db", "off"]


class ZeroSampleEnvForcedError(DataQError):
    """Turning the mode off was refused because the environment forces it on."""

    code = "zero_sample_env_forced"
    status_code = 409


def get_row(session: Session) -> PrivacySetting | None:
    return session.get(PrivacySetting, _SETTINGS_ROW_ID)


def env_forced() -> bool:
    """Whether `PRIVACY_ZERO_SAMPLE_MODE` pins the mode on regardless of the row."""
    return get_settings().privacy_zero_sample_mode


def stored_zero_sample_mode(session: Session) -> bool:
    """The row's own value — what the toggle last wrote, NOT the effective value.
    Only the admin read model should use this; every enforcement path wants
    `zero_sample_mode`.
    """
    row = get_row(session)
    return bool(row is not None and row.zero_sample_mode)


def zero_sample_mode(session: Session) -> bool:
    """The EFFECTIVE zero-sample state: env OR row.

    Every sample writer and sample-labeller routes through this — persistence
    (`run_service._build_result`), the `zero_sample` redaction label
    (`run_service.zero_sample_suppressed`, feeding the results API, MCP and
    alert payloads) and the dry-run preview. A new reader of
    `settings.privacy_zero_sample_mode` outside this module is a guard applied
    at one door and not its sibling, and a test enforces that.
    """
    return env_forced() or stored_zero_sample_mode(session)


def source(session: Session) -> ZeroSampleSource:
    if env_forced():
        return "env"
    return "db" if stored_zero_sample_mode(session) else "off"


def set_zero_sample_mode(
    session: Session,
    *,
    enabled: bool,
    actor: User,
) -> PrivacySetting:
    """Write the toggle, audited. Refuses an off request while the env forces it
    on rather than storing `false` and reporting a state the resolver would
    ignore — a setting that silently does not apply is the honesty defect this
    surface must not have.
    """
    if env_forced() and not enabled:
        raise ZeroSampleEnvForcedError(
            "zero-sample mode is forced on by PRIVACY_ZERO_SAMPLE_MODE and cannot be "
            "turned off here — clear that environment variable and redeploy",
        )
    row = get_row(session)
    before = audit_service.snapshot("privacy_setting", row) if row is not None else None
    if row is None:
        row = PrivacySetting(id=_SETTINGS_ROW_ID, zero_sample_mode=enabled)
        session.add(row)
    row.zero_sample_mode = enabled
    row.updated_by = actor.id
    session.flush()
    # `record`, not `record_entity_change`: the singleton's integer id can't ride the
    # UUID `entity_id` column — ADR 0041's "no single row" NULL case, as for llm_setting.
    audit_service.record(
        session,
        action="privacy_setting.update",
        entity_type="privacy_setting",
        entity_id=None,
        actor=actor,
        before=before,
        after=audit_service.snapshot("privacy_setting", row),
    )
    log.info("privacy_zero_sample_mode_saved", zero_sample_mode=enabled)
    return row
