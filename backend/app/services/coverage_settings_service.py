"""Sensitivity for automatic baselines (ADR 0047, 2026-10-13 amendment).

One document shape at two levels: the workspace row (``coverage_settings``) and the per-table
override (``assets.auto_coverage_config``). ``resolve`` is the ONE place the hierarchy is
applied: asset override, then workspace setting, then the built-in default.

A level sets a group whole or not at all (one baseline's three thresholds, or the three shape
values), so each group is valid where it is saved and no merge across levels can break one.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final, Literal, get_args

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.datasources.monitors import (
    ANOMALY,
    ANOMALY_DEGENERATE_Z,
    COLUMN_PROFILE_METRIC,
    FRESHNESS_AGE_METRIC,
    ROW_COUNT_METRIC,
    MonitorConfigError,
    anomaly_params,
)
from backend.app.db.models import Asset, Check, CoverageSetting, Suite, User
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


# ───────────────────────── run-time inheritance ─────────────────────────

#: Server-owned key in an automatic anomaly check's config: ``true`` = each run resolves its
#: thresholds and shape through the hierarchy; ``false`` = a person edited them, so its own
#: stored values apply.
INHERIT_KEY: Final = "inherit_coverage_settings"

_BASELINE_BY_METRIC: Final[dict[str, str]] = {
    ROW_COUNT_METRIC: "volume",
    FRESHNESS_AGE_METRIC: "freshness",
    COLUMN_PROFILE_METRIC: "profile",
}

# What the reconciler wrote before the flag existed. Fixed here, apart from the built-in
# defaults, so changing a default later does not change which old checks inherit.
_LEGACY_SENSITIVITY: Final = Sensitivity(warn=3, fail=4, critical=6)
_LEGACY_SHAPE: Final = Shape(window=8, min_points=4, seasonality=True)


@dataclass(frozen=True)
class CheckSettings:
    """What one inheriting check runs on, and the level each group came from."""

    sensitivity: Sensitivity
    sensitivity_from: Level
    shape: Shape
    shape_from: Level

    def config(self, stored: dict[str, Any]) -> dict[str, Any]:
        """The check's config with the resolved shape in place of the stored one."""
        return {**stored, **self.shape.as_dict()}

    def provenance(self) -> dict[str, Any]:
        """Recorded on each result: the values it was scored against and where they came from."""
        return {
            "thresholds": self.sensitivity.as_dict(),
            "inherited_from": {"sensitivity": self.sensitivity_from, "baseline": self.shape_from},
        }


def baseline_of(check: Check) -> str | None:
    """Which automatic baseline an anomaly check is, by the metric it watches."""
    if check.kind != ANOMALY:
        return None
    return _BASELINE_BY_METRIC.get(str((check.config or {}).get("target_metric")))


def _number(value: Any) -> float | None:
    return None if value is None else float(value)


def _matches_legacy_signature(check: Check) -> bool:
    config = check.config or {}
    stored = (
        _number(check.warn_threshold),
        _number(check.fail_threshold),
        _number(check.critical_threshold),
    )
    legacy = _LEGACY_SENSITIVITY
    return stored == (legacy.warn, legacy.fail, legacy.critical) and all(
        # `is` for the flag: 1 == True, and a window of 1 must not read as seasonality on.
        type(config.get(key)) is type(want) and config.get(key) == want
        for key, want in _LEGACY_SHAPE.as_dict().items()
    )


def inherits(check: Check) -> bool:
    """Whether this check takes its thresholds and shape from the settings rather than from
    its own stored values. Only an automatic baseline can."""
    if check.origin != "auto" or baseline_of(check) is None:
        return False
    flag = (check.config or {}).get(INHERIT_KEY)
    if isinstance(flag, bool):
        return flag
    # No flag: created before it existed. Inherits while nobody has tuned it.
    return _matches_legacy_signature(check)


@dataclass(frozen=True)
class SuiteCoverage:
    """The settings in effect for one suite's table. Built once per run or request."""

    effective: Effective

    def for_check(self, check: Check) -> CheckSettings | None:
        """What ``check`` runs on if it inherits; ``None`` when its own stored values apply."""
        baseline = baseline_of(check)
        if baseline is None or not inherits(check):
            return None
        return CheckSettings(
            sensitivity=self.effective.sensitivity[baseline],
            sensitivity_from=self.effective.sensitivity_from[baseline],
            shape=self.effective.shape,
            shape_from=self.effective.shape_from,
        )


class InheritEditError(ValueError):
    """An edit that cannot be reconciled with the check's inheritance; the message is safe
    to show the author."""


def strip_flag(config: dict[str, Any]) -> dict[str, Any]:
    """A config for a check that cannot inherit (anything a person authors or imports)."""
    return {k: v for k, v in config.items() if k != INHERIT_KEY}


def validate_new_check(config: dict[str, Any], *, kind: str, origin: str) -> None:
    """The flag is server-owned: only the coverage loop may create a check that carries it."""
    if INHERIT_KEY not in config:
        return
    if not isinstance(config[INHERIT_KEY], bool):
        raise InheritEditError(f"{INHERIT_KEY} must be true or false")
    is_baseline = kind == ANOMALY and str(config.get("target_metric")) in _BASELINE_BY_METRIC
    if origin != "auto" or not is_baseline:
        raise InheritEditError(
            f"{INHERIT_KEY} applies only to an automatic baseline check; remove it"
        )


def _same(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return float(a) == float(b)


def settle_edit(
    check: Check,
    coverage: SuiteCoverage,
    *,
    sent_config: dict[str, Any] | None,
    sent_thresholds: Mapping[str, Decimal | None],
) -> tuple[dict[str, Any], dict[str, Decimal | None]]:
    """The config and thresholds an edit leaves on ``check``, with the inherit flag settled.

    ``sent_thresholds`` holds only the tiers the edit names (``warn`` / ``fail`` /
    ``critical``). The rule (ADR 0047): an edit that changes a threshold or the shape of an
    inheriting check pins all of them at the values then in effect; one that does not leaves
    it inheriting. A sent flag decides outright: ``false`` pins, ``true`` (re)starts
    inheriting, and ``true`` together with a changed value is refused rather than guessed at.
    """
    stored_config = dict(check.config or {})
    stored = {
        "warn": check.warn_threshold,
        "fail": check.fail_threshold,
        "critical": check.critical_threshold,
    }
    config = dict(sent_config) if sent_config is not None else stored_config
    sent_flag = config.pop(INHERIT_KEY, None) if sent_config is not None else None
    config.pop(INHERIT_KEY, None)
    if sent_flag is not None and not isinstance(sent_flag, bool):
        raise InheritEditError(f"{INHERIT_KEY} must be true or false")
    is_baseline = (
        check.origin == "auto"
        and check.kind == ANOMALY
        and str(config.get("target_metric")) in _BASELINE_BY_METRIC
    )
    if not is_baseline:
        if sent_flag is not None:
            raise InheritEditError(
                f"{INHERIT_KEY} applies only to an automatic baseline check; remove it"
            )
        return config, {**stored, **sent_thresholds}
    settings = coverage.for_check(check)
    if settings is None:
        # Its own values apply today; only an explicit `true` hands it back to the settings.
        return {**config, INHERIT_KEY: sent_flag is True}, {**stored, **sent_thresholds}

    effective = settings.sensitivity.as_dict()
    shape = settings.shape.as_dict()
    sent_shape = {k: config[k] for k in _SHAPE_KEYS if sent_config is not None and k in config}
    changed = [t for t, v in sent_thresholds.items() if not _same(v, effective[t])]
    changed += [k for k, v in sent_shape.items() if v != shape[k]]
    if sent_flag is True:
        # A document that says "inherit" carries the stored snapshot, which is not an edit.
        edits = [
            name
            for name in changed
            if not (
                _same(sent_thresholds[name], stored[name])
                if name in sent_thresholds
                else sent_shape[name] == stored_config.get(name)
            )
        ]
        if edits:
            raise InheritEditError(
                f"this check inherits its thresholds and baseline shape, but the edit also "
                f"changes {', '.join(edits)}; send {INHERIT_KEY}: false to give it its own "
                "values, or leave those unchanged to keep inheriting"
            )
        changed = []
    if sent_flag is False or changed:
        pinned_thresholds: dict[str, Decimal | None] = {
            t: Decimal(str(v)) for t, v in effective.items()
        }
        return (
            {**config, **shape, **sent_shape, INHERIT_KEY: False},
            {**pinned_thresholds, **sent_thresholds},
        )
    # Still inheriting: the stored snapshot stays as it was, whatever was echoed back.
    kept_shape = {k: stored_config.get(k, shape[k]) for k in _SHAPE_KEYS}
    return {**config, **kept_shape, INHERIT_KEY: True}, stored


def for_suite(session: Session, suite: Suite) -> SuiteCoverage:
    asset = session.get(Asset, suite.asset_id) if suite.asset_id is not None else None
    override = asset.auto_coverage_config if asset is not None else None
    return SuiteCoverage(resolve(workspace_document(session), override))


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


def inherited_settings(session: Session, checks: Iterable[Check]) -> dict[uuid.UUID, CheckSettings]:
    """What each inheriting check among ``checks`` runs on, keyed by check id. The one resolver
    behind every read surface (REST, MCP): a check absent from the result runs on its own
    stored values."""
    by_suite: dict[uuid.UUID, SuiteCoverage] = {}
    out: dict[uuid.UUID, CheckSettings] = {}
    for check in checks:
        if check.origin != "auto" or baseline_of(check) is None:
            continue
        coverage = by_suite.get(check.suite_id)
        if coverage is None:
            suite = session.get(Suite, check.suite_id)
            if suite is None:
                continue
            coverage = by_suite[check.suite_id] = for_suite(session, suite)
        settings = coverage.for_check(check)
        if settings is not None:
            out[check.id] = settings
    return out
