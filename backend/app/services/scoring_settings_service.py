"""Health-score penalty weights as a workspace setting (#1559, ADR 0005 amendment).

`weights(session)` is the ONE resolver every score computation reads: the row if
present, the ADR 0005 defaults otherwise. Scores are computed on read and never
stored, so a change recolours every score — dashboards, trend deltas, suite
ranking, asset scorecards — at once; the audit event is the only trace of that
step. No cache: read per request so a save applies without a restart.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import ScoringSetting, User
from backend.app.services import audit_service

log = get_logger(__name__)

_SETTINGS_ROW_ID = 1


@dataclass(frozen=True)
class Weights:
    warn: float
    fail: float
    critical: float

    def penalty(self, status: str) -> float:
        return 0.0 if status == "pass" else float(getattr(self, status))


DEFAULT_WEIGHTS = Weights(warn=0.5, fail=1.0, critical=2.0)

MAX_WEIGHT = 100.0


class ScoringWeightsInvalidError(DataQError):
    code = "scoring_weights_invalid"
    status_code = 422


def validate(warn: float, fail: float, critical: float) -> None:
    """0 ≤ warn ≤ fail ≤ critical, critical > 0, all ≤ MAX_WEIGHT. Mirrors the
    table CHECK so the refusal is a 422 with a reason, not an IntegrityError."""
    if not all(0 <= v <= MAX_WEIGHT for v in (warn, fail, critical)):
        raise ScoringWeightsInvalidError(f"each weight must be between 0 and {MAX_WEIGHT:g}")
    if not warn <= fail <= critical:
        raise ScoringWeightsInvalidError("weights must satisfy warn ≤ fail ≤ critical")
    if critical <= 0:
        raise ScoringWeightsInvalidError(
            "critical must be greater than 0 — it normalises the score"
        )


def get_row(session: Session) -> ScoringSetting | None:
    return session.get(ScoringSetting, _SETTINGS_ROW_ID)


def weights_of(row: ScoringSetting | None) -> Weights:
    if row is None:
        return DEFAULT_WEIGHTS
    return Weights(
        warn=float(row.warn_weight),
        fail=float(row.fail_weight),
        critical=float(row.critical_weight),
    )


def weights(session: Session) -> Weights:
    return weights_of(get_row(session))


def set_weights(
    session: Session, *, warn: float, fail: float, critical: float, actor: User
) -> ScoringSetting:
    validate(warn, fail, critical)
    row = get_row(session)
    before = audit_service.snapshot("scoring_setting", row) if row is not None else None
    if row is None:
        row = ScoringSetting(id=_SETTINGS_ROW_ID)
        session.add(row)
    row.warn_weight = Decimal(str(warn))
    row.fail_weight = Decimal(str(fail))
    row.critical_weight = Decimal(str(critical))
    row.updated_by = actor.id
    session.flush()
    audit_service.record(
        session,
        action="scoring_setting.update",
        entity_type="scoring_setting",
        entity_id=None,
        actor=actor,
        before=before,
        after=audit_service.snapshot("scoring_setting", row),
    )
    log.info("scoring_weights_saved", warn=warn, fail=fail, critical=critical)
    return row


def reset_weights(session: Session, *, actor: User) -> None:
    """Back to the ADR 0005 defaults by deleting the row, audited like a change."""
    row = get_row(session)
    if row is None:
        return
    before = audit_service.snapshot("scoring_setting", row)
    session.delete(row)
    session.flush()
    audit_service.record(
        session,
        action="scoring_setting.update",
        entity_type="scoring_setting",
        entity_id=None,
        actor=actor,
        before=before,
        after=None,
    )
    log.info("scoring_weights_reset")
