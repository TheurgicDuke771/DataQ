"""Dashboard read API — the Enhanced Monitoring Dashboard summary (Week 6, ADR 0022).

A single suite-scoped aggregate: KPIs (health score, pass rate, run count, active
connections), a per-day run trend, and per-suite performance. All scoping is done
in the service via the owned-or-shared accessible-suite filter, so this endpoint
is gated on authentication only (the data it returns is already scoped to the
caller). Read-only; no JSONB is reduced in Python (ADR 0005 / 0012).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.core.auth import get_current_user
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.services import dashboard_service as svc

router = APIRouter(tags=["dashboard"])

_WINDOW_DEFAULT = 7
_WINDOW_MAX = 90


class KpisRead(BaseModel):
    health_score: float | None
    pass_rate: float | None
    total_runs: int
    active_connections: int


class TrendPointRead(BaseModel):
    day: date
    succeeded: int
    failed: int


class SuitePerformanceRead(BaseModel):
    suite_id: uuid.UUID
    name: str
    score: float | None
    state: str  # optimal | stable | critical | unknown


class DashboardSummaryRead(BaseModel):
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
    summary = svc.dashboard_summary(db, user_id=current_user.id, window_days=window_days)
    return DashboardSummaryRead(
        window_days=summary.window_days,
        kpis=KpisRead(
            health_score=summary.kpis.health_score,
            pass_rate=summary.kpis.pass_rate,
            total_runs=summary.kpis.total_runs,
            active_connections=summary.kpis.active_connections,
        ),
        trend=[
            TrendPointRead(day=p.day, succeeded=p.succeeded, failed=p.failed) for p in summary.trend
        ],
        suite_performance=[
            SuitePerformanceRead(suite_id=s.suite_id, name=s.name, score=s.score, state=s.state)
            for s in summary.suite_performance
        ],
    )
