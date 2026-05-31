"""AdfProvider.parse_event tests — Azure Monitor payload → RunUpdate.

Pure unit tests (no DB): the provider only transforms bytes → DTO.
"""

import json
from typing import Any

import pytest

from backend.app.orchestration.adf import AdfProvider
from backend.app.orchestration.base import MalformedEventError, RunUpdate

_EVENT: dict[str, Any] = {
    "factoryName": "lll-adf-nonprod",
    "pipelineName": "load_finance",
    "runId": "run-abc-123",
    "status": "Failed",
    "start": "2026-05-31T00:00:00Z",
    "end": "2026-05-31T00:05:00Z",
    "message": "Activity Copy1 failed",
}


def _parse(event: dict[str, Any]) -> RunUpdate:
    return AdfProvider().parse_event(json.dumps(event).encode(), {})


def test_parse_extracts_all_fields() -> None:
    update = _parse(_EVENT)
    assert update.provider_run_id == "run-abc-123"
    assert update.pipeline_or_dag_id == "load_finance"
    assert update.resource_name == "lll-adf-nonprod"
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

    # airflow is a valid provider value but has no impl registered yet.
    with pytest.raises(UnsupportedProviderError, match="airflow"):
        get_orchestration_provider("airflow")


def test_fetch_and_list_are_deferred() -> None:
    provider = AdfProvider()
    with pytest.raises(NotImplementedError):
        provider.fetch_run_detail("factory", "run-1")
    with pytest.raises(NotImplementedError):
        import datetime as _dt

        provider.list_recent_runs(_dt.datetime(2026, 5, 31))
