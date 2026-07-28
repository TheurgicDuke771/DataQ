"""Tests for the Celery app config and request_id propagation signals.

No broker is needed: the signal receivers are called directly with the same
arguments Celery would pass, and the `request_id_var` ContextVar is inspected
to assert propagation. The eager-mode case guards the bug fixed pre-merge —
a blanket clear in task_postrun would drop the caller's request_id.
"""

import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from billiard.exceptions import WorkerLostError
from celery.schedules import crontab

from backend.app.core.logging import request_id_var
from backend.app.worker import celery_app
from backend.app.worker.celery_app import (
    REQUEST_ID_HEADER,
    _clear_request_id,
    _inject_request_id,
    _restore_request_id,
    create_celery_app,
)


@pytest.fixture(autouse=True)
def _clean_request_id() -> Iterator[None]:
    """Reset the ContextVar around each test so state never leaks between them."""
    request_id_var.set(None)
    yield
    request_id_var.set(None)


def _fake_task(request_id: str | None = None) -> SimpleNamespace:
    """A stand-in for a Celery task: only `.request` and its attrs are used."""
    request = SimpleNamespace()
    if request_id is not None:
        setattr(request, REQUEST_ID_HEADER, request_id)
    return SimpleNamespace(request=request)


# ───────────────────────── app config ──────────────────────────


def test_create_celery_app_uses_redis_url_and_json() -> None:
    app = create_celery_app()
    assert app.main == "dataq"
    assert app.conf.broker_url.startswith("redis://")
    assert app.conf.result_backend.startswith("redis://")
    assert app.conf.task_serializer == "json"
    assert app.conf.accept_content == ["json"]
    # task_track_started lets the run read-back distinguish queued from running.
    assert app.conf.task_track_started is True


def test_beat_schedule_registers_poll_and_gap_recovery() -> None:
    """Both orchestration beats are wired with the right tasks + intervals — the
    10-min poll (#171) and the 30-min gap-recovery sweep (B2). Guards against a
    dropped entry silently disabling either schedule."""
    schedule = create_celery_app().conf.beat_schedule
    assert schedule["poll-orchestration-runs"]["task"] == "poll_orchestration_runs"
    assert schedule["poll-orchestration-runs"]["schedule"] == 600.0
    assert schedule["recover-orchestration-gaps"]["task"] == "recover_orchestration_gaps"
    assert schedule["recover-orchestration-gaps"]["schedule"] == 1800.0


def test_beat_schedule_registers_orphan_asset_sweep() -> None:
    """The orphan-asset sweep (#770) is wired daily, same cadence as the
    sample-failures retention sweep — guards against a dropped entry silently
    disabling the janitor."""
    schedule = create_celery_app().conf.beat_schedule
    assert schedule["sweep-orphan-assets"]["task"] == "sweep_orphan_assets"
    assert isinstance(schedule["sweep-orphan-assets"]["schedule"], crontab)


def test_beat_schedule_registers_orphan_secret_sweep() -> None:
    """The orphan-SECRET sweep (#1059), daily like its asset sibling.

    Also asserts the task NAME resolves to a registered task: a beat entry naming a
    task that does not exist fails silently at runtime — beat logs and moves on —
    which is the #405/#904 shape where periodic work quietly stopped while
    everything reported healthy.
    """
    app = create_celery_app()
    schedule = app.conf.beat_schedule
    assert schedule["sweep-orphan-secrets"]["task"] == "sweep_orphan_secrets"
    assert isinstance(schedule["sweep-orphan-secrets"]["schedule"], crontab)
    import backend.app.worker.tasks  # noqa: F401  — registers the tasks

    assert "sweep_orphan_secrets" in app.tasks


# Sub-hourly liveness intervals are fine: a beat restart resetting a countdown of
# minutes delays a tick by minutes. Anything slower would cross the restart cadence
# of the embedded-beat deployment and belongs on a wall clock instead.
_INTERVAL_CEILING_S = 1800.0


def test_no_beat_entry_uses_an_interval_a_restart_can_starve() -> None:
    """Every beat entry is either a short liveness interval or a wall-clock crontab.

    The #1091 class: beat runs embedded in the worker (`worker -B`) with no
    persisted state, so an INTERVAL schedule restarts its countdown on every
    worker restart — and prod restarts more often than daily (revision rolls,
    scaling, the #904 watchdog). A 24h interval therefore never fired: warehouse
    lineage sat 10 days stale while beat's every-minute tasks ran fine, and the
    only daily task that executed at all was the one #1024 kicks on beat start.

    Guarding the CLASS, not the six names: any future beat entry added with a
    slow interval re-introduces the bug, so the ceiling applies to every entry.
    A `crontab` fires at a wall-clock moment and is indifferent to restarts.
    """
    schedule = create_celery_app().conf.beat_schedule
    assert schedule, "beat schedule unexpectedly empty"
    for name, entry in schedule.items():
        sched = entry["schedule"]
        if isinstance(sched, crontab):
            continue
        assert isinstance(sched, float), f"{name}: unexpected schedule type {type(sched)!r}"
        assert sched <= _INTERVAL_CEILING_S, (
            f"{name}: {sched}s interval would reset on every worker restart and can "
            f"starve forever under embedded beat (#1091) — use crontab() instead"
        )


def test_daily_crontabs_are_staggered_not_a_midnight_herd() -> None:
    """No two wall-clock entries share a fire minute (and none sits at 00:00).

    The stagger is deliberate — six tasks include warehouse queries and vault
    sweeps, and firing them as one batch turns a cheap daily cadence into a
    thundering herd on the worker, the DB and the warehouses.
    """
    schedule = create_celery_app().conf.beat_schedule
    moments = []
    for entry in schedule.values():
        sched = entry["schedule"]
        if isinstance(sched, crontab):
            moment = (frozenset(sched.hour), frozenset(sched.minute))
            assert moment != (frozenset({0}), frozenset({0}))
            moments.append(moment)
    assert moments, "expected at least one crontab entry"
    assert len(moments) == len(set(moments)), f"duplicate crontab fire times: {moments}"


# ───────────────────────── inject (publisher side) ─────────────────


def test_inject_stamps_request_id_onto_headers_when_set() -> None:
    request_id_var.set("req-123")
    headers: dict[str, str] = {}
    _inject_request_id(headers=headers)
    assert headers[REQUEST_ID_HEADER] == "req-123"


def test_inject_is_noop_when_request_id_unset() -> None:
    headers: dict[str, str] = {}
    _inject_request_id(headers=headers)
    assert headers == {}


def test_inject_is_noop_when_headers_none() -> None:
    request_id_var.set("req-123")
    # Must not raise when Celery passes headers=None.
    _inject_request_id(headers=None)


# ───────────────────────── restore / clear (worker side) ───────────


def test_restore_then_clear_in_worker_context() -> None:
    """Worker process starts uncorrelated: prerun sets, postrun clears to None."""
    task = _fake_task("req-abc")
    _restore_request_id(task=task)
    assert request_id_var.get() == "req-abc"
    _clear_request_id(task=task)
    assert request_id_var.get() is None


def test_clear_restores_prior_value_under_eager_mode() -> None:
    """Eager mode: signals fire inside the caller's context.

    postrun must restore the request's own request_id, not blow it away — the
    regression this guards.
    """
    request_id_var.set("req-CALLER")
    task = _fake_task("req-CALLER")
    _restore_request_id(task=task)
    assert request_id_var.get() == "req-CALLER"
    _clear_request_id(task=task)
    assert request_id_var.get() == "req-CALLER"


def test_restore_is_noop_when_task_has_no_request_id() -> None:
    """A task dispatched without a request_id leaves the caller context intact."""
    request_id_var.set("req-CALLER")
    task = _fake_task(None)
    _restore_request_id(task=task)
    assert request_id_var.get() == "req-CALLER"
    # postrun finds no stashed token, so it must not touch the var.
    _clear_request_id(task=task)
    assert request_id_var.get() == "req-CALLER"


def test_restore_handles_missing_task() -> None:
    # Defensive: Celery always passes task, but the guard must hold regardless.
    _restore_request_id(task=None)
    assert request_id_var.get() is None


# ───────────────── worker-loss run closure (#755) ─────────────────
#
# When the OOM killer SIGKILLs a prefork child mid-`run_suite`, nothing inside the
# task runs — so the run row can only be closed from the PARENT, via task_failure.
# These pin the handler's scoping, because the failure mode of getting it wrong is
# silent: too broad and it overwrites well-classified failures with generic memory
# text; too narrow and the run sits `running` for an hour.


class _Sender:
    def __init__(self, name: str, args: tuple[Any, ...] = (), kwargs: dict[str, Any] | None = None):
        self.name = name
        self.request = SimpleNamespace(args=args, kwargs=kwargs or {})


def _closed_run_ids(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """Capture what the handler asks run_service to close, without a DB."""
    seen: list[uuid.UUID] = []

    class _FakeSession:
        def close(self) -> None: ...

    monkeypatch.setattr("backend.app.db.session.get_session", lambda: _FakeSession())

    def _capture(_session: Any, *, run_id: uuid.UUID, **_k: Any) -> bool:
        seen.append(run_id)
        return True

    monkeypatch.setattr("backend.app.services.run_service.fail_run_worker_lost", _capture)
    return seen


def test_worker_lost_closes_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _closed_run_ids(monkeypatch)
    rid = uuid.uuid4()
    celery_app._fail_run_on_worker_lost(
        task_id="t1",
        exception=WorkerLostError("signal 9 (SIGKILL)"),
        sender=_Sender("run_suite", args=(str(rid),)),
    )
    assert seen == [rid]


def test_worker_lost_accepts_the_keyword_call_form(monkeypatch: pytest.MonkeyPatch) -> None:
    # run_suite is dispatched positionally today; reading only args[0] would break
    # silently the day someone calls it by keyword.
    seen = _closed_run_ids(monkeypatch)
    rid = uuid.uuid4()
    celery_app._fail_run_on_worker_lost(
        task_id="t1",
        exception=WorkerLostError("boom"),
        sender=_Sender("run_suite", kwargs={"run_id": str(rid)}),
    )
    assert seen == [rid]


def test_ordinary_exceptions_are_left_to_the_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """The task's own handlers already ran and classified the failure (#605).

    Overwriting that with the generic memory reason would make real failures LESS
    diagnosable — the opposite of what #755 is for.
    """
    seen = _closed_run_ids(monkeypatch)
    celery_app._fail_run_on_worker_lost(
        task_id="t1",
        exception=ValueError("a normal bug"),
        sender=_Sender("run_suite", args=(str(uuid.uuid4()),)),
    )
    assert seen == []


def test_other_tasks_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only run_suite has a run row to close.

    The arg is a VALID uuid on purpose: an unparseable one would make this pass via
    the run-id guard instead of the task-name guard, so deleting the name check
    would not fail anything. (It did exactly that until a mutation check caught it.)
    """
    seen = _closed_run_ids(monkeypatch)
    celery_app._fail_run_on_worker_lost(
        task_id="t1",
        exception=WorkerLostError("boom"),
        sender=_Sender("poll_orchestration_runs", args=(str(uuid.uuid4()),)),
    )
    assert seen == []


def test_unparseable_or_missing_run_id_is_survivable(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _closed_run_ids(monkeypatch)
    for sender in (_Sender("run_suite", args=("not-a-uuid",)), _Sender("run_suite")):
        celery_app._fail_run_on_worker_lost(
            task_id="t1", exception=WorkerLostError("boom"), sender=sender
        )
    assert seen == []


def test_handler_never_raises_out_of_the_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    """It runs on an already-failing path; raising here would mask the real error."""

    def _explode() -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr("backend.app.db.session.get_session", _explode)
    celery_app._fail_run_on_worker_lost(
        task_id="t1",
        exception=WorkerLostError("boom"),
        sender=_Sender("run_suite", args=(str(uuid.uuid4()),)),
    )  # must not raise


def test_acks_late_stays_off_so_an_oom_run_is_not_redelivered() -> None:
    """Early ack is what stops #755's poison-pill crash loop (25 local restarts).

    Pinned as a test because it is a *default* we are relying on: flipping it to
    late-ack would silently hand an OOM-killing run straight back to a fresh child.
    """
    assert celery_app.celery_app.conf.task_acks_late is False
