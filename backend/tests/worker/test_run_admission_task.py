"""The run_suite task's admission behaviour: defer, surface, release (#1998)."""

from __future__ import annotations

import uuid
from typing import Any, cast

import pytest
from celery.exceptions import Retry
from sqlalchemy.orm import Session

from backend.app.db.models import Run
from backend.app.services import run_admission
from backend.app.worker import tasks

MiB = 1024 * 1024


class RunOnlySession:
    """Enough session for `_admit_run`; the run body is stubbed out."""

    def __init__(self, run: Run | None) -> None:
        self.run = run
        self.commits = 0
        self.closed = False

    def get(self, model: type, _pk: Any, with_for_update: bool = False) -> Any:
        return self.run if model is Run else None

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        self.closed = True


def _sess(session: RunOnlySession) -> Session:
    return cast(Session, session)


@pytest.fixture
def stub_run_path(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Silence everything the task does AFTER admission."""
    from backend.app.alerting import dispatch as alert_dispatch
    from backend.app.lineage import dispatch as lineage_dispatch
    from backend.app.services import incident_service

    monkeypatch.setattr(lineage_dispatch, "emit_run_lineage_start", lambda *_a, **_k: None)
    monkeypatch.setattr(lineage_dispatch, "emit_run_lineage_terminal", lambda *_a, **_k: None)
    monkeypatch.setattr(incident_service, "sync_incidents_for_run", lambda *_a, **_k: None)
    monkeypatch.setattr(alert_dispatch, "publish_run_outcome", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks, "_alert_datasource_health_for_run", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks, "_run_suite", lambda *_a, **_k: "succeeded")


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    run: Run | None,
    estimate: run_admission.MemoryEstimate | None,
    decision: run_admission.AdmissionDecision,
) -> tuple[RunOnlySession, list[uuid.UUID]]:
    session = RunOnlySession(run)
    released: list[uuid.UUID] = []
    monkeypatch.setattr(tasks, "get_session", lambda: _sess(session))
    monkeypatch.setattr(run_admission, "estimate_run_memory", lambda *_a, **_k: estimate)
    monkeypatch.setattr(run_admission, "admit", lambda *_a, **_k: decision)
    monkeypatch.setattr(run_admission, "release", lambda rid: released.append(rid))
    return session, released


def _queued_run() -> Run:
    return Run(id=uuid.uuid4(), suite_id=uuid.uuid4(), status="queued")


def test_a_deferred_run_is_requeued_and_says_why_it_is_still_queued(
    monkeypatch: pytest.MonkeyPatch, stub_run_path: None
) -> None:
    """The run must not sit in `running` while it is actually waiting, and `queued` alone
    cannot tell a reader which kind of waiting this is.
    """
    run = _queued_run()
    _install(
        monkeypatch,
        run=run,
        estimate=run_admission.MemoryEstimate(bytes=900 * MiB, basis="flat_file_size"),
        decision=run_admission.AdmissionDecision(defer=True),
    )

    with pytest.raises(Retry):
        tasks.run_suite(str(run.id))

    assert run.status == "queued"
    assert run.queued_reason == run_admission.AWAITING_MEMORY


def test_an_admitted_run_clears_the_waiting_reason_and_executes(
    monkeypatch: pytest.MonkeyPatch, stub_run_path: None
) -> None:
    run = _queued_run()
    run.queued_reason = run_admission.AWAITING_MEMORY
    _install(
        monkeypatch,
        run=run,
        estimate=run_admission.MemoryEstimate(bytes=10, basis="flat_file_size"),
        decision=run_admission.AdmissionDecision(defer=False),
    )

    assert tasks.run_suite(str(run.id)) == "succeeded"
    assert run.queued_reason is None


def test_a_cancelled_run_stops_waiting_instead_of_requeueing_forever(
    monkeypatch: pytest.MonkeyPatch, stub_run_path: None
) -> None:
    run = _queued_run()
    run.status = "cancelled"
    run.queued_reason = run_admission.AWAITING_MEMORY
    _install(
        monkeypatch,
        run=run,
        estimate=run_admission.MemoryEstimate(bytes=900 * MiB, basis="flat_file_size"),
        decision=run_admission.AdmissionDecision(defer=True),
    )

    assert tasks.run_suite(str(run.id)) == "cancelled"
    assert run.queued_reason is None


def test_the_reservation_is_released_even_when_the_run_crashes(
    monkeypatch: pytest.MonkeyPatch, stub_run_path: None
) -> None:
    run = _queued_run()
    _, released = _install(
        monkeypatch,
        run=run,
        estimate=run_admission.MemoryEstimate(bytes=10, basis="flat_file_size"),
        decision=run_admission.AdmissionDecision(defer=False),
    )

    def _boom(*_a: object, **_k: object) -> str:
        raise RuntimeError("worker fell over mid-run")

    monkeypatch.setattr(tasks, "_run_suite", _boom)

    with pytest.raises(RuntimeError, match="fell over"):
        tasks.run_suite(str(run.id))

    assert released == [run.id]


def test_a_carried_estimate_is_not_re_probed_on_a_retry(
    monkeypatch: pytest.MonkeyPatch, stub_run_path: None
) -> None:
    """Re-probing the store on every 15-second retry would pay a metadata call per wait."""
    run = _queued_run()
    probes: list[int] = []
    session = RunOnlySession(run)
    monkeypatch.setattr(tasks, "get_session", lambda: _sess(session))
    monkeypatch.setattr(run_admission, "estimate_run_memory", lambda *_a, **_k: probes.append(1))
    seen: list[run_admission.MemoryEstimate | None] = []

    def _admit(_s: Any, *, run: Run, estimate: Any, waited_out: bool) -> Any:
        seen.append(estimate)
        return run_admission.AdmissionDecision(defer=False)

    monkeypatch.setattr(run_admission, "admit", _admit)
    monkeypatch.setattr(run_admission, "release", lambda _rid: None)

    tasks.run_suite(
        str(run.id),
        admission={"bytes": 700 * MiB, "basis": "flat_file_size", "exclusive": False},
        admission_deadline=1.0,
    )

    assert probes == []
    assert seen == [
        run_admission.MemoryEstimate(bytes=700 * MiB, basis="flat_file_size", exclusive=False)
    ]
