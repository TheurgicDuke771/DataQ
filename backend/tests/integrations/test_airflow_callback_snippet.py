"""Round-trip tests for the user-facing Airflow DAG callback snippet.

The snippet (`integrations/airflow/dataq_airflow_callback.py`) is the
*producer* half of the Airflow integration; `backend/app/api/v1/orchestration.py`
is the *consumer*. These tests load the snippet by path and assert producer and
consumer agree on **both** axes: the HMAC the snippet signs is accepted by the
receiver's `_authenticate_airflow`, and the JSON it builds parses cleanly through
`AirflowProvider.parse_event`. If either side drifts (header name, signing input,
field names, state map), a test here fails before a user's DAG silently does.
"""

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from backend.app.api.v1.orchestration import WebhookAuthError, _authenticate_airflow
from backend.app.core.config import get_settings
from backend.app.core.secrets import SecretNotFoundError
from backend.app.orchestration.airflow import AirflowProvider

_KEY = "shared-hmac-signing-key"


def _load_snippet() -> Any:
    """Import the copy-paste snippet from docs/ (it lives outside the app package)."""
    repo_root = Path(__file__).resolve().parents[3]
    path = repo_root / "integrations" / "airflow" / "dataq_airflow_callback.py"
    spec = importlib.util.spec_from_file_location("dataq_airflow_callback", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


snippet = _load_snippet()


class _FakeStore:
    """Minimal SecretStore: serves the Airflow signing key under its config name."""

    def __init__(self, **data: str) -> None:
        self.data = dict(data)

    def get(self, name: str) -> str:
        if name not in self.data:
            raise SecretNotFoundError(name)
        return self.data[name]

    def set(self, name: str, value: str) -> None:
        self.data[name] = value


def _store(key: str = _KEY) -> _FakeStore:
    return _FakeStore(**{get_settings().airflow_webhook_secret_name: key})


class _FakeDagRun:
    dag_id = "load_finance"
    run_id = "manual__2026-06-01T00:00:00+00:00"
    start_date = None
    end_date = None


# ── producer/consumer agreement on the raw functions ──


def test_snippet_signature_is_accepted_by_the_receiver() -> None:
    body = snippet.build_payload(
        dag_id="load_finance",
        run_id="run-1",
        state="success",
        base_url="https://airflow.example.com",
    )
    signature = snippet.sign(_KEY, body)
    # Must not raise: the snippet's HMAC equals the receiver's expected digest.
    _authenticate_airflow(body, signature, _store())


def test_snippet_payload_parses_into_expected_runupdate() -> None:
    body = snippet.build_payload(
        dag_id="load_finance",
        run_id="run-1",
        state="success",
        base_url="https://airflow.example.com/",  # trailing slash
        start_date="2026-06-01T00:00:00+00:00",
        end_date="2026-06-01T00:05:00+00:00",
    )
    update = AirflowProvider().parse_event(body, {})
    assert update.pipeline_or_dag_id == "load_finance"
    assert update.provider_run_id == "run-1"
    assert update.status == "succeeded"
    assert update.resource_name == "https://airflow.example.com"  # rstripped both sides
    assert update.started_at is not None and update.finished_at is not None


def test_failure_payload_carries_error_and_maps_to_failed() -> None:
    body = snippet.build_payload(
        dag_id="d", run_id="r", state="failed", base_url="https://a", error="task X failed"
    )
    update = AirflowProvider().parse_event(body, {})
    assert update.status == "failed"
    assert update.failure_reason == "task X failed"


def test_tampered_body_is_rejected() -> None:
    body = snippet.build_payload(dag_id="d", run_id="r", state="failed", base_url="https://a")
    signature = snippet.sign(_KEY, body)
    with pytest.raises(WebhookAuthError):
        _authenticate_airflow(body + b" ", signature, _store())


def test_wrong_key_is_rejected() -> None:
    body = snippet.build_payload(dag_id="d", run_id="r", state="success", base_url="https://a")
    signature = snippet.sign("a-different-key", body)
    with pytest.raises(WebhookAuthError):
        _authenticate_airflow(body, signature, _store())


# ── the callback entry points, end to end ──


def test_notify_is_safe_and_silent_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATAQ_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("DATAQ_WEBHOOK_SECRET", raising=False)
    posts: list[Any] = []
    monkeypatch.setattr(snippet, "_post", lambda *a, **k: posts.append(a))
    snippet.on_dataq_success({"dag_run": _FakeDagRun()})  # must not raise
    assert posts == []  # nothing sent without config


def test_callback_emits_a_receiver_acceptable_signed_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATAQ_WEBHOOK_URL", "https://dataq.example.com/api/v1/orchestration/events/airflow"
    )
    monkeypatch.setenv("DATAQ_WEBHOOK_SECRET", _KEY)
    monkeypatch.setenv("DATAQ_AIRFLOW_BASE_URL", "https://airflow.example.com")

    captured: dict[str, Any] = {}

    def fake_post(url: str, body: bytes, signature: str) -> int:
        captured["url"] = url
        captured["body"] = body
        captured["signature"] = signature
        return 200

    monkeypatch.setattr(snippet, "_post", fake_post)
    snippet.on_dataq_success({"dag_run": _FakeDagRun()})

    # Exactly what the callback emitted is accepted + parsed by the real receiver.
    _authenticate_airflow(captured["body"], captured["signature"], _store())
    update = AirflowProvider().parse_event(captured["body"], {})
    assert update.pipeline_or_dag_id == "load_finance"
    assert update.provider_run_id == _FakeDagRun.run_id
    assert update.status == "succeeded"
    assert update.resource_name == "https://airflow.example.com"
