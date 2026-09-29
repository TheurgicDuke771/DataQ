"""DQX stream mode (#2227): resume points, grouping, and the stream report DataQ persists."""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.datasources import databricks_dqx as dqx
from backend.app.datasources.databricks_dqx import DqxConfigError, build_dqx_rule
from backend.tests.datasources.test_databricks_dqx import _FakeJobs

_STREAM = {"column": "status", "mode": "stream"}
_TABLE_ID = "346d89e1-34ea-444e-8f5f-fd56931cbbcb"


def _batch(
    jobs: _FakeJobs,
    specs: list[tuple[str, dict[str, Any]]],
    previous: list[dict[str, Any] | None] | None = None,
    volume: str | None = "main.ops.dataq_ckpt",
) -> Any:
    return dqx.run_dqx_batch(
        jobs,  # type: ignore[arg-type]
        specs,
        catalog="main",
        schema="gold",
        table="orders",
        previous=previous,
        checkpoint_volume=volume,
    )


def _report(**over: Any) -> dict[str, Any]:
    return {
        "table_id": _TABLE_ID,
        "from_version": 2,
        "next_version": 3,
        "restarted": None,
        "batches": 1,
        "change_commits_skipped": 0,
        **over,
    }


def test_mode_is_optional_and_never_reaches_the_rule() -> None:
    assert build_dqx_rule("dqx:is_not_null", _STREAM) == build_dqx_rule(
        "dqx:is_not_null", {"column": "status"}
    )
    assert dqx.dqx_mode({"column": "c"}) == "snapshot"


@pytest.mark.parametrize("mode", ["STREAM", "batch", None, 1])
def test_an_unknown_mode_is_refused(mode: Any) -> None:
    with pytest.raises(DqxConfigError):
        build_dqx_rule("dqx:is_not_null", {"column": "c", "mode": mode})


def test_the_checkpoint_volume_becomes_a_volumes_path_of_identifiers() -> None:
    assert dqx.checkpoint_root("main.ops.ckpt") == "/Volumes/main/ops/ckpt"
    for bad in ["main.ops", "main.ops.ck/../x", "a.b.c.d", "main.ops.ck pt"]:
        with pytest.raises(ValueError):
            dqx.checkpoint_root(bad)


def test_a_stream_check_with_no_history_reads_from_the_start_in_its_own_group() -> None:
    jobs = _FakeJobs(
        {
            "rows": 9,
            "results": {
                "dataq_0": {"failing": 1},
                "dataq_1": {"failing": 2, "rows": 6, "stream": _report(from_version=None)},
            },
        }
    )
    snap, stream = _batch(
        jobs, [("dqx:is_not_null", {"column": "id"}), ("dqx:is_not_null", _STREAM)]
    )
    spec = jobs.submitted[0]
    assert set(spec["checks"]) == {"dataq_0"}
    assert spec["streams"] == [
        {
            "starting_version": None,
            "table_id": None,
            "checks": {"dataq_1": build_dqx_rule("dqx:is_not_null", _STREAM)},
        }
    ]
    assert spec["checkpoint_root"] == "/Volumes/main/ops/dataq_ckpt"
    assert snap.observed_value == {"failing_rows": 1, "rows": 9}
    # A stream result's rows are the rows it evaluated, not the table's row count.
    assert stream.observed_value["rows"] == 6 and stream.metric_value == 2.0
    assert stream.observed_value["stream"]["table"] == "main.gold.orders"
    assert stream.observed_value["stream"]["next_version"] == 3


def test_a_snapshot_only_run_sends_no_stream_or_checkpoint() -> None:
    jobs = _FakeJobs({"rows": 1, "results": {"dataq_0": {"failing": 0}}})
    _batch(jobs, [("dqx:is_not_null", {"column": "id"})], volume=None)
    assert jobs.submitted[0]["streams"] == [] and jobs.submitted[0]["checkpoint_root"] is None


def test_stream_checks_resume_from_the_version_their_last_result_recorded() -> None:
    prior = {"failing_rows": 0, "rows": 4, "stream": {**_report(), "table": "main.gold.orders"}}
    snapshot_prior = {"failing_rows": 0, "rows": 4}  # a snapshot-mode result: no resume point
    jobs = _FakeJobs({"rows": None, "results": {}})
    _batch(
        jobs,
        [
            ("dqx:is_not_null", _STREAM),
            ("dqx:is_not_empty", _STREAM),
            ("dqx:is_not_null", {"column": "id", "mode": "stream"}),
        ],
        previous=[prior, prior, snapshot_prior],
    )
    groups = {
        (g["starting_version"], g["table_id"]): set(g["checks"])
        for g in jobs.submitted[0]["streams"]
    }
    assert groups == {(3, _TABLE_ID): {"dataq_0", "dataq_1"}, (None, None): {"dataq_2"}}


@pytest.mark.parametrize(
    "state",
    [
        {**_report(), "table": "main.gold.other"},  # the suite was re-pointed
        {**_report(next_version=-1), "table": "main.gold.orders"},
        {**_report(next_version=True), "table": "main.gold.orders"},
        {**_report(next_version="3"), "table": "main.gold.orders"},
        {**_report(table_id=""), "table": "main.gold.orders"},
        "garbage",
    ],
)
def test_a_resume_point_dataq_cannot_trust_restarts_from_the_beginning(state: Any) -> None:
    jobs = _FakeJobs({"rows": None, "results": {}})
    _batch(jobs, [("dqx:is_not_null", _STREAM)], previous=[{"stream": state}])
    assert jobs.submitted[0]["streams"][0]["starting_version"] is None


def test_a_stream_check_without_a_checkpoint_volume_errors_alone_and_submits_nothing() -> None:
    jobs = _FakeJobs({"rows": 1, "results": {}})
    (outcome,) = _batch(jobs, [("dqx:is_not_null", _STREAM)], volume=None)
    assert outcome.errored is True and "checkpoint volume" in (outcome.error_message or "")
    assert jobs.submitted == []


@pytest.mark.parametrize(
    "stream",
    [None, _report(next_version=None), _report(next_version=-2), _report(table_id=7), "x"],
)
def test_a_malformed_stream_report_is_an_error_so_no_resume_point_is_persisted(
    stream: Any,
) -> None:
    jobs = _FakeJobs(
        {"rows": None, "results": {"dataq_0": {"failing": 0, "rows": 3, "stream": stream}}}
    )
    (outcome,) = _batch(jobs, [("dqx:is_not_null", _STREAM)])
    assert outcome.errored is True and outcome.observed_value is None


def test_an_unrecognised_restart_reason_is_dropped_not_stored() -> None:
    report = _report(restarted="anything else")
    jobs = _FakeJobs(
        {"rows": None, "results": {"dataq_0": {"failing": 0, "rows": 2, "stream": report}}}
    )
    (outcome,) = _batch(jobs, [("dqx:is_not_null", _STREAM)])
    assert outcome.observed_value["stream"]["restarted"] is None


@pytest.mark.parametrize("rows", [None, -1, "3", True])
def test_a_stream_result_without_a_row_count_is_an_error(rows: Any) -> None:
    jobs = _FakeJobs(
        {"rows": None, "results": {"dataq_0": {"failing": 0, "rows": rows, "stream": _report()}}}
    )
    (outcome,) = _batch(jobs, [("dqx:is_not_null", _STREAM)])
    assert outcome.errored is True


def test_a_stream_run_with_no_new_rows_is_a_skip_not_a_pass() -> None:
    """Zero appended rows evaluated nothing; a pass would read as a clean bill of health."""
    report = _report(from_version=4, next_version=4, batches=0)
    jobs = _FakeJobs(
        {"rows": None, "results": {"dataq_0": {"failing": 0, "rows": 0, "stream": report}}}
    )
    (outcome,) = _batch(jobs, [("dqx:is_not_null", _STREAM)])
    assert outcome.skipped is True and outcome.metric_value is None
    assert outcome.observed_value["rows"] == 0
    assert outcome.observed_value["stream"]["next_version"] == 4


def test_the_notebook_never_lets_a_stream_start_past_the_latest_commit() -> None:
    """A stream that fails fails the whole Databricks command even when caught (live), so a
    resume version beyond the latest commit must start no stream at all."""
    notebook = dqx.RUNNER_NOTEBOOK
    assert "if start is None or start <= latest:" in notebook
    assert '"history_expired"' in notebook and '"table_replaced"' in notebook
    assert '.option("skipChangeCommits", "true")' in notebook
    assert "trigger(availableNow=True)" in notebook
    assert "dbutils.fs.rm(checkpoint, True)" in notebook


def test_the_connection_validates_and_normalises_the_checkpoint_volume() -> None:
    from pydantic import ValidationError

    from backend.app.datasources.unity_catalog import UnityCatalogConfig

    base = {"workspace_url": "https://w.example", "warehouse_id": "w"}
    assert UnityCatalogConfig.model_validate(base).dqx_checkpoint_volume is None
    blank = UnityCatalogConfig.model_validate({**base, "dqx_checkpoint_volume": " "})
    assert blank.dqx_checkpoint_volume is None
    ok = UnityCatalogConfig.model_validate({**base, "dqx_checkpoint_volume": " main.ops.ck "})
    assert ok.dqx_checkpoint_volume == "main.ops.ck"
    with pytest.raises(ValidationError):
        UnityCatalogConfig.model_validate({**base, "dqx_checkpoint_volume": "main.ops.ck/.."})


def test_the_uc_runner_forwards_the_volume_and_previous_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.datasources import unity_catalog
    from backend.app.datasources.unity_catalog import UnityCatalogCheckRunner, UnityCatalogConfig

    seen: dict[str, Any] = {}

    def _fake_batch(jobs: Any, specs: Any, **kw: Any) -> list[Any]:
        seen.update(kw)
        return []

    monkeypatch.setattr(unity_catalog, "run_dqx_batch", _fake_batch)
    config = UnityCatalogConfig.model_validate(
        {
            "workspace_url": "https://w.example",
            "warehouse_id": "w",
            "dqx_checkpoint_volume": "m.o.c",
        }
    )
    runner = UnityCatalogCheckRunner(config=config, token="t", catalog="main")
    runner.run_native_checks(
        "dqx",
        [("expectation", "dqx:is_not_null", _STREAM)],
        table="orders",
        schema="gold",
        previous=[{"x": 1}],
    )
    assert seen["checkpoint_volume"] == "m.o.c" and seen["previous"] == [{"x": 1}]
