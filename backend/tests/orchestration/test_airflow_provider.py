"""AirflowProvider.parse_event tests — the signed-callback body → RunUpdate.

Pure unit tests (no auth here — HMAC verification is the endpoint's job; these
exercise parsing + state mapping + the deferred REST methods).
"""

import json
from datetime import datetime
from typing import Any

import pytest

from backend.app.orchestration.airflow import AirflowProvider
from backend.app.orchestration.base import MalformedEventError

_CALLBACK = {
    "dag_id": "load_finance",
    "run_id": "manual__2026-05-31T00:00:00+00:00",
    "state": "success",
    "base_url": "https://airflow.example.com",
    "start_date": "2026-05-31T00:00:00+00:00",
    "end_date": "2026-05-31T00:05:00+00:00",
}


def _payload(**overrides: Any) -> bytes:
    body = {**_CALLBACK, **overrides}
    return json.dumps(body).encode()


def test_provider_identity() -> None:
    p = AirflowProvider()
    assert p.provider == "airflow"
    assert p.resource_config_key == "base_url"


def test_parse_success_maps_to_succeeded() -> None:
    update = AirflowProvider().parse_event(_payload(), {})
    assert update.provider_run_id == "manual__2026-05-31T00:00:00+00:00"
    assert update.pipeline_or_dag_id == "load_finance"
    assert update.resource_name == "https://airflow.example.com"
    assert update.status == "succeeded"
    assert update.started_at == datetime.fromisoformat("2026-05-31T00:00:00+00:00")
    assert update.finished_at == datetime.fromisoformat("2026-05-31T00:05:00+00:00")
    assert update.failure_reason is None


def test_parse_failed_carries_error_reason() -> None:
    update = AirflowProvider().parse_event(_payload(state="failed", error="task X failed"), {})
    assert update.status == "failed"
    assert update.failure_reason == "task X failed"


@pytest.mark.parametrize(
    ("state", "expected"),
    [("success", "succeeded"), ("failed", "failed"), ("running", "running"), ("queued", "queued")],
)
def test_state_mapping(state: str, expected: str) -> None:
    assert AirflowProvider().parse_event(_payload(state=state), {}).status == expected


def test_base_url_trailing_slash_stripped_to_match_connection() -> None:
    update = AirflowProvider().parse_event(_payload(base_url="https://airflow.example.com/"), {})
    assert update.resource_name == "https://airflow.example.com"


@pytest.mark.parametrize("field", ["dag_id", "run_id", "state", "base_url"])
def test_missing_required_field_raises(field: str) -> None:
    body = {k: v for k, v in _CALLBACK.items() if k != field}
    with pytest.raises(MalformedEventError, match="missing required"):
        AirflowProvider().parse_event(json.dumps(body).encode(), {})


def test_unparseable_timestamps_become_none() -> None:
    update = AirflowProvider().parse_event(_payload(start_date="not-a-date", end_date=""), {})
    assert update.started_at is None
    assert update.finished_at is None


def test_unrecognised_state_raises() -> None:
    with pytest.raises(MalformedEventError, match="unrecognised"):
        AirflowProvider().parse_event(_payload(state="up_for_retry"), {})


def test_non_json_body_raises() -> None:
    with pytest.raises(MalformedEventError, match="not valid JSON"):
        AirflowProvider().parse_event(b"not json{", {})


def test_non_object_body_raises() -> None:
    with pytest.raises(MalformedEventError, match="must be a JSON object"):
        AirflowProvider().parse_event(b"[1, 2, 3]", {})


def test_fetch_run_detail_is_not_implemented() -> None:
    # The signed callback is authoritative — no REST enrichment for Airflow.
    with pytest.raises(NotImplementedError):
        AirflowProvider().fetch_run_detail({}, "secret", "run-1")


def test_list_recent_runs_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        AirflowProvider().list_recent_runs(datetime.now())
