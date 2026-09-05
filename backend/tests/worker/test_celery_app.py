"""Tests for the Celery app config and request_id propagation signals."""

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
    LLM_INVOKE_TASK_NAME,
    LLM_QUEUE_NAME,
    OTP_SEND_TASK_NAME,
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


def test_llm_invoke_routes_to_its_own_queue() -> None:
    """#1777: llm_invoke shares no queue with run_suite — a backlog of long
    suite runs must not be able to leave an LLM dispatch queued-but-intact
    past the reaper's pending threshold. Guards against a dropped/renamed
    task_routes entry silently reintroducing the shared-queue false-kill
    (#1726 Part A).
    """
    app = create_celery_app()
    assert app.conf.task_routes[LLM_INVOKE_TASK_NAME] == {"queue": LLM_QUEUE_NAME}


def test_send_otp_code_stays_on_the_default_queue() -> None:
    """#1731: a dedicated queue would need the coordinated worker `-Q` change
    (#1777) — routing it by default means an upgrade cannot leave sign-in codes
    queued on a queue no worker consumes. Pinned so a future `task_routes` entry
    is a deliberate decision, not drift.
    """
    import backend.app.worker.tasks  # noqa: F401  — registers the tasks

    app = create_celery_app()
    assert app.conf.task_routes[OTP_SEND_TASK_NAME] == {"queue": LLM_QUEUE_NAME}
    assert OTP_SEND_TASK_NAME in celery_app.celery_app.tasks


def test_worker_concurrency_is_pinned_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """#1790: the prefork default is the HOST's core count, so two identical 1-vCPU deployments
    ran 4 and 2. The pin lives in conf (ships with the image), not in four launch commands.
    """
    from backend.app.core import config

    assert create_celery_app().conf.worker_concurrency == 4
    monkeypatch.setenv("WORKER_CONCURRENCY", "2")
    config.get_settings.cache_clear()
    try:
        assert create_celery_app().conf.worker_concurrency == 2
    finally:
        config.get_settings.cache_clear()


def test_beat_schedule_file_lives_in_the_temp_dir_not_the_cwd() -> None:
    """The runtime image runs as a non-root user with a read-only /workspace (#1408), so beat's
    schedule shelve must never land in the CWD — celery's default.
    """
    import os
    import tempfile

    filename = create_celery_app().conf.beat_schedule_filename
    assert filename == os.path.join(tempfile.gettempdir(), "dataq-celerybeat-schedule")


def test_beat_schedule_registers_poll_and_gap_recovery() -> None:
    """Both orchestration beats are wired with the right tasks + intervals — the
    10-min poll (#171) and the 30-min gap-recovery sweep (B2). Guards against a
    dropped entry silently disabling either schedule.
    """
    schedule = create_celery_app().conf.beat_schedule
    assert schedule["poll-orchestration-runs"]["task"] == "poll_orchestration_runs"
    assert schedule["poll-orchestration-runs"]["schedule"] == 600.0
    assert schedule["recover-orchestration-gaps"]["task"] == "recover_orchestration_gaps"
    assert schedule["recover-orchestration-gaps"]["schedule"] == 1800.0


def test_beat_schedule_registers_stuck_run_reaper() -> None:
    """The stuck-run reaper (#309) — the whole-schedule iterators below only
    inspect entries that EXIST, so a dropped/renamed entry key passes CI
    silently (#1730): this is the exact #405-class compensator built to catch
    a worker dying silently, so its own beat entry disappearing must not.
    """
    schedule = create_celery_app().conf.beat_schedule
    assert schedule["reap-stuck-runs"]["task"] == "reap_stuck_runs"
    assert schedule["reap-stuck-runs"]["schedule"] == 600.0


def test_beat_schedule_registers_llm_invocation_reaper() -> None:
    """The llm_invocations reaper (#1644), same #1730 rationale as its
    stuck-run sibling above — and the two share no other dedicated test.
    """
    schedule = create_celery_app().conf.beat_schedule
    assert schedule["reap-stuck-llm-invocations"]["task"] == "reap_stuck_llm_invocations"
    assert schedule["reap-stuck-llm-invocations"]["schedule"] == 300.0


def test_monitored_queue_names_covers_the_default_and_llm_queues() -> None:
    """#1885: the admin health API's `LLEN` targets — a dropped/renamed entry here
    would silently stop reporting depth for a real queue.
    """
    assert celery_app.MONITORED_QUEUE_NAMES == (celery_app.DEFAULT_QUEUE_NAME, LLM_QUEUE_NAME)


def test_beat_schedule_registers_beat_heartbeat_at_its_named_interval() -> None:
    """#1885 reads `BEAT_HEARTBEAT_INTERVAL_S` back for the admin health API's
    staleness math — pin it against the actual schedule entry.
    """
    schedule = create_celery_app().conf.beat_schedule
    assert schedule["beat-heartbeat"]["task"] == "beat_heartbeat"
    assert schedule["beat-heartbeat"]["schedule"] == celery_app.BEAT_HEARTBEAT_INTERVAL_S


def test_beat_schedule_registers_orphan_asset_sweep() -> None:
    """The orphan-asset sweep (#770) is wired daily, same cadence as the
    sample-failures retention sweep — guards against a dropped entry silently
    disabling the janitor.
    """
    schedule = create_celery_app().conf.beat_schedule
    assert schedule["sweep-orphan-assets"]["task"] == "sweep_orphan_assets"
    assert isinstance(schedule["sweep-orphan-assets"]["schedule"], crontab)


def test_beat_schedule_registers_orphan_secret_sweep() -> None:
    """The orphan-SECRET sweep (#1059), daily like its asset sibling."""
    app = create_celery_app()
    schedule = app.conf.beat_schedule
    assert schedule["sweep-orphan-secrets"]["task"] == "sweep_orphan_secrets"
    assert isinstance(schedule["sweep-orphan-secrets"]["schedule"], crontab)
    import backend.app.worker.tasks  # noqa: F401  — registers the tasks

    assert "sweep_orphan_secrets" in app.tasks


def test_beat_schedule_registers_otp_purge_sweep() -> None:
    """The OTP-code retention sweep (#1136), daily like its sibling janitors."""
    app = create_celery_app()
    schedule = app.conf.beat_schedule
    assert schedule["purge-otp-codes"]["task"] == "purge_otp_codes"
    assert isinstance(schedule["purge-otp-codes"]["schedule"], crontab)
    import backend.app.worker.tasks  # noqa: F401  — registers the tasks

    assert "purge_otp_codes" in app.tasks


# Sub-hourly liveness intervals are fine: a beat restart resetting a countdown of minutes delays a
# tick by minutes.
_INTERVAL_CEILING_S = 1800.0


def test_no_beat_entry_uses_an_interval_a_restart_can_starve() -> None:
    """Every beat entry is either a short liveness interval or a wall-clock crontab."""
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


def test_every_beat_entry_names_a_registered_task() -> None:
    """Every beat entry's task NAME resolves to a registered task — generalized from
    the single #1070 assertion to the whole schedule (review finding on this PR).
    """
    app = create_celery_app()
    import backend.app.worker.tasks  # noqa: F401  — registers the tasks

    for name, entry in app.conf.beat_schedule.items():
        assert entry["task"] in app.tasks, f"{name}: task {entry['task']!r} is not registered"


def test_daily_crontabs_are_staggered_not_a_midnight_herd() -> None:
    """No two wall-clock entries share a fire minute (and none sits at 00:00)."""
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
    """Eager mode: signals fire inside the caller's context."""
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
    """The task's own handlers already ran and classified the failure (#605)."""
    seen = _closed_run_ids(monkeypatch)
    celery_app._fail_run_on_worker_lost(
        task_id="t1",
        exception=ValueError("a normal bug"),
        sender=_Sender("run_suite", args=(str(uuid.uuid4()),)),
    )
    assert seen == []


def test_other_tasks_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only run_suite has a run row to close."""
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
    """Early ack is what stops #755's poison-pill crash loop (25 local restarts)."""
    assert celery_app.celery_app.conf.task_acks_late is False


class TestRedissSafeUrl:
    """#1363 — celery hard-rejects a rediss:// URL without ssl_cert_reqs, so the
    app defaults it rather than crash-looping on any TLS redis (the #1361 AWS
    failure, generalized). Never weakens an explicit choice.
    """

    def test_plain_redis_url_untouched(self) -> None:
        url = "redis://:pw@host:6379/0"
        assert celery_app._rediss_safe_url(url) == url

    def test_rediss_without_param_gains_required(self) -> None:
        assert (
            celery_app._rediss_safe_url("rediss://:pw@host:6379/0")
            == "rediss://:pw@host:6379/0?ssl_cert_reqs=required"
        )

    def test_rediss_with_existing_query_appends_with_ampersand(self) -> None:
        assert (
            celery_app._rediss_safe_url("rediss://:pw@host:6379/0?socket_timeout=5")
            == "rediss://:pw@host:6379/0?socket_timeout=5&ssl_cert_reqs=required"
        )

    def test_explicit_value_is_left_alone_even_cert_none(self) -> None:
        # A deliberate operator choice — even a weaker one — is never rewritten.
        url = "rediss://:pw@host:6379/0?ssl_cert_reqs=CERT_NONE"
        assert celery_app._rediss_safe_url(url) == url

    def test_celery_app_accepts_the_defaulted_url(self) -> None:
        # The end-to-end claim: celery's redis backend parses the defaulted URL
        # (it raises ValueError on the bare one — the #1361 crash shape).
        from celery import Celery

        bare = "rediss://:pw@host:6379/0"
        app = Celery(
            broker=celery_app._rediss_safe_url(bare), backend=celery_app._rediss_safe_url(bare)
        )
        assert app.backend.connparams["ssl_cert_reqs"] is not None
        with pytest.raises(ValueError, match="ssl_cert_reqs"):
            Celery(broker=bare, backend=bare).backend  # noqa: B018
