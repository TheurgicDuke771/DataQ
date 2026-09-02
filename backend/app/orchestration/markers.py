"""The `runs.triggered_by` ↔ `pipeline_runs` correlation, in ONE place (#1728).

`orchestration_service._trigger_suites` stamps a triggered run with
``f"{provider}:{pipeline_or_dag_id}:{provider_run_id}"``. Neither id is colon-free
(an Airflow ``run_id`` carries timestamps, dbt's job_name is free-form webhook
input), so the marker cannot be split back apart — and two distinct pipeline runs
can reconstruct to the identical string when a colon lands on the other side of
the boundary (``"nightly:etl"``+``"run-1"`` vs ``"nightly"``+``"etl:run-1"``).
Every reader therefore reconstructs-and-compares against the stored columns and
FAILS CLOSED when more than one row matches (#1714), instead of attributing a DQ
run to whichever row came back first.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.db.models import PipelineRun, Run

log = get_logger(__name__)


def pipeline_run_marker(pipeline_run: PipelineRun) -> str:
    return (
        f"{pipeline_run.provider}:{pipeline_run.pipeline_or_dag_id}:{pipeline_run.provider_run_id}"
    )


def _reconstructed_marker() -> ColumnElement[str]:
    return func.concat(
        PipelineRun.provider, ":", PipelineRun.pipeline_or_dag_id, ":", PipelineRun.provider_run_id
    )


def pipeline_runs_for_marker(session: Session, marker: str) -> list[PipelineRun]:
    """Every stored pipeline run that reconstructs to ``marker`` — the caller decides
    what more than one means (nothing, for every reader today).
    """
    provider, sep, _rest = marker.partition(":")
    if not sep:
        return []
    return list(
        session.scalars(
            select(PipelineRun).where(
                PipelineRun.provider == provider, _reconstructed_marker() == marker
            )
        )
    )


def unambiguous_pipeline_run(session: Session, marker: str) -> PipelineRun | None:
    candidates = pipeline_runs_for_marker(session, marker)
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        log.warning("pipeline_run_marker_ambiguous", marker=marker, candidate_count=len(candidates))
    return None


def ambiguous_markers(session: Session, markers: Iterable[str]) -> set[str]:
    """The subset of ``markers`` more than one stored pipeline run reconstructs to.

    The concat expression has no index (#1814); the provider pre-filter narrows the
    scan to the providers actually on the page.
    """
    wanted = set(markers)
    if not wanted:
        return set()
    providers = {m.partition(":")[0] for m in wanted}
    reconstructed = _reconstructed_marker()
    rows = session.execute(
        select(reconstructed, func.count())
        .where(PipelineRun.provider.in_(providers), reconstructed.in_(wanted))
        .group_by(reconstructed)
        .having(func.count() > 1)
    ).all()
    return {marker for marker, _count in rows}


@dataclass
class TriggeredRuns:
    #: Per pipeline run, the DQ runs it triggered, newest first — ``[]`` when its
    #: marker is ambiguous, so a collided row never claims another row's run.
    by_pipeline_run: dict[uuid.UUID, list[uuid.UUID]] = field(default_factory=dict)
    ambiguous_markers: set[str] = field(default_factory=set)

    def is_ambiguous(self, pipeline_run: PipelineRun) -> bool:
        return pipeline_run_marker(pipeline_run) in self.ambiguous_markers


def triggered_runs(session: Session, pipeline_runs: Sequence[PipelineRun]) -> TriggeredRuns:
    by_marker: dict[str, list[uuid.UUID]] = {pipeline_run_marker(p): [] for p in pipeline_runs}
    if not by_marker:
        return TriggeredRuns()
    ambiguous = ambiguous_markers(session, by_marker)
    for run in session.scalars(
        select(Run)
        .where(Run.triggered_by.in_(set(by_marker) - ambiguous))
        .order_by(Run.created_at.desc())
    ):
        assert run.triggered_by is not None
        by_marker[run.triggered_by].append(run.id)
    if ambiguous:
        log.warning("pipeline_run_marker_ambiguous", markers=sorted(ambiguous))
    return TriggeredRuns(
        by_pipeline_run={p.id: by_marker[pipeline_run_marker(p)] for p in pipeline_runs},
        ambiguous_markers=ambiguous,
    )
