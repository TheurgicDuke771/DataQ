"""AdfProvider tests — parse_event (payload → RunUpdate) + fetch_run_detail (ARM REST).

Pure unit tests (no DB): parse_event transforms bytes → DTO; fetch_run_detail's
HTTP calls are monkeypatched.
"""

import json
from typing import Any

import httpx
import pytest

from backend.app.orchestration.adf import AdfProvider
from backend.app.orchestration.base import AlertPing, MalformedEventError, RunUpdate


class _FakeResponse:
    def __init__(
        self, *, json_body: dict[str, Any] | None = None, raise_exc: Exception | None = None
    ) -> None:
        self._json = json_body or {}
        self._raise = raise_exc

    def raise_for_status(self) -> None:
        if self._raise is not None:
            raise self._raise

    def json(self) -> dict[str, Any]:
        return self._json


_EVENT: dict[str, Any] = {
    "factoryName": "example-adf-preprod",
    "pipelineName": "load_finance",
    "runId": "run-abc-123",
    "status": "Failed",
    "start": "2026-05-31T00:00:00Z",
    "end": "2026-05-31T00:05:00Z",
    "message": "Activity Copy1 failed",
}

_ADF_CONFIG: dict[str, Any] = {
    "subscription_id": "00000000-0000-0000-0000-000000000001",
    "resource_group": "rg-data",
    "factory_name": "example-adf-preprod",
    "tenant_id": "00000000-0000-0000-0000-0000000000aa",
    "client_id": "00000000-0000-0000-0000-0000000000bb",
}


def _parse(event: dict[str, Any]) -> RunUpdate:
    update = AdfProvider().parse_event(json.dumps(event).encode(), {})
    assert isinstance(update, RunUpdate)
    return update


def _parse_ping(event: dict[str, Any]) -> AlertPing:
    update = AdfProvider().parse_event(json.dumps(event).encode(), {})
    assert isinstance(update, AlertPing)
    return update


# Azure Monitor Common Alert Schema (#492) — the shape a metric alert on
# PipelineFailedRuns (dimension Name=<pipeline>) actually delivers.
_COMMON_ALERT: dict[str, Any] = {
    "schemaId": "azureMonitorCommonAlertSchema",
    "data": {
        "essentials": {
            "alertId": "/subscriptions/sub-1/providers/Microsoft.AlertsManagement/alerts/x",
            "alertRule": "dataq-adf-failed-runs",
            "severity": "Sev3",
            "signalType": "Metric",
            "monitorCondition": "Fired",
            "monitoringService": "Platform",
            "alertTargetIDs": [
                "/subscriptions/sub-1/resourcegroups/dataq-harness-rg"
                "/providers/microsoft.datafactory/factories/dataq-harness-adf"
            ],
            "firedDateTime": "2026-07-02T03:01:00.000Z",
        },
        "alertContext": {
            "condition": {
                "windowSize": "PT5M",
                "allOf": [
                    {
                        "metricName": "PipelineFailedRuns",
                        "metricNamespace": "Microsoft.DataFactory/factories",
                        "dimensions": [
                            {"name": "FailureType", "value": "UserError"},
                            {"name": "Name", "value": "pl_flow_a_orders"},
                        ],
                        "operator": "GreaterThan",
                        "threshold": "0",
                        "timeAggregation": "Total",
                        "metricValue": 1.0,
                    }
                ],
            }
        },
    },
}


def test_common_alert_schema_returns_fired_ping_with_factory_and_pipeline() -> None:
    ping = _parse_ping(_COMMON_ALERT)
    assert ping.monitor_condition == "fired"
    assert ping.resource_name == "dataq-harness-adf"
    assert ping.pipeline_or_dag_id == "pl_flow_a_orders"
    assert ping.fired_at is not None and ping.fired_at.year == 2026


def test_common_alert_schema_resolved_condition() -> None:
    event = json.loads(json.dumps(_COMMON_ALERT))
    event["data"]["essentials"]["monitorCondition"] = "Resolved"
    ping = _parse_ping(event)
    assert ping.monitor_condition == "resolved"


def test_common_alert_schema_tolerates_missing_target_and_dimensions() -> None:
    """Factory/pipeline are enrichment only — the poll doesn't need them."""
    event = json.loads(json.dumps(_COMMON_ALERT))
    event["data"]["essentials"]["alertTargetIDs"] = []
    event["data"]["alertContext"] = {}
    ping = _parse_ping(event)
    assert ping.monitor_condition == "fired"
    assert ping.resource_name is None
    assert ping.pipeline_or_dag_id is None


def test_common_alert_schema_without_essentials_raises_malformed() -> None:
    with pytest.raises(MalformedEventError, match="essentials"):
        _parse({"schemaId": "azureMonitorCommonAlertSchema", "data": {}})


def test_parse_extracts_all_fields() -> None:
    update = _parse(_EVENT)
    assert update.provider_run_id == "run-abc-123"
    assert update.pipeline_or_dag_id == "load_finance"
    assert update.resource_name == "example-adf-preprod"
    assert update.status == "failed"
    assert update.failure_reason == "Activity Copy1 failed"
    assert update.started_at is not None and update.started_at.year == 2026
    assert update.finished_at is not None


@pytest.mark.parametrize(
    ("adf_status", "expected"),
    [
        ("Succeeded", "succeeded"),
        ("Failed", "failed"),
        ("Cancelled", "cancelled"),
        ("Canceled", "cancelled"),
        ("InProgress", "running"),
        ("Queued", "queued"),
        ("fired", "failed"),  # Common-Alert-Schema monitorCondition
    ],
)
def test_status_normalisation(adf_status: str, expected: str) -> None:
    assert _parse({**_EVENT, "status": adf_status}).status == expected


def test_status_defaults_to_failed_when_absent() -> None:
    # The v1 ADF webhook is the failure channel (ADR 0004).
    event = {k: v for k, v in _EVENT.items() if k != "status"}
    assert _parse(event).status == "failed"


def test_unknown_status_raises_malformed() -> None:
    with pytest.raises(MalformedEventError, match="status"):
        _parse({**_EVENT, "status": "Frobnicated"})


@pytest.mark.parametrize("missing", ["factoryName", "pipelineName", "runId"])
def test_missing_required_field_raises_malformed(missing: str) -> None:
    event = {k: v for k, v in _EVENT.items() if k != missing}
    with pytest.raises(MalformedEventError) as excinfo:
        _parse(event)
    assert missing in excinfo.value.detail["missing"]


def test_non_json_body_raises_malformed() -> None:
    with pytest.raises(MalformedEventError, match="not valid JSON"):
        AdfProvider().parse_event(b"not json{", {})


def test_json_array_body_raises_malformed() -> None:
    with pytest.raises(MalformedEventError, match="JSON object"):
        AdfProvider().parse_event(b"[1, 2, 3]", {})


def test_bad_timestamp_degrades_to_none() -> None:
    update = _parse({**_EVENT, "start": "not-a-date"})
    assert update.started_at is None


def test_registry_returns_adf_provider() -> None:
    from backend.app.orchestration.registry import get_orchestration_provider

    assert isinstance(get_orchestration_provider("adf"), AdfProvider)


def test_registry_unknown_provider_raises() -> None:
    from backend.app.orchestration.registry import (
        UnsupportedProviderError,
        get_orchestration_provider,
    )

    # adf + airflow + dbt are all registered now; probe a provider with no impl
    # (prefect is a hypothetical future OrchestrationProvider, ADR 0011).
    with pytest.raises(UnsupportedProviderError, match="prefect"):
        get_orchestration_provider("prefect")


def _query_client(monkeypatch: pytest.MonkeyPatch, *, runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Mock the token POST + the queryPipelineRuns POST (both are httpx.post)."""
    seen: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        if "login.microsoftonline" in url:
            return _FakeResponse(json_body={"access_token": "tok"})
        seen["url"] = url
        seen["body"] = kwargs.get("json")
        return _FakeResponse(json_body={"value": runs})

    monkeypatch.setattr(httpx, "post", fake_post)
    return seen


def test_list_recent_runs_maps_query_response(monkeypatch: pytest.MonkeyPatch) -> None:
    import datetime as _dt

    seen = _query_client(monkeypatch, runs=[_RUN_DETAIL])
    updates = AdfProvider().list_recent_runs(
        _ADF_CONFIG, "sp-secret", _dt.datetime(2026, 5, 31, tzinfo=_dt.UTC)
    )

    assert len(updates) == 1
    assert updates[0].provider_run_id == "run-abc-123"
    assert updates[0].pipeline_or_dag_id == "load_finance"
    assert updates[0].resource_name == _ADF_CONFIG["factory_name"]
    assert updates[0].status == "succeeded"
    # hit queryPipelineRuns over the lastUpdatedAfter window with NO status filter
    # — the poll records all statuses now (#490); trigger-on-success is downstream.
    assert "queryPipelineRuns" in seen["url"]
    assert "filters" not in seen["body"]
    assert seen["body"]["lastUpdatedAfter"].startswith("2026-05-31")


def test_list_recent_runs_skips_malformed_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    import datetime as _dt

    # one good row, one missing runId, one unknown status → only the good one maps
    _query_client(
        monkeypatch,
        runs=[
            _RUN_DETAIL,
            {**_RUN_DETAIL, "runId": None},
            {**_RUN_DETAIL, "status": "Frobnicated"},
        ],
    )
    updates = AdfProvider().list_recent_runs(
        _ADF_CONFIG, "sp-secret", _dt.datetime(2026, 5, 31, tzinfo=_dt.UTC)
    )
    assert [u.provider_run_id for u in updates] == ["run-abc-123"]


# ───────────────────────── fetch_run_detail (ARM REST) ─────────────

_RUN_DETAIL = {
    "runId": "run-abc-123",
    "pipelineName": "load_finance",
    "status": "Succeeded",
    "runStart": "2026-05-31T00:00:00Z",
    "runEnd": "2026-05-31T00:05:00Z",
    "message": None,
}


def _detail_client(
    monkeypatch: pytest.MonkeyPatch, *, run_detail: dict[str, Any]
) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(json_body={"access_token": "tok"})

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        seen["url"] = url
        seen["auth"] = kwargs["headers"]["Authorization"]
        return _FakeResponse(json_body=run_detail)

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)
    return seen


def test_fetch_run_detail_maps_arm_response(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _detail_client(monkeypatch, run_detail=_RUN_DETAIL)
    update = AdfProvider().fetch_run_detail(_ADF_CONFIG, "sp-secret", "run-abc-123")

    assert update.provider_run_id == "run-abc-123"
    assert update.pipeline_or_dag_id == "load_finance"
    assert update.resource_name == _ADF_CONFIG["factory_name"]
    assert update.status == "succeeded"
    assert update.started_at is not None and update.finished_at is not None
    # called the per-run ARM URL with the bearer token
    assert "run-abc-123" in seen["url"] and seen["auth"] == "Bearer tok"


def test_fetch_run_detail_rejects_unknown_status(monkeypatch: pytest.MonkeyPatch) -> None:
    _detail_client(monkeypatch, run_detail={**_RUN_DETAIL, "status": "Frobnicated"})
    with pytest.raises(ValueError, match="unrecognised status"):
        AdfProvider().fetch_run_detail(_ADF_CONFIG, "sp-secret", "run-abc-123")


def test_fetch_run_detail_propagates_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx, "post", lambda url, **kw: _FakeResponse(json_body={"access_token": "t"})
    )
    http_error = httpx.HTTPStatusError("404", request=None, response=None)  # type: ignore[arg-type]
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _FakeResponse(raise_exc=http_error))
    with pytest.raises(httpx.HTTPStatusError):
        AdfProvider().fetch_run_detail(_ADF_CONFIG, "sp-secret", "run-abc-123")
