"""Integration test for request_id correlation FastAPI -> Celery -> log (#75).

Eager mode can't verify the mechanism (the task runs in the caller's context, so
the request_id would appear even with no propagation). Instead this drives the
real signal handlers across a *context boundary*: inject on the caller side,
reset the ContextVar to None (simulating a fresh worker process after the broker
hop), restore from the message header, and assert a log emitted during execution
carries the request_id. Resetting to None is what proves propagation happens via
the header, not via a same-context leak.

The one link not exercised here is Celery serialising the custom header over a
real broker onto task.request — documented protocol-v2 behaviour, confirmed by
the compose worker.
"""

import json
from types import SimpleNamespace
from typing import Any, cast

from backend.app.core.logging import (
    configure_logging,
    get_logger,
    request_id_var,
)
from backend.app.worker.celery_app import (
    REQUEST_ID_HEADER,
    _clear_request_id,
    _inject_request_id,
    _restore_request_id,
)


def _find_event(captured_out: str, event: str) -> dict[str, Any] | None:
    for line in captured_out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("event") == event:
            return cast(dict[str, Any], record)
    return None


def test_request_id_flows_from_publish_to_worker_log(capsys: Any) -> None:
    configure_logging()
    log = get_logger("test.worker")

    # 1) Caller (request) context: an active request_id is stamped onto headers.
    request_id_var.set("rid-INTEGRATION")
    headers: dict[str, str] = {}
    _inject_request_id(headers=headers)
    assert headers[REQUEST_ID_HEADER] == "rid-INTEGRATION"

    # 2) Broker hop -> fresh worker context (no leaked rid); the header rides on
    #    task.request, as Celery exposes custom headers under protocol v2.
    request_id_var.set(None)
    task = SimpleNamespace(
        request=SimpleNamespace(**{REQUEST_ID_HEADER: headers[REQUEST_ID_HEADER]})
    )

    # 3) task_prerun restores it; a log emitted "during execution" must carry it.
    _restore_request_id(task=task)
    log.info("run_started")
    _clear_request_id(task=task)

    # 4) Context is clean again after the task.
    assert request_id_var.get() is None

    record = _find_event(capsys.readouterr().out, "run_started")
    assert record is not None, "run_started log line not found in output"
    assert record["request_id"] == "rid-INTEGRATION"
