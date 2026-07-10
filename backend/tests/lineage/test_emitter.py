"""Tests for the OpenLineage emitter — the env gate + the pure event builders.

No network, no DB: the gate is exercised via monkeypatched env vars, and the
builders run over transient (session-less) model instances. The PII property is
asserted by serializing a whole terminal event and checking a sample-row sentinel
never appears.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from backend.app.core.config import get_settings
from backend.app.db.models import FAILING_TIERS, Asset, Check, Result, Run, Suite
from backend.app.lineage import emitter


def _reconfigure() -> None:
    """Make a mid-test env change visible: drop the cached Settings + client."""
    get_settings.cache_clear()
    emitter.reset_openlineage_client_cache()


# ─────────────────────────────── model factories ───────────────────────────────


def _asset() -> Asset:
    return Asset(id=uuid.uuid4(), namespace="snowflake://org-acct", name="DB.SCHEMA.ORDERS")


def _suite() -> Suite:
    return Suite(id=uuid.uuid4(), name="Retail Orders", connection_id=uuid.uuid4())


def _run(*, status: str, asset_id: uuid.UUID | None, failure_reason: str | None = None) -> Run:
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    return Run(
        id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        asset_id=asset_id,
        status=status,
        started_at=now,
        finished_at=now,
        failure_reason=failure_reason,
    )


def _check(
    *, kind: str = "expectation", expectation_type: str = "expect_x", column: str | None = None
) -> Check:
    config: dict[str, object] = {}
    if column is not None:
        config["column"] = column
    return Check(
        id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        name="c",
        kind=kind,
        expectation_type=expectation_type,
        config=config,
    )


def _result(
    check: Check,
    *,
    status: str,
    metric_value: Decimal | None = None,
    observed: dict[str, Any] | None = None,
    sample: dict[str, Any] | None = None,
) -> Result:
    return Result(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        check_id=check.id,
        status=status,
        metric_value=metric_value,
        observed_value=observed,
        sample_failures=sample,
    )


# ─────────────────────────────────── the gate ──────────────────────────────────


def test_unconfigured_is_dark(monkeypatch: pytest.MonkeyPatch) -> None:
    # No env → not configured, no client.
    assert emitter.is_emission_configured() is False
    assert emitter.get_openlineage_client() is None


@pytest.mark.parametrize("var", ["OPENLINEAGE_URL", *emitter._ADVANCED_TRANSPORT_ENV_VARS])
def test_any_transport_var_configures(monkeypatch: pytest.MonkeyPatch, var: str) -> None:
    monkeypatch.setenv(var, "http://localhost:5000" if var == "OPENLINEAGE_URL" else "console")
    _reconfigure()
    assert emitter.is_emission_configured() is True


@pytest.mark.parametrize("disabled", ["1", "true", "TRUE", "Yes"])
def test_disabled_forces_dark_even_with_url(monkeypatch: pytest.MonkeyPatch, disabled: str) -> None:
    monkeypatch.setenv("OPENLINEAGE_URL", "http://localhost:5000")
    monkeypatch.setenv("OPENLINEAGE_DISABLED", disabled)
    _reconfigure()
    assert emitter.is_emission_configured() is False
    assert emitter.get_openlineage_client() is None


def test_url_constructs_a_client(monkeypatch: pytest.MonkeyPatch) -> None:
    # A dead URL is fine — construction never connects (emit would).
    monkeypatch.setenv("OPENLINEAGE_URL", "http://127.0.0.1:1")
    _reconfigure()
    client = emitter.get_openlineage_client()
    assert client is not None
    # Cached: the same instance comes back without re-reading the env.
    assert emitter.get_openlineage_client() is client


def test_client_cache_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    assert emitter.get_openlineage_client() is None  # cached None (unconfigured)
    monkeypatch.setenv("OPENLINEAGE_URL", "http://127.0.0.1:1")
    get_settings.cache_clear()
    assert emitter.get_openlineage_client() is None  # still cached None until reset
    emitter.reset_openlineage_client_cache()
    assert emitter.get_openlineage_client() is not None


def test_bad_transport_config_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    # A structurally-invalid transport type must return None, never raise.
    monkeypatch.setenv("OPENLINEAGE__TRANSPORT__TYPE", "not-a-real-transport")
    _reconfigure()
    assert emitter.get_openlineage_client() is None


def test_construction_failure_does_not_latch(monkeypatch: pytest.MonkeyPatch) -> None:
    # A transient construction failure must self-heal on the next call (review
    # finding: latching None went dark for the process lifetime).
    monkeypatch.setenv("OPENLINEAGE_URL", "http://127.0.0.1:1")
    _reconfigure()
    calls = {"n": 0}
    real_build = emitter._build_client

    def _flaky() -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient transport failure")
        return real_build()

    monkeypatch.setattr(emitter, "_build_client", _flaky)
    assert emitter.get_openlineage_client() is None  # first call fails open...
    assert emitter.get_openlineage_client() is not None  # ...and retries, no reset
    assert calls["n"] == 2


def test_severity_map_covers_every_failing_tier() -> None:
    # Derived from the #657 single source: a future tier must map, warn stays warn,
    # everything else is an error; non-failing statuses carry no severity.
    assert set(emitter._SEVERITY_MAP) == set(FAILING_TIERS)
    assert emitter._SEVERITY_MAP["warn"] == "warn"
    assert all(v == "error" for k, v in emitter._SEVERITY_MAP.items() if k != "warn")
    for status in ("pass", "skip", "error"):
        assert status not in emitter._SEVERITY_MAP


# ───────────────────────────────── build: START ────────────────────────────────


def test_start_event_shape() -> None:
    from openlineage.client.event_v2 import RunState

    asset = _asset()
    run = _run(status="running", asset_id=asset.id)
    suite = _suite()
    event = emitter.build_start_event(run, suite, asset)

    assert event.eventType == RunState.START
    assert event.producer == "https://github.com/TheurgicDuke771/DataQ"
    assert event.run.runId == str(run.id)
    assert event.job.namespace == "dataq"
    # Stable job identity: the suite id (names are renameable + not unique); the
    # human-readable name rides the documentation facet for consumer display.
    assert event.job.name == f"suite.{suite.id}"
    assert event.job.facets["documentation"].description == "Retail Orders"
    assert event.eventTime == "2026-07-10T12:00:00+00:00"
    # Bare input dataset (no facets yet at START).
    assert len(event.inputs) == 1
    assert event.inputs[0].namespace == asset.namespace
    assert event.inputs[0].name == asset.name
    assert event.inputs[0].inputFacets == {}


def test_no_asset_means_no_inputs() -> None:
    run = _run(status="running", asset_id=None)
    event = emitter.build_start_event(run, _suite(), None)
    assert event.inputs == []


def test_naive_timestamp_is_assumed_utc() -> None:
    asset = _asset()
    run = _run(status="running", asset_id=asset.id)
    run.started_at = datetime(2026, 7, 10, 9, 30)  # naive — no tzinfo
    event = emitter.build_start_event(run, _suite(), asset)
    assert event.eventTime == "2026-07-10T09:30:00+00:00"


def test_missing_timestamps_fall_back_to_now() -> None:
    asset = _asset()
    run = _run(status="running", asset_id=asset.id)
    run.started_at = None
    # No started_at → now(); just assert a tz-aware ISO string was produced.
    event = emitter.build_start_event(run, _suite(), asset)
    assert event.eventTime.endswith("+00:00")


def test_result_without_matching_check_is_omitted() -> None:
    asset = _asset()
    run = _run(status="succeeded", asset_id=asset.id)
    check = _check(expectation_type="expect_present")
    orphan = _check(expectation_type="expect_gone")  # not in the checks list
    event = emitter.build_terminal_event(
        run,
        _suite(),
        asset,
        [check],
        [_result(check, status="pass"), _result(orphan, status="fail")],
    )
    facet = event.inputs[0].inputFacets["dataQualityAssertions"]
    # Only the in-list check's result survives; the orphan is skipped.
    assert [a.assertion for a in facet.assertions] == ["expect_present"]


# ──────────────────────────────── build: terminal ──────────────────────────────


@pytest.mark.parametrize(
    ("status", "state"),
    [("succeeded", "COMPLETE"), ("failed", "FAIL"), ("cancelled", "ABORT")],
)
def test_terminal_event_type_mapping(status: str, state: str) -> None:
    from openlineage.client.event_v2 import RunState

    asset = _asset()
    run = _run(
        status=status,
        asset_id=asset.id,
        failure_reason="setup failed" if status == "failed" else None,
    )
    event = emitter.build_terminal_event(run, _suite(), asset, [], [])
    assert event.eventType == getattr(RunState, state)


@pytest.mark.parametrize("status", ["queued", "running"])
def test_nonterminal_status_maps_to_fail(status: str) -> None:
    # A crashed/reaped run that emitted START but never reached a terminal DB
    # status must still close as FAIL — a START is never left dangling.
    from openlineage.client.event_v2 import RunState

    asset = _asset()
    run = _run(status=status, asset_id=asset.id)
    event = emitter.build_terminal_event(run, _suite(), asset, [], [])
    assert event.eventType == RunState.FAIL


def test_assertions_facet_maps_check_result_pairs() -> None:
    asset = _asset()
    run = _run(status="failed", asset_id=asset.id)
    passing = _check(expectation_type="expect_not_null", column="EMAIL")
    warned = _check(expectation_type="expect_unique")
    failed = _check(expectation_type="expect_in_set")
    errored = _check(expectation_type="expect_range")
    skipped = _check(expectation_type="expect_present")
    checks = [passing, warned, failed, errored, skipped]
    results = [
        _result(passing, status="pass"),
        _result(warned, status="warn"),
        _result(failed, status="fail"),
        _result(errored, status="error"),
        _result(skipped, status="skip"),
    ]
    event = emitter.build_terminal_event(run, _suite(), asset, checks, results)
    facet = event.inputs[0].inputFacets["dataQualityAssertions"]

    # skip AND operational error are omitted (neither is a DQ verdict — an errored
    # check as success=False would read downstream as "the data failed this check");
    # the three evaluated outcomes map through.
    assert len(facet.assertions) == 3
    by_assertion = {a.assertion: a for a in facet.assertions}
    assert "expect_range" not in by_assertion
    assert "expect_present" not in by_assertion
    assert by_assertion["expect_not_null"].success is True
    assert by_assertion["expect_not_null"].column == "EMAIL"
    assert by_assertion["expect_not_null"].severity is None
    assert by_assertion["expect_unique"].success is False
    assert by_assertion["expect_unique"].severity == "warn"
    assert by_assertion["expect_in_set"].success is False
    assert by_assertion["expect_in_set"].severity == "error"


def test_assertions_facet_absent_when_all_skipped() -> None:
    asset = _asset()
    run = _run(status="succeeded", asset_id=asset.id)
    check = _check()
    event = emitter.build_terminal_event(
        run, _suite(), asset, [check], [_result(check, status="skip")]
    )
    assert "dataQualityAssertions" not in event.inputs[0].inputFacets


def test_assertion_falls_back_to_kind_when_no_expectation_type() -> None:
    asset = _asset()
    run = _run(status="succeeded", asset_id=asset.id)
    # A monitor stores `monitor:<kind>` in expectation_type; use an empty string to
    # force the kind fallback branch.
    check = _check(kind="freshness", expectation_type="")
    event = emitter.build_terminal_event(
        run, _suite(), asset, [check], [_result(check, status="pass")]
    )
    facet = event.inputs[0].inputFacets["dataQualityAssertions"]
    assert facet.assertions[0].assertion == "freshness"


def test_volume_metric_populates_row_count() -> None:
    asset = _asset()
    run = _run(status="succeeded", asset_id=asset.id)
    volume = _check(kind="volume", expectation_type="monitor:volume")
    other = _check(kind="expectation")
    checks = [volume, other]
    results = [
        _result(other, status="pass"),
        # metric_value is the DEVIATION %, not the count — the count is the
        # observed_value["row_count"] aggregate (monitors.py). 12.5 here proves
        # the facet reads the count, never the banded metric.
        _result(
            volume,
            status="pass",
            metric_value=Decimal("12.5"),
            observed={"row_count": 1234, "deviation_pct": 12.5},
        ),
    ]
    event = emitter.build_terminal_event(run, _suite(), asset, checks, results)
    metrics = event.inputs[0].inputFacets["dataQualityMetrics"]
    assert metrics.rowCount == 1234


def test_volume_metric_without_usable_count_is_skipped() -> None:
    asset = _asset()
    run = _run(status="succeeded", asset_id=asset.id)
    volume = _check(kind="volume", expectation_type="monitor:volume")
    # No observed row_count (or a non-int / bool one) → the metrics facet is dropped.
    for observed in (None, {}, {"row_count": "1234"}, {"row_count": True}):
        event = emitter.build_terminal_event(
            run,
            _suite(),
            asset,
            [volume],
            [_result(volume, status="pass", metric_value=Decimal("12.5"), observed=observed)],
        )
        assert "dataQualityMetrics" not in event.inputs[0].inputFacets


def test_no_metrics_facet_without_volume() -> None:
    asset = _asset()
    run = _run(status="succeeded", asset_id=asset.id)
    check = _check(kind="expectation")
    event = emitter.build_terminal_event(
        run, _suite(), asset, [check], [_result(check, status="pass", metric_value=Decimal("5"))]
    )
    assert "dataQualityMetrics" not in event.inputs[0].inputFacets


def test_failed_run_carries_error_facet() -> None:
    asset = _asset()
    run = _run(
        status="failed", asset_id=asset.id, failure_reason="connection could not be established"
    )
    event = emitter.build_terminal_event(run, _suite(), asset, [], [])
    err = event.run.facets["errorMessage"]
    assert err.message == "connection could not be established"
    assert err.programmingLanguage == "python"
    assert err.stackTrace is None


def test_failed_run_without_reason_has_no_error_facet() -> None:
    asset = _asset()
    run = _run(status="failed", asset_id=asset.id, failure_reason=None)
    event = emitter.build_terminal_event(run, _suite(), asset, [], [])
    assert "errorMessage" not in event.run.facets


def test_succeeded_run_has_no_error_facet() -> None:
    asset = _asset()
    run = _run(status="succeeded", asset_id=asset.id, failure_reason="stale reason")
    event = emitter.build_terminal_event(run, _suite(), asset, [], [])
    assert "errorMessage" not in event.run.facets


# ─────────────────────────────────── PII rule ──────────────────────────────────


def test_sample_failures_never_leak_into_the_event() -> None:
    from openlineage.client.serde import Serde

    asset = _asset()
    run = _run(status="failed", asset_id=asset.id, failure_reason="a check failed")
    check = _check(expectation_type="expect_in_set", column="SSN")
    # A result whose sample rows carry a sentinel that must NEVER be serialized.
    sentinel = "SSN-SENTINEL-12345"
    result = _result(
        check,
        status="fail",
        sample={"rows": [{"SSN": sentinel, "NAME": "leak-me"}]},
    )
    event = emitter.build_terminal_event(run, _suite(), asset, [check], [result])
    serialized = Serde.to_json(event)
    assert sentinel not in serialized
    assert "leak-me" not in serialized
