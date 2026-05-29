"""Tests for the Celery app config and request_id propagation signals.

No broker is needed: the signal receivers are called directly with the same
arguments Celery would pass, and the `request_id_var` ContextVar is inspected
to assert propagation. The eager-mode case guards the bug fixed pre-merge —
a blanket clear in task_postrun would drop the caller's request_id.
"""

from collections.abc import Iterator
from types import SimpleNamespace

import pytest

from backend.app.core.logging import request_id_var
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
