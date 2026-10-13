"""Sensitivity for automatic baselines (ADR 0047, 2026-10-13 amendment).

One document shape at two levels: the workspace row (``coverage_settings``) and the per-table
override (``assets.auto_coverage_config``). ``resolve`` is the ONE place the hierarchy is
applied: asset override, then workspace setting, then the built-in default.

A level sets a group whole or not at all (one baseline's three thresholds, or the three shape
values), so each group is valid where it is saved and no merge across levels can break one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Literal, get_args

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.datasources.monitors import (
    ANOMALY_DEGENERATE_Z,
    ROW_COUNT_METRIC,
    MonitorConfigError,
    anomaly_params,
)
from backend.app.db.models import CoverageSetting, User
from backend.app.services import audit_service

log = get_logger(__name__)

_SETTINGS_ROW_ID = 1

#: The automatic baselines that carry deviation thresholds (the roles in ``auto_state``).
Baseline = Literal["volume", "freshness", "profile"]
BASELINES: Final[tuple[str, ...]] = get_args(Baseline)
_TIERS: Final[tuple[str, ...]] = ("warn", "fail", "critical")
_SHAPE_KEYS: Final[tuple[str, ...]] = ("window", "min_points", "seasonality")

Level = Literal["asset", "workspace", "default"]


class CoverageSettingsInvalidError(DataQError):
    code = "coverage_settings_invalid"
    status_code = 422


@dataclass(frozen=True)
class Sensitivity:
    warn: float
    fail: float
    critical: float

    def as_dict(self) -> dict[str, float]:
        return {"warn": self.warn, "fail": self.fail, "critical": self.critical}


@dataclass(frozen=True)
class Shape:
    window: int
    min_points: int
    seasonality: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "window": self.window,
            "min_points": self.min_points,
            "seasonality": self.seasonality,
        }


DEFAULT_SENSITIVITY: Final = Sensitivity(warn=3, fail=4, critical=6)
DEFAULT_SHAPE: Final = Shape(window=8, min_points=4, seasonality=True)


@dataclass(frozen=True)
class Effective:
    """What a table's automatic baselines run on, and the level each group came from."""

    sensitivity: dict[str, Sensitivity]
    sensitivity_from: dict[str, Level]
    shape: Shape
    shape_from: Level


def _only_keys(value: Any, allowed: tuple[str, ...], what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CoverageSettingsInvalidError(f"{what} must be an object")
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise CoverageSettingsInvalidError(
            f"{what} has unknown keys: {', '.join(str(k)[:40] for k in unknown[:5])}"
        )
    return value


def _sensitivity(value: Any, baseline: str) -> Sensitivity:
    what = f"sensitivity.{baseline}"
    group = _only_keys(value, _TIERS, what)
    if set(group) != set(_TIERS):
        raise CoverageSettingsInvalidError(f"{what} must set warn, fail and critical together")
    numbers = []
    for tier in _TIERS:
        raw = group[tier]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise CoverageSettingsInvalidError(f"{what}.{tier} must be a number")
        # A history with no spread reports a change as this fixed deviation, so a tier above
        # it could never fire on a table whose value had never moved before.
        if not 0 < raw <= ANOMALY_DEGENERATE_Z:
            raise CoverageSettingsInvalidError(
                f"{what}.{tier} must be above 0 and at most {ANOMALY_DEGENERATE_Z:g} "
                "(the deviation reported when a value that never varied changes)"
            )
        numbers.append(float(raw))
    warn, fail, critical = numbers
    if not warn <= fail <= critical:
        raise CoverageSettingsInvalidError(f"{what} must satisfy warn ≤ fail ≤ critical")
    return Sensitivity(warn=warn, fail=fail, critical=critical)


def _shape(value: Any) -> Shape:
    group = _only_keys(value, _SHAPE_KEYS, "baseline")
    if set(group) != set(_SHAPE_KEYS):
        raise CoverageSettingsInvalidError(
            "baseline must set window, min_points and seasonality together"
        )
    try:
        # The anomaly kind's own parser, so a shape that saves is a shape that runs.
        params = anomaly_params({"target_metric": ROW_COUNT_METRIC, **group})
    except MonitorConfigError as exc:
        raise CoverageSettingsInvalidError(str(exc)[:300]) from exc
    return Shape(window=params.window, min_points=params.min_points, seasonality=params.seasonality)


def validate(document: Any) -> dict[str, Any]:
    """The normalised document, or a 422 naming the first thing wrong with it."""
    top = _only_keys(document, ("sensitivity", "baseline"), "coverage settings")
    out: dict[str, Any] = {}
    if "sensitivity" in top:
        per_baseline = _only_keys(top["sensitivity"], BASELINES, "sensitivity")
        if per_baseline:
            out["sensitivity"] = {
                b: _sensitivity(group, b).as_dict() for b, group in per_baseline.items()
            }
    if "baseline" in top:
        out["baseline"] = _shape(top["baseline"]).as_dict()
    return out


def _stored(document: dict[str, Any] | None) -> tuple[dict[str, Sensitivity], Shape | None]:
    """A stored document's groups. It was validated on the way in; a group that no longer
    parses (a tightened rule) is skipped, so the level below applies instead of every run
    of the table erroring."""
    sensitivity: dict[str, Sensitivity] = {}
    shape: Shape | None = None
    if not isinstance(document, dict):
        return sensitivity, shape
    groups = document.get("sensitivity")
    for baseline in BASELINES:
        if isinstance(groups, dict) and baseline in groups:
            try:
                sensitivity[baseline] = _sensitivity(groups[baseline], baseline)
            except CoverageSettingsInvalidError:
                log.warning("coverage_settings_group_unreadable", group=baseline)
    if "baseline" in document:
        try:
            shape = _shape(document["baseline"])
        except CoverageSettingsInvalidError:
            log.warning("coverage_settings_group_unreadable", group="baseline")
    return sensitivity, shape


def resolve(workspace: dict[str, Any] | None, asset: dict[str, Any] | None = None) -> Effective:
    """Asset override, then workspace setting, then the built-in default, per group."""
    asset_sens, asset_shape = _stored(asset)
    ws_sens, ws_shape = _stored(workspace)
    sensitivity: dict[str, Sensitivity] = {}
    sensitivity_from: dict[str, Level] = {}
    for baseline in BASELINES:
        if baseline in asset_sens:
            sensitivity[baseline], sensitivity_from[baseline] = asset_sens[baseline], "asset"
        elif baseline in ws_sens:
            sensitivity[baseline], sensitivity_from[baseline] = ws_sens[baseline], "workspace"
        else:
            sensitivity[baseline], sensitivity_from[baseline] = DEFAULT_SENSITIVITY, "default"
    shape_from: Level
    if asset_shape is not None:
        shape, shape_from = asset_shape, "asset"
    elif ws_shape is not None:
        shape, shape_from = ws_shape, "workspace"
    else:
        shape, shape_from = DEFAULT_SHAPE, "default"
    return Effective(
        sensitivity=sensitivity,
        sensitivity_from=sensitivity_from,
        shape=shape,
        shape_from=shape_from,
    )


def get_row(session: Session) -> CoverageSetting | None:
    return session.get(CoverageSetting, _SETTINGS_ROW_ID)


def workspace_document(session: Session) -> dict[str, Any]:
    row = get_row(session)
    return dict(row.config) if row is not None else {}


def set_workspace(session: Session, document: Any, *, actor: User) -> CoverageSetting | None:
    """Store the workspace setting. A document that sets no group is a reset: a stored row
    always means something differs from the built-in defaults."""
    config = validate(document)
    if not config:
        reset_workspace(session, actor=actor)
        return None
    # Two first saves at once must not both INSERT the singleton: claim the row, then lock it.
    created = session.scalar(
        pg_insert(CoverageSetting)
        .values(id=_SETTINGS_ROW_ID, config={})
        .on_conflict_do_nothing(index_elements=["id"])
        .returning(CoverageSetting.id)
    )
    row = session.get(CoverageSetting, _SETTINGS_ROW_ID, with_for_update=True)
    if row is None:  # unreachable: just inserted or already present, and now locked
        raise RuntimeError("coverage_settings row vanished between insert and lock")
    before = None if created is not None else audit_service.snapshot("coverage_setting", row)
    row.config = config
    row.updated_by = actor.id
    session.flush()
    audit_service.record(
        session,
        action="coverage_setting.update",
        entity_type="coverage_setting",
        entity_id=None,
        actor=actor,
        before=before,
        after=audit_service.snapshot("coverage_setting", row),
    )
    log.info("coverage_settings_saved", groups=sorted(config))
    return row


def reset_workspace(session: Session, *, actor: User) -> None:
    """Back to the built-in defaults by deleting the row, audited like a change."""
    row = get_row(session)
    if row is None:
        return
    before = audit_service.snapshot("coverage_setting", row)
    session.delete(row)
    session.flush()
    audit_service.record(
        session,
        action="coverage_setting.update",
        entity_type="coverage_setting",
        entity_id=None,
        actor=actor,
        before=before,
        after=None,
    )
    log.info("coverage_settings_reset")
