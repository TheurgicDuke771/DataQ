"""Dashboard read API — the Enhanced Monitoring Dashboard summary (Week 6, ADR 0022)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import ConfigDict
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel
from backend.app.api.v1.assets import ScorecardRead
from backend.app.core.auth import get_current_user
from backend.app.core.roles import is_workspace_admin
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.services import asset_view_service, coverage_service, onboarding_service
from backend.app.services import dashboard_service as svc

router = APIRouter(tags=["dashboard"])

_WINDOW_DEFAULT = 7
_WINDOW_MAX = 90


class KpisRead(ApiModel):
    health_score: float | None
    pass_rate: float | None
    total_runs: int
    active_connections: int
    # #352 enrichments — avg run duration + period-over-period deltas vs the previous equivalent
    # window.
    avg_duration_ms: float | None
    health_score_delta: float | None
    pass_rate_delta: float | None
    total_runs_delta_pct: float | None
    avg_duration_delta_pct: float | None


class TrendPointRead(ApiModel):
    day: date
    succeeded: int
    failed: int


class SuitePerformanceRead(ApiModel):
    suite_id: uuid.UUID
    name: str
    score: float | None
    state: str  # optimal | stable | critical | unknown


class DashboardSummaryRead(ApiModel):
    window_days: int
    kpis: KpisRead
    trend: list[TrendPointRead]
    suite_performance: list[SuitePerformanceRead]


@router.get(
    "/dashboard/summary",
    response_model=DashboardSummaryRead,
    summary="Dashboard summary — KPIs, run trend, per-suite performance",
)
def get_dashboard_summary(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    window_days: Annotated[int, Query(ge=1, le=_WINDOW_MAX)] = _WINDOW_DEFAULT,
) -> DashboardSummaryRead:
    summary = svc.dashboard_summary(
        db,
        user_id=current_user.id,
        window_days=window_days,
        include_all=is_workspace_admin(current_user),
    )
    return DashboardSummaryRead(
        window_days=summary.window_days,
        kpis=KpisRead(
            health_score=summary.kpis.health_score,
            pass_rate=summary.kpis.pass_rate,
            total_runs=summary.kpis.total_runs,
            active_connections=summary.kpis.active_connections,
            avg_duration_ms=summary.kpis.avg_duration_ms,
            health_score_delta=summary.kpis.health_score_delta,
            pass_rate_delta=summary.kpis.pass_rate_delta,
            total_runs_delta_pct=summary.kpis.total_runs_delta_pct,
            avg_duration_delta_pct=summary.kpis.avg_duration_delta_pct,
        ),
        trend=[
            TrendPointRead(day=p.day, succeeded=p.succeeded, failed=p.failed) for p in summary.trend
        ],
        suite_performance=[
            SuitePerformanceRead(suite_id=s.suite_id, name=s.name, score=s.score, state=s.state)
            for s in summary.suite_performance
        ],
    )


@router.get(
    "/dashboard/dimensions",
    response_model=ScorecardRead,
    summary="Each DQ dimension across every suite in the workspace",
)
def get_workspace_dimensions(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> asset_view_service.Scorecard:
    """One row per DQ dimension that has checks anywhere; `uncovered` lists the
    dimensions no suite has a check for. Each suite is scored from its latest run, and
    only if that run completed: a suite that is mid-run, failed or was cancelled keeps
    its checks in `checks_total` but adds nothing to the score until its next completed
    run. An earlier completed run is not used in its place.

    **Workspace-wide, unlike `/dashboard/summary`**: it covers every suite, including
    ones the caller cannot open, and is identical for every member. Checks with no
    dimension are counted in `unclassified_checks` and are in no row.
    """
    return asset_view_service.workspace_scorecard(db)


class CoverageFiguresRead(ApiModel):
    """How much of the inventory is watched, and how often automatic checks cry wolf.

    Workspace-wide, like the dimension rollup: identical for every member, counts only.

    `coverage_pct` is `assets_watched / assets_total`: assets with at least one suite
    that completed a run in the last `coverage_window_days`. `assets_watched_authored`
    are watched by a suite a person authored; `assets_watched_auto_only` only by
    automatic coverage. NULL when the workspace has no assets.

    `false_positive_rate` is `false_positive / stated`, over automatic-suite incidents a
    person resolved in the last `false_positive_window_days`. `stated` counts those
    resolved WITH a stated resolution; `unstated` those without. NULL when nothing was
    stated — which is "not measured", not "no false positives". Auto-resolved incidents
    are in none of these numbers.
    """

    model_config = ConfigDict(from_attributes=True)

    assets_total: int
    assets_watched: int
    assets_watched_authored: int
    assets_watched_auto_only: int
    coverage_pct: float | None
    coverage_window_days: int
    false_positive_window_days: int
    resolved: int
    stated: int
    unstated: int
    false_positive: int
    false_positive_rate: float | None


@router.get(
    "/dashboard/coverage",
    response_model=CoverageFiguresRead,
    summary="Coverage of the asset inventory and the false-positive rate of automatic checks",
)
def get_coverage_figures(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    false_positive_window_days: Annotated[int, Query(ge=1, le=_WINDOW_MAX)] = 30,
) -> coverage_service.CoverageFigures:
    return coverage_service.coverage_figures(
        db, false_positive_window_days=false_positive_window_days
    )


class OnboardingStatusRead(ApiModel):
    """Which first-run steps the workspace has done.

    Workspace-wide: true if ANY connection, suite, check or run exists, including ones the
    caller cannot open, so every member sees the same answer. Booleans only. An
    orchestration connection (ADF, Airflow, dbt) does not count as a data source, and
    suites, checks and runs that automatic coverage made do not count: the steps are about
    a person authoring them.
    """

    model_config = ConfigDict(from_attributes=True)

    has_datasource: bool
    has_suite: bool
    has_check: bool
    has_run: bool
    complete: bool


@router.get(
    "/dashboard/onboarding",
    response_model=OnboardingStatusRead,
    summary="Which first-run steps the workspace has done",
)
def get_onboarding_status(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> onboarding_service.OnboardingStatus:
    return onboarding_service.onboarding_status(db)
