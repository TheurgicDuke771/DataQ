"""Pipeline DQ gate: an orchestrator asks whether its pipeline may continue (ADR 0046).

Provider-agnostic: keyed by `trigger_bindings` and the `runs.triggered_by` marker, with no
provider-specific branch. DataQ never mutates the pipeline; the caller decides what to do.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import ORCHESTRATION_PROVIDERS, SEVERITY_RANK, Run, TriggerBinding
from backend.app.services import run_dispatch
from backend.app.services.orchestration_service import insert_triggered_run, trigger_marker
from backend.app.services.run_service import check_outcome_counts, operational_result_flags
from backend.app.services.suite_authz import SuiteForbiddenError, require_permission
from backend.app.services.suite_service import SuiteNotFoundError

log = get_logger(__name__)

FAIL_ON_LEVELS: Final = ("warn", "fail", "critical")

AWAITING_TRIGGER: Final = "awaiting_trigger"
RUNNING: Final = "running"
PASSED: Final = "passed"
FAILED: Final = "failed"
ERROR: Final = "error"

# A running gate is polled; this is how soon a client should ask again.
_RETRY_AFTER_SECONDS: Final = 15


class GateBindingNotFoundError(DataQError):
    status_code = 404
    code = "gate_binding_not_found"


class GateForbiddenError(DataQError):
    status_code = 403
    code = "gate_forbidden"


@dataclass(frozen=True)
class GateSuite:
    suite_id: uuid.UUID
    run_id: uuid.UUID | None
    run_status: str | None
    state: str
    checks_total: int
    checks_passed: int
    worst_severity: str | None
    has_error: bool


@dataclass(frozen=True)
class GateOutcome:
    state: str
    fail_on: str
    triggered_by: str
    suites: list[GateSuite]
    created_runs: int
    retry_after_seconds: int | None


def evaluate_gate(
    session: Session,
    *,
    user_id: uuid.UUID,
    provider: str,
    pipeline_or_dag_id: str,
    env: str,
    provider_run_id: str,
    fail_on: str = "fail",
    trigger: bool = True,
) -> GateOutcome:
    """Answer one gate request, starting the bound suites' runs first when ``trigger``."""
    if provider not in ORCHESTRATION_PROVIDERS:
        raise GateBindingNotFoundError(
            "unknown orchestration provider", detail={"provider": provider}
        )
    if fail_on not in FAIL_ON_LEVELS:
        raise DataQError(
            "fail_on must be one of warn, fail, critical",
            code="gate_fail_on_invalid",
            status_code=422,
            detail={"fail_on": fail_on},
        )
    suite_ids = _bound_suite_ids(session, user_id, provider, pipeline_or_dag_id, env, trigger)
    marker = trigger_marker(provider, pipeline_or_dag_id, provider_run_id)

    created: list[Run] = []
    if trigger:
        for suite_id in suite_ids:
            run = insert_triggered_run(session, suite_id=suite_id, marker=marker)
            if run is not None:
                created.append(run)
        if created:
            session.commit()
            for run in created:
                session.refresh(run)
                run_dispatch.dispatch_or_fail(
                    session, run, provider=provider, pipeline=pipeline_or_dag_id
                )
            log.info(
                "gate_runs_triggered",
                provider=provider,
                pipeline=pipeline_or_dag_id,
                run_marker=marker,
                count=len(created),
            )

    suites = _suite_states(session, suite_ids, marker, fail_on)
    state = _overall(suites)
    return GateOutcome(
        state=state,
        fail_on=fail_on,
        triggered_by=marker,
        suites=suites,
        created_runs=len(created),
        retry_after_seconds=_RETRY_AFTER_SECONDS if state in (RUNNING, AWAITING_TRIGGER) else None,
    )


def _bound_suite_ids(
    session: Session,
    user_id: uuid.UUID,
    provider: str,
    pipeline_or_dag_id: str,
    env: str,
    trigger: bool,
) -> list[uuid.UUID]:
    """The enabled bound suites, all of which the caller must be able to act on.

    A verdict that silently skipped a suite the caller can't see would pass a pipeline that
    one of its bound suites says to stop, so any such suite refuses the gate outright.
    """
    suite_ids = sorted(
        set(
            session.scalars(
                select(TriggerBinding.suite_id).where(
                    TriggerBinding.provider == provider,
                    TriggerBinding.pipeline_or_dag_id == pipeline_or_dag_id,
                    TriggerBinding.env == env,
                    TriggerBinding.enabled.is_(True),
                )
            )
        )
    )
    minimum = "edit" if trigger else "view"
    visible: list[uuid.UUID] = []
    refused = False
    for suite_id in suite_ids:
        try:
            require_permission(session, suite_id, user_id, minimum=minimum)
        except SuiteNotFoundError:
            refused = True
            continue
        except SuiteForbiddenError:
            refused = True
        visible.append(suite_id)
    if not visible:
        raise GateBindingNotFoundError(
            "no enabled trigger binding for this pipeline",
            detail={"provider": provider, "pipeline_or_dag_id": pipeline_or_dag_id, "env": env},
        )
    if refused:
        raise GateForbiddenError(
            f"the gate needs {minimum!r} permission on every suite bound to this pipeline",
            detail={"need": minimum},
        )
    return visible


def _suite_states(
    session: Session, suite_ids: list[uuid.UUID], marker: str, fail_on: str
) -> list[GateSuite]:
    runs = {
        run.suite_id: run
        for run in session.scalars(
            select(Run).where(Run.suite_id.in_(suite_ids), Run.triggered_by == marker)
        )
    }
    finished = [r.id for r in runs.values() if r.status == "succeeded"]
    counts = check_outcome_counts(session, finished)
    flags = operational_result_flags(session, finished)
    threshold = SEVERITY_RANK[fail_on]
    out: list[GateSuite] = []
    for suite_id in suite_ids:
        run = runs.get(suite_id)
        if run is None:
            out.append(GateSuite(suite_id, None, None, AWAITING_TRIGGER, 0, 0, None, False))
            continue
        total, passed, worst = counts.get(run.id, (0, 0, None))
        has_error = flags.get(run.id, (False, False))[0]
        if run.status in ("queued", "running"):
            state = RUNNING
        elif run.status != "succeeded" or has_error:
            state = ERROR
        elif worst is not None and SEVERITY_RANK[worst] >= threshold:
            state = FAILED
        else:
            state = PASSED
        out.append(GateSuite(suite_id, run.id, run.status, state, total, passed, worst, has_error))
    return out


# Most decisive first: one failing suite fails the gate even while another still runs.
_PRECEDENCE: Final = (FAILED, ERROR, RUNNING, AWAITING_TRIGGER, PASSED)


def _overall(suites: list[GateSuite]) -> str:
    states = {s.state for s in suites}
    return next(state for state in _PRECEDENCE if state in states)
