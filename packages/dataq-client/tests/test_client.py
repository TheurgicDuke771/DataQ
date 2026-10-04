"""The dataq-client convenience layer and CLI, against a mock transport."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from dataq_client import (
    AuthError,
    DataQClient,
    DataQError,
    GateTimeoutError,
    RateLimitedError,
    RunOutcome,
    RunTimeoutError,
    cli,
)

SUITE = uuid.UUID("11111111-1111-1111-1111-111111111111")
RUN = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _run(status: str, **fields: Any) -> dict[str, Any]:
    return {
        "id": str(RUN),
        "suite_id": str(SUITE),
        "status": status,
        "triggered_by": "api",
        "started_at": None,
        "finished_at": None,
        "created_at": "2026-09-28T00:00:00Z",
        "checks_total": fields.pop("checks_total", 3),
        "checks_passed": fields.pop("checks_passed", 3),
        "worst_severity": fields.pop("worst_severity", None),
        "failure_reason": fields.pop("failure_reason", None),
        "results": fields.pop("results", []),
        **fields,
    }


def _progress(status: str, completed: int = 3) -> dict[str, Any]:
    return {
        "run_id": str(RUN),
        "suite_id": str(SUITE),
        "status": status,
        "total_checks": 3,
        "completed_checks": completed,
        "counts": {"pass": completed},
        "checks": [],
        "started_at": None,
        "finished_at": None,
    }


def _result(status: str) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "check_id": str(uuid.uuid4()),
        "status": status,
        "metric_value": None,
        "duration_ms": None,
        "observed_value": None,
        "expected_value": None,
        "sample_failures": None,
    }


class _Server:
    """Answers each request with the next queued response and records what was asked."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)


def _client(server: Callable[[httpx.Request], httpx.Response]) -> DataQClient:
    return DataQClient(
        "https://dq.example.com", "dq_live_x", httpx_args={"transport": httpx.MockTransport(server)}
    )


def _json(status: int, body: Any) -> httpx.Response:
    return httpx.Response(status, json=body)


def test_the_token_travels_as_a_bearer_and_the_path_is_the_api_path() -> None:
    server = _Server(_json(202, _run("queued")))
    outcome = _client(server).trigger_run(SUITE)
    request = server.requests[0]
    assert request.headers["authorization"] == "Bearer dq_live_x"
    assert (request.method, request.url.path) == ("POST", f"/api/v1/suites/{SUITE}/run")
    assert (outcome.status, outcome.finished) == ("queued", False)


def test_url_and_token_fall_back_to_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATAQ_URL", raising=False)
    monkeypatch.delenv("DATAQ_PAT", raising=False)
    with pytest.raises(ValueError, match="DATAQ_URL"):
        DataQClient()
    monkeypatch.setenv("DATAQ_URL", "https://dq.example.com")
    with pytest.raises(ValueError, match="DATAQ_PAT"):
        DataQClient()


def test_wait_reads_the_lifecycle_never_the_counts() -> None:
    """A running run already shows 3/3 passed and no severity — the "nothing failed" shape. It
    is not finished until its status says so."""
    server = _Server(
        _json(200, _progress("running", completed=3)),
        _json(200, _progress("running", completed=3)),
        _json(200, _progress("succeeded")),
        _json(200, _run("succeeded", worst_severity="critical", checks_passed=2)),
    )
    sleeps: list[float] = []
    outcome = _client(server).wait_for_run(RUN, sleep=sleeps.append, clock=lambda: 0.0)
    assert (outcome.status, outcome.worst_severity) == ("succeeded", "critical")
    # Polls hit the progress endpoint; the full run (sample rows, an audit event) is read once.
    paths = [request.url.path for request in server.requests]
    assert paths == [f"/api/v1/runs/{RUN}/progress"] * 3 + [f"/api/v1/runs/{RUN}"]


def test_polling_never_goes_below_five_seconds_and_backs_off() -> None:
    server = _Server(
        *[_json(200, _progress("running"))] * 6,
        _json(200, _progress("succeeded")),
        _json(200, _run("succeeded")),
    )
    sleeps: list[float] = []
    _client(server).wait_for_run(RUN, interval=0.1, sleep=sleeps.append, clock=lambda: 0.0)
    assert sleeps[0] == 5.0
    assert sleeps == sorted(sleeps) and max(sleeps) <= 30.0 and sleeps[-1] > 5.0


def test_a_run_still_going_at_the_deadline_times_out() -> None:
    server = _Server(*[_json(200, _progress("running"))] * 3)
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    with pytest.raises(RunTimeoutError, match="still running"):
        _client(server).wait_for_run(RUN, timeout=8, sleep=sleep, clock=lambda: now[0])


def test_a_rate_limit_is_raised_not_retried() -> None:
    server = _Server(_json(429, {"error": {"code": "rate_limited", "message": "slow down"}}))
    with pytest.raises(RateLimitedError) as exc:
        _client(server).wait_for_run(RUN, sleep=lambda _s: None)
    assert exc.value.code == "rate_limited"
    assert len(server.requests) == 1


def test_an_auth_refusal_carries_the_servers_code() -> None:
    server = _Server(_json(403, {"error": {"code": "forbidden", "message": "needs edit"}}))
    with pytest.raises(AuthError) as exc:
        _client(server).trigger_run(SUITE)
    assert (exc.value.status_code, exc.value.code, exc.value.message) == (
        403,
        "forbidden",
        "needs edit",
    )


def test_an_unparseable_success_is_an_error_not_a_crash() -> None:
    from dataq_client import DataQError

    server = _Server(_json(200, _run("queued")))  # the trigger answers 202, not 200
    with pytest.raises(DataQError, match="unexpected_response"):
        _client(server).trigger_run(SUITE)


def test_errored_checks_are_counted() -> None:
    server = _Server(
        _json(200, _run("succeeded", results=[_result("pass"), _result("error"), _result("skip")]))
    )
    assert _client(server).get_run(RUN).checks_errored == 1


def test_export_then_import_round_trips_the_document() -> None:
    document = {
        "version": 1,
        "name": "orders",
        "description": None,
        "checks": [
            {
                "name": "amount_range",
                "kind": "expectation",
                "expectation_type": "expect_column_values_to_be_between",
                "config": {"column": "amount", "min_value": 0.01, "max_value": 99999.99},
                "severity_warn_threshold": 0.05,
            }
        ],
    }
    imported = {
        "id": str(uuid.uuid4()),
        "name": "orders",
        "description": None,
        "connection_id": str(uuid.uuid4()),
        "target": None,
        "created_by": str(uuid.uuid4()),
        "created_at": "2026-09-28T00:00:00Z",
        "updated_at": "2026-09-28T00:00:00Z",
    }
    server = _Server(_json(200, document), _json(201, imported))
    client = _client(server)
    exported = client.export_suite(SUITE)
    client.import_suite(exported, imported["connection_id"])
    sent = json.loads(server.requests[1].content)
    assert sent["connection_id"] == imported["connection_id"]
    assert sent["document"]["checks"][0]["config"] == document["checks"][0]["config"]


# ───────────────────────────── CLI ─────────────────────────────


def _outcome(**fields: Any) -> RunOutcome:
    base: dict[str, Any] = {
        "run_id": RUN,
        "suite_id": SUITE,
        "status": "succeeded",
        "worst_severity": None,
        "checks_total": 3,
        "checks_passed": 3,
        "checks_errored": 0,
        "failure_reason": None,
    }
    return RunOutcome(**{**base, **fields})


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ({}, 0),
        ({"worst_severity": "warn"}, 1),
        ({"worst_severity": "fail"}, 2),
        ({"worst_severity": "critical"}, 2),
        ({"status": "failed"}, 3),
        ({"status": "cancelled", "worst_severity": "warn"}, 3),
        ({"checks_errored": 1, "worst_severity": "critical"}, 3),
    ],
)
def test_the_exit_code_ladder(fields: dict[str, Any], code: int) -> None:
    assert cli.exit_code(_outcome(**fields)) == code


def test_cli_run_wait_exits_by_the_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    server = _Server(
        _json(202, _run("queued")),
        _json(200, _progress("succeeded")),
        _json(200, _run("succeeded", worst_severity="fail")),
    )
    monkeypatch.setattr(cli, "DataQClient", lambda url: _client(server))
    assert cli.main(["run", str(SUITE), "--wait"]) == 2
    assert "worst severity fail" in capsys.readouterr().out


def test_cli_client_problems_exit_four(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _Server(_json(401, {"error": {"code": "unauthenticated", "message": "no"}}))
    monkeypatch.setattr(cli, "DataQClient", lambda url: _client(server))
    assert cli.main(["run", str(SUITE), "--wait"]) == 4
    monkeypatch.delenv("DATAQ_PAT", raising=False)
    monkeypatch.setattr(cli, "DataQClient", DataQClient)
    assert cli.main(["--url", "https://dq.example.com", "run", str(SUITE)]) == 4


# ── pipeline gate (ADR 0046) ─────────────────────────────────────────────────────────────


def _gate(state: str, *, retry: int | None = 15, worst: str | None = None) -> dict[str, Any]:
    return {
        "state": state,
        "fail_on": "fail",
        "triggered_by": "airflow:nightly:r1",
        "created_runs": 0,
        "retry_after_seconds": retry,
        "suites": [
            {
                "suite_id": str(SUITE),
                "run_id": str(RUN),
                "run_status": "running" if state == "running" else "succeeded",
                "state": state,
                "checks_total": 3,
                "checks_passed": 2,
                "worst_severity": worst,
                "has_error": state == "error",
            }
        ],
    }


_GATE_ARGS: dict[str, Any] = {
    "provider": "airflow",
    "pipeline_or_dag_id": "nightly",
    "env": "dev",
    "provider_run_id": "r1",
}


def test_the_gate_repeats_the_same_request_until_a_final_state() -> None:
    server = _Server(
        _json(200, _gate("running")),
        _json(200, _gate("running")),
        _json(200, _gate("failed", retry=None, worst="fail")),
    )
    sleeps: list[float] = []
    outcome = _client(server).gate(**_GATE_ARGS, sleep=sleeps.append, clock=lambda: 0.0)
    assert (outcome.state, outcome.finished) == ("failed", True)
    assert outcome.suites[0]["worst_severity"] == "fail"
    assert sleeps == [15.0, 15.0]
    bodies = [json.loads(request.content) for request in server.requests]
    assert all(body == bodies[0] for body in bodies)
    assert bodies[0]["provider_run_id"] == "r1" and bodies[0]["trigger"] is True
    assert server.requests[0].url.path == "/api/v1/orchestration/gate"


def test_the_gate_never_polls_faster_than_five_seconds() -> None:
    server = _Server(_json(200, _gate("running", retry=1)), _json(200, _gate("passed", retry=None)))
    sleeps: list[float] = []
    _client(server).gate(**_GATE_ARGS, sleep=sleeps.append, clock=lambda: 0.0)
    assert sleeps == [5.0]


def test_a_gate_still_running_at_the_deadline_times_out() -> None:
    server = _Server(*[_json(200, _gate("awaiting_trigger"))] * 3)
    now = [0.0]

    def _sleep(seconds: float) -> None:
        now[0] += seconds

    with pytest.raises(GateTimeoutError, match="awaiting_trigger"):
        _client(server).gate(
            **_GATE_ARGS, trigger=False, timeout=20.0, sleep=_sleep, clock=lambda: now[0]
        )


@pytest.mark.parametrize(("state", "code"), [("passed", 0), ("failed", 2), ("error", 3)])
def test_cli_gate_exits_by_the_verdict(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], state: str, code: int
) -> None:
    server = _Server(_json(200, _gate(state, retry=None)))
    monkeypatch.setattr(cli, "DataQClient", lambda url: _client(server))
    argv = ["gate", "--provider", "airflow", "--pipeline", "nightly", "--env", "dev"]
    assert cli.main([*argv, "--run-id", "r1"]) == code
    assert f"gate airflow:nightly:r1: {state}" in capsys.readouterr().out


def test_cli_gate_timeout_exits_four(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _Server(*[_json(200, _gate("running", retry=5))] * 5)
    monkeypatch.setattr(cli, "DataQClient", lambda url: _client(server))
    monkeypatch.setattr("dataq_client.client.time.sleep", lambda _s: None)
    argv = ["gate", "--provider", "airflow", "--pipeline", "nightly", "--env", "dev"]
    assert cli.main([*argv, "--run-id", "r1", "--timeout", "0"]) == 4


# ── suite documents: validate / apply / drift (checks-as-code) ──


_PLAN = {
    "dry_run": True,
    "changed": True,
    "suite_fields": [],
    "checks": [
        {"name": "amount_range", "action": "update", "fields": ["config"]},
        {"name": "id_not_null", "action": "unchanged", "fields": []},
    ],
    "unmanaged": ["legacy"],
}


def test_a_yaml_document_is_sent_as_text_and_a_json_one_as_an_object() -> None:
    server = _Server(
        _json(200, {"valid": True, "check_count": 0, "problems": []}),
        _json(200, {"valid": True, "check_count": 0, "problems": []}),
    )
    client = _client(server)
    connection = str(uuid.uuid4())

    client.validate_suite("name: orders\nchecks: []\n", connection)
    client.validate_suite({"name": "orders", "checks": []}, connection)

    as_yaml, as_json = (json.loads(r.content) for r in server.requests)
    assert server.requests[0].url.path == "/api/v1/suites/validate"
    # The server parses the YAML, so client and server cannot disagree about a value.
    assert as_yaml == {"connection_id": connection, "document_yaml": "name: orders\nchecks: []\n"}
    assert as_json == {"connection_id": connection, "document": {"name": "orders", "checks": []}}


def test_apply_sends_prune_and_dry_run_and_returns_the_plan() -> None:
    server = _Server(_json(200, _PLAN))
    plan = _client(server).apply_suite(SUITE, {"name": "o", "checks": []}, prune=True, dry_run=True)

    request = server.requests[0]
    assert request.url.path == f"/api/v1/suites/{SUITE}/apply"
    sent = json.loads(request.content)
    assert (sent["prune"], sent["dry_run"]) == (True, True)
    assert plan == _PLAN


def test_a_refused_apply_raises_with_the_servers_code() -> None:
    server = _Server(_json(422, {"error": {"code": "suite_import_invalid", "message": "bad"}}))
    with pytest.raises(DataQError) as excinfo:
        _client(server).apply_suite(SUITE, {"name": "o", "checks": []})
    assert excinfo.value.code == "suite_import_invalid"


def _cli_with(monkeypatch: pytest.MonkeyPatch, *responses: httpx.Response) -> _Server:
    server = _Server(*responses)
    monkeypatch.setenv("DATAQ_URL", "https://dq.example.com")
    monkeypatch.setenv("DATAQ_PAT", "dq_live_x")
    monkeypatch.setattr(cli, "DataQClient", lambda _url=None: _client(server))
    return server


@pytest.mark.parametrize(("changed", "code"), [(True, 2), (False, 0)])
def test_cli_drift_exits_two_only_when_the_suite_differs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
    changed: bool,
    code: int,
) -> None:
    plan = {**_PLAN, "changed": changed}
    if not changed:
        plan["checks"] = [{"name": "id_not_null", "action": "unchanged", "fields": []}]
    server = _cli_with(monkeypatch, _json(200, plan))
    file = tmp_path / "orders.yaml"
    file.write_text("name: orders\nchecks: []\n")

    exit_code_ = cli.main(["drift", str(file), "--suite", str(SUITE)])

    assert exit_code_ == code
    # drift never writes: it is the dry run.
    assert json.loads(server.requests[0].content)["dry_run"] is True
    out = capsys.readouterr().out
    assert ("update  amount_range (config)" in out) is changed
    assert ("no drift" in out) is not changed


def test_cli_apply_writes_and_exits_zero_even_when_it_changed_things(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    server = _cli_with(monkeypatch, _json(200, {**_PLAN, "dry_run": False}))
    file = tmp_path / "orders.json"
    file.write_text(json.dumps({"name": "orders", "checks": []}))

    exit_code_ = cli.main(["apply", str(file), "--suite", str(SUITE), "--prune"])

    assert exit_code_ == 0
    sent = json.loads(server.requests[0].content)
    assert (sent["dry_run"], sent["prune"]) == (False, True)
    assert sent["document"] == {"name": "orders", "checks": []}  # .json is sent as an object
    out = capsys.readouterr().out
    assert "changed: 0 created, 1 updated, 0 deleted" in out
    assert "None" not in out
    assert "not in the document, left alone: legacy" in out


@pytest.mark.parametrize(("valid", "code"), [(True, 0), (False, 2)])
def test_cli_validate_exits_by_validity_and_prints_every_problem(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
    valid: bool,
    code: int,
) -> None:
    problems = (
        []
        if valid
        else [
            {"location": "checks[1]", "check_name": "bad", "code": "x", "message": "wrong type"},
            {"location": "document", "check_name": None, "code": "y", "message": "no connection"},
        ]
    )
    _cli_with(monkeypatch, _json(200, {"valid": valid, "check_count": 2, "problems": problems}))
    file = tmp_path / "orders.yml"
    file.write_text("name: orders\n")

    exit_code_ = cli.main(["validate", str(file), "--connection", str(uuid.uuid4())])

    assert exit_code_ == code
    out = capsys.readouterr().out
    assert ("checks[1] (bad): wrong type" in out) is not valid
    assert ("document: no connection" in out) is not valid


def test_cli_export_writes_yaml_when_the_output_file_is_yaml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    server = _cli_with(
        monkeypatch,
        httpx.Response(200, text="name: orders\n", headers={"content-type": "application/yaml"}),
    )
    out = tmp_path / "orders.yaml"

    exit_code_ = cli.main(["export", str(SUITE), "-o", str(out)])

    assert exit_code_ == 0
    assert server.requests[0].url.params["format"] == "yaml"
    assert out.read_text() == "name: orders\n"
