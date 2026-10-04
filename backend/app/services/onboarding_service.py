"""Which first-run steps the workspace has done (#1668). Workspace-wide, booleans only."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from backend.app.db.models import ORCHESTRATION_PROVIDERS, Check, Connection, Run, Suite


@dataclass(frozen=True)
class OnboardingStatus:
    has_datasource: bool
    has_suite: bool
    has_check: bool
    has_run: bool

    @property
    def complete(self) -> bool:
        return self.has_datasource and self.has_suite and self.has_check and self.has_run


def onboarding_status(session: Session) -> OnboardingStatus:
    def any_row(condition: object) -> bool:
        return bool(session.scalar(select(exists().where(condition))))  # type: ignore[arg-type]

    return OnboardingStatus(
        # An orchestration connection is not something a check can run against.
        has_datasource=any_row(Connection.type.not_in(ORCHESTRATION_PROVIDERS)),
        # Automatic coverage (ADR 0047) creates suites, checks and runs with nobody having
        # authored anything; the steps are about a person doing so.
        has_suite=any_row(Suite.origin == "user"),
        has_check=any_row(Check.origin != "auto"),
        has_run=any_row(Run.suite_id.in_(select(Suite.id).where(Suite.origin == "user"))),
    )
