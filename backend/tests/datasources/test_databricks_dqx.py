"""DQX engine (ADR 0036 §6): rule building, result mapping, and the one-job-per-run batch."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from backend.app.datasources import databricks_dqx as dqx
from backend.app.datasources.databricks_dqx import DqxConfigError, build_dqx_rule


def test_a_column_is_backtick_quoted_after_the_identifier_allowlist() -> None:
    rule = build_dqx_rule("dqx:is_not_null", {"column": "email"})
    assert rule == {"function": "is_not_null", "arguments": {"column": "`email`"}}


@pytest.mark.parametrize("column", ["a; DROP TABLE t", "a`b", "x) OR (1=1", "", None, 3])
def test_a_column_that_is_not_a_plain_identifier_is_refused(column: Any) -> None:
    with pytest.raises(DqxConfigError):
        build_dqx_rule("dqx:is_not_null", {"column": column})


def test_list_strings_become_escaped_literals_never_expressions() -> None:
    """DQX evaluates a bare string with `F.expr` — 'SM CASE' was resolved as a COLUMN named SM,
    live. Every string must reach it as a quoted literal, embedded quotes doubled."""
    rule = build_dqx_rule(
        "dqx:is_in_list",
        {"column": "status", "allowed": ["SM CASE", "it's", "x') OR true --", 3, 2.5]},
    )
    assert rule["arguments"]["allowed"] == [
        "'SM CASE'",
        "'it''s'",
        "'x'') OR true --'",
        3,
        2.5,
    ]


@pytest.mark.parametrize("allowed", [[], "a,b", [True], [None], [{"a": 1}], ["x" * 1001]])
def test_a_bad_allowed_list_is_refused(allowed: Any) -> None:
    with pytest.raises(DqxConfigError):
        build_dqx_rule("dqx:is_in_list", {"column": "c", "allowed": allowed})


def test_range_limits_accept_numbers_and_iso_dates_only() -> None:
    rule = build_dqx_rule(
        "dqx:is_in_range", {"column": "d", "min_limit": "2024-01-01", "max_limit": 5}
    )
    assert rule["arguments"] == {"column": "`d`", "min_limit": "2024-01-01", "max_limit": 5}


@pytest.mark.parametrize("limit", ["other_col", "1 + (select 1)", True, None, [1]])
def test_a_non_literal_limit_is_refused(limit: Any) -> None:
    """A non-ISO string would reach DQX's `F.expr` — a column reference or worse."""
    with pytest.raises(DqxConfigError):
        build_dqx_rule("dqx:is_not_less_than", {"column": "c", "limit": limit})


def test_regex_passes_through_as_a_string() -> None:
    rule = build_dqx_rule("dqx:regex_match", {"column": "c", "regex": "^[A-Z]{2}$"})
    assert rule["arguments"]["regex"] == "^[A-Z]{2}$"


@pytest.mark.parametrize(
    ("expectation_type", "config"),
    [
        ("dqx:nope", {"column": "c"}),
        ("dqx:is_not_null", {"column": "c", "extra": 1}),
        ("dqx:is_in_range", {"column": "c", "min_limit": 1}),
    ],
)
def test_unknown_types_and_config_keys_are_refused(
    expectation_type: str, config: dict[str, Any]
) -> None:
    with pytest.raises(DqxConfigError):
        build_dqx_rule(expectation_type, config)


def test_the_target_is_three_quoted_identifiers() -> None:
    assert dqx.qualified_table(catalog="main", schema="gold", table="orders") == (
        "`main`.`gold`.`orders`"
    )
    with pytest.raises(DqxConfigError):
        dqx.qualified_table(catalog="main", schema="gold", table="orders; drop")


class _FakeJobs:
    def __init__(
        self, payload: dict[str, Any] | None = None, fail: Exception | None = None
    ) -> None:
        self.payload = payload
        self.fail = fail
        self.submitted: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.cancelled: list[int] = []

    def notebook_path(self) -> str:
        return "/Users/me/.dataq/dqx_runner"

    def upload(self, path: str) -> None:
        pass

    def upload_spec(self, notebook_path: str, spec: dict[str, Any]) -> str:
        self.submitted.append(spec)
        return "/Users/me/.dataq/specs/x.json"

    def delete(self, path: str) -> None:
        self.deleted.append(path)

    def cancel(self, run_id: int) -> None:
        self.cancelled.append(run_id)

    def submit(self, path: str, spec_path: str) -> int:
        return 1

    def wait(self, run_id: int, **_kw: Any) -> dict[str, Any]:
        if self.fail is not None:
            raise self.fail
        assert self.payload is not None
        return self.payload


def _batch(jobs: _FakeJobs, specs: list[tuple[str, dict[str, Any]]]) -> Any:
    return dqx.run_dqx_batch(jobs, specs, catalog="main", schema="gold", table="orders")  # type: ignore[arg-type]


def test_one_job_evaluates_every_rule_and_maps_counts_back_in_order() -> None:
    jobs = _FakeJobs(
        {"rows": 100, "results": {"dataq_0": {"failing": 0}, "dataq_1": {"failing": 7}}}
    )
    first, second = _batch(
        jobs,
        [
            ("dqx:is_not_null", {"column": "id"}),
            ("dqx:is_in_range", {"column": "qty", "min_limit": 1, "max_limit": 9}),
        ],
    )

    assert len(jobs.submitted) == 1
    assert jobs.submitted[0]["table"] == "`main`.`gold`.`orders`"
    assert set(jobs.submitted[0]["checks"]) == {"dataq_0", "dataq_1"}
    assert first.success is True and first.metric_value == 0.0
    assert second.success is False and second.metric_value == 7.0
    assert second.observed_value == {"failing_rows": 7, "rows": 100}


def test_a_rule_dataq_refuses_errors_only_itself() -> None:
    jobs = _FakeJobs({"rows": 5, "results": {"dataq_1": {"failing": 0}}})
    refused, ran = _batch(
        jobs,
        [("dqx:is_not_null", {"column": "bad name"}), ("dqx:is_not_null", {"column": "id"})],
    )
    assert refused.errored is True and "identifier" in (refused.error_message or "")
    assert ran.errored is False and ran.success is True
    assert set(jobs.submitted[0]["checks"]) == {"dataq_1"}


def test_a_rule_the_workspace_rejects_is_classified_not_echoed() -> None:
    jobs = _FakeJobs(
        {"rows": 5, "results": {"dataq_0": {"error": "invalid rule: token=dapiSECRET123 leaked"}}}
    )
    (outcome,) = _batch(jobs, [("dqx:is_not_null", {"column": "id"})])
    assert outcome.errored is True
    assert "dapiSECRET123" not in (outcome.error_message or "")


def test_a_job_failure_errors_the_batch_with_a_classified_reason() -> None:
    jobs = _FakeJobs(fail=RuntimeError("HTTP 403 for https://x/?token=dapiSECRET"))
    outcomes = _batch(
        jobs, [("dqx:is_not_null", {"column": "a"}), ("dqx:is_not_null", {"column": "b"})]
    )
    assert all(o.errored for o in outcomes)
    assert all("dapiSECRET" not in (o.error_message or "") for o in outcomes)


@pytest.mark.parametrize("result", [None, {"failing": -1}, {"failing": True}, {"failing": "3"}])
def test_a_malformed_result_is_an_error_not_a_pass(result: Any) -> None:
    results = {} if result is None else {"dataq_0": result}
    (outcome,) = _batch(
        _FakeJobs({"rows": 1, "results": results}), [("dqx:is_not_null", {"column": "a"})]
    )
    assert outcome.errored is True and outcome.success is False


def test_the_runner_notebook_pins_dqx_and_never_returns_row_values() -> None:
    assert f"databricks-labs-dqx=={dqx.DQX_VERSION}" in dqx.RUNNER_NOTEBOOK
    assert "failing" in dqx.RUNNER_NOTEBOOK
    assert ".collect()" in dqx.RUNNER_NOTEBOOK
    # the only collect() is over the grouped counts, never the table's rows
    assert dqx.RUNNER_NOTEBOOK.count(".collect()") == 1
    assert 'groupBy("e.name").count().collect()' in dqx.RUNNER_NOTEBOOK


# ─────────────── the Jobs/Workspace REST seam ───────────────


def _jobs(handler: Any) -> dqx.DqxJobs:
    import httpx

    return dqx.DqxJobs(
        workspace_url="https://w.example/",
        token="tok",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_the_notebook_lands_in_the_token_users_home_under_a_versioned_name() -> None:
    import httpx

    seen: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        assert request.headers["Authorization"] == "Bearer tok"
        if request.url.path.endswith("/Me"):
            return httpx.Response(200, json={"userName": "ana@x.io"})
        return httpx.Response(200, json={})

    jobs = _jobs(handler)
    path = jobs.notebook_path()
    jobs.upload(path)

    assert path == f"/Users/ana@x.io/.dataq/dqx_runner_{dqx.DQX_VERSION.replace('.', '_')}"
    imported = next(body for method, url, body in seen if url.endswith("/workspace/import"))
    assert imported["path"] == path and imported["overwrite"] is True
    assert base64.b64decode(imported["content"]).decode() == dqx.RUNNER_NOTEBOOK


def test_submit_sends_one_notebook_task_with_the_spec_path_and_a_timeout() -> None:
    import httpx

    bodies: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"run_id": 42})

    assert _jobs(handler).submit("/p", "/p/specs/s.json") == 42
    (task,) = bodies[0]["tasks"]
    # The rules travel as a workspace file: a notebook parameter has a size limit a long
    # allowed-values list can exceed.
    assert task["notebook_task"]["base_parameters"] == {"spec_path": "/p/specs/s.json"}
    assert bodies[0]["timeout_seconds"] > 0


def _runs_handler(state: dict[str, Any], output: Any) -> Any:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/runs/get"):
            return httpx.Response(200, json={"state": state, "tasks": [{"run_id": 7}]})
        return httpx.Response(200, json=output)

    return handler


def test_wait_returns_the_notebook_exit_payload() -> None:
    payload = {"rows": 3, "results": {}}
    handler = _runs_handler(
        {"life_cycle_state": "TERMINATED", "result_state": "SUCCESS"},
        {"notebook_output": {"result": json.dumps(payload)}},
    )
    assert _jobs(handler).wait(1, sleep=lambda _s: None) == payload


@pytest.mark.parametrize(
    ("state", "output"),
    [
        ({"life_cycle_state": "INTERNAL_ERROR", "result_state": "FAILED"}, {}),
        ({"life_cycle_state": "TERMINATED", "result_state": "SUCCESS"}, {}),
        (
            {"life_cycle_state": "TERMINATED", "result_state": "SUCCESS"},
            {"notebook_output": {"result": "[1, 2]"}},
        ),
    ],
)
def test_wait_refuses_a_failed_run_or_a_missing_or_malformed_output(
    state: dict[str, Any], output: Any
) -> None:
    with pytest.raises(RuntimeError):
        _jobs(_runs_handler(state, output)).wait(1, sleep=lambda _s: None)


def test_wait_polls_until_the_run_finishes() -> None:
    import httpx

    states = iter(["PENDING", "RUNNING", "TERMINATED"])
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/runs/get"):
            return httpx.Response(
                200,
                json={
                    "state": {"life_cycle_state": next(states), "result_state": "SUCCESS"},
                    "tasks": [{"run_id": 7}],
                },
            )
        return httpx.Response(200, json={"notebook_output": {"result": '{"rows": 1}'}})

    assert _jobs(handler).wait(1, sleep=sleeps.append) == {"rows": 1}
    assert len(sleeps) == 2


def test_the_spec_file_is_deleted_whether_or_not_the_job_succeeds() -> None:
    ok = _FakeJobs({"rows": 1, "results": {"dataq_0": {"failing": 0}}})
    _batch(ok, [("dqx:is_not_null", {"column": "a"})])
    failed = _FakeJobs(fail=TimeoutError("did not finish"))
    _batch(failed, [("dqx:is_not_null", {"column": "a"})])
    assert ok.deleted == failed.deleted == ["/Users/me/.dataq/specs/x.json"]


def test_an_abandoned_job_is_cancelled_so_it_stops_billing() -> None:
    jobs = _FakeJobs(fail=TimeoutError("did not finish"))
    _batch(jobs, [("dqx:is_not_null", {"column": "a"})])
    assert jobs.cancelled == [1]


def test_a_transient_poll_failure_is_retried_not_fatal() -> None:
    import httpx

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/runs/get"):
            calls["n"] += 1
            if calls["n"] <= 2:
                return httpx.Response(503 if calls["n"] == 1 else 429)
            return httpx.Response(
                200,
                json={
                    "state": {"life_cycle_state": "TERMINATED", "result_state": "SUCCESS"},
                    "tasks": [{"run_id": 7}],
                },
            )
        return httpx.Response(200, json={"notebook_output": {"result": '{"rows": 1}'}})

    assert _jobs(handler).wait(1, sleep=lambda _s: None) == {"rows": 1}
    assert calls["n"] == 3


def test_a_permanent_poll_failure_is_not_retried() -> None:
    import httpx

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(403)

    with pytest.raises(httpx.HTTPStatusError):
        _jobs(handler).wait(1, sleep=lambda _s: None)
    assert calls["n"] == 1


@pytest.mark.parametrize(
    ("value", "literal"),
    [
        ("x\\' || p_container || \\'", "'x\\\\'' || p_container || \\\\'''"),
        ("ends\\", "'ends\\\\'"),
    ],
)
def test_backslashes_are_escaped_before_quotes(value: str, literal: str) -> None:
    """Spark processes backslash escapes in string literals: live (2026-09-28) the first value
    escaped its literal into an expression referencing a column, and one such value failed the
    whole job. Both must stay plain literals."""
    rule = build_dqx_rule("dqx:is_in_list", {"column": "p_container", "allowed": [value]})
    assert rule["arguments"]["allowed"] == [literal]


def test_the_notebook_isolates_a_rule_that_fails_at_run_time() -> None:
    """A combined pass that raises (live: an invalid regex) falls back to one rule at a time, so
    only that rule errors — never every DQX check in the run."""
    notebook = dqx.RUNNER_NOTEBOOK
    assert "counts = failing_counts(valid)" in notebook
    assert "counts.update(failing_counts([rule]))" in notebook
    assert 'results[rule["name"]] = {"error": type(exc).__name__' in notebook
