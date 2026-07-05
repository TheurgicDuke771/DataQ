"""Round-trip tests for the user-facing dbt build callback snippet.

The snippet (`integrations/dbt/dataq_dbt_callback.py`) is the *producer* half of the
dbt integration; `backend/app/api/v1/orchestration.py` is the *consumer*. These
tests load the snippet by path and assert producer and consumer agree on both axes:
the HMAC the snippet signs is accepted by the receiver's `_authenticate_dbt`, and
the JSON it builds (from a `run_results.json`) parses cleanly through
`DbtProvider.parse_event`. If either side drifts (header name, signing input, field
names, status map), a test here fails before a user's build silently does.
"""

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from backend.app.api.v1.orchestration import WebhookAuthError, _authenticate_dbt
from backend.app.core.config import get_settings
from backend.app.core.secrets import SecretNotFoundError
from backend.app.orchestration.dbt import DbtProvider

_KEY = "shared-dbt-hmac-signing-key"


def _load_snippet() -> Any:
    repo_root = Path(__file__).resolve().parents[3]
    path = repo_root / "integrations" / "dbt" / "dataq_dbt_callback.py"
    spec = importlib.util.spec_from_file_location("dataq_dbt_callback", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


snippet = _load_snippet()


class _FakeStore:
    def __init__(self, **data: str) -> None:
        self.data = dict(data)

    def get(self, name: str) -> str:
        if name not in self.data:
            raise SecretNotFoundError(name)
        return self.data[name]

    def set(self, name: str, value: str) -> None:
        self.data[name] = value


def _store(key: str = _KEY) -> _FakeStore:
    return _FakeStore(**{get_settings().dbt_webhook_secret_name: key})


def _run_results(*statuses: str) -> dict[str, Any]:
    return {
        "metadata": {
            "invocation_id": "inv-777",
            "invocation_started_at": "2026-07-05T10:31:04Z",
            "generated_at": "2026-07-05T10:31:14Z",
        },
        "results": [
            {"status": s, "unique_id": f"model.dataq_lineage.m{i}"} for i, s in enumerate(statuses)
        ],
    }


# ── producer/consumer agreement on the raw functions ──


def test_snippet_signature_is_accepted_by_the_receiver() -> None:
    body = snippet.build_payload(
        project_name="dataq_lineage",
        job_name="lineage_build",
        invocation_id="i",
        status="succeeded",
    )
    _authenticate_dbt(body, snippet.sign(_KEY, body), _store())  # must not raise


def test_snippet_payload_parses_into_expected_runupdate() -> None:
    body = snippet.build_payload(
        project_name="dataq_lineage",
        job_name="lineage_build",
        invocation_id="inv-777",
        status="succeeded",
        started_at="2026-07-05T10:31:04+00:00",
        finished_at="2026-07-05T10:31:14+00:00",
    )
    update = DbtProvider().parse_event(body, {})
    assert update.pipeline_or_dag_id == "lineage_build"
    assert update.resource_name == "dataq_lineage"
    assert update.provider_run_id == "inv-777"
    assert update.status == "succeeded"
    assert update.started_at is not None and update.finished_at is not None


def test_status_from_results_helper() -> None:
    assert snippet.status_from_results(_run_results("success", "pass")["results"]) == "succeeded"
    assert snippet.status_from_results(_run_results("success", "error")["results"]) == "failed"


def test_wrong_key_is_rejected() -> None:
    body = snippet.build_payload(
        project_name="p", job_name="j", invocation_id="i", status="succeeded"
    )
    with pytest.raises(WebhookAuthError):
        _authenticate_dbt(body, snippet.sign("a-different-key", body), _store())


# ── the callback entry point, end to end (reads a run_results.json) ──


def test_notify_is_safe_and_silent_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATAQ_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("DATAQ_WEBHOOK_SECRET", raising=False)
    posts: list[Any] = []
    monkeypatch.setattr(snippet, "_post", lambda *a, **k: posts.append(a))
    snippet.notify("nonexistent.json")  # must not raise
    assert posts == []


def test_callback_emits_a_receiver_acceptable_signed_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    results_path = tmp_path / "run_results.json"
    results_path.write_text(json.dumps(_run_results("success", "pass")))

    monkeypatch.setenv(
        "DATAQ_WEBHOOK_URL", "https://dataq.example.com/api/v1/orchestration/events/dbt"
    )
    monkeypatch.setenv("DATAQ_WEBHOOK_SECRET", _KEY)
    monkeypatch.setenv("DATAQ_DBT_JOB", "lineage_build")
    monkeypatch.setenv("DATAQ_DBT_PROJECT", "dataq_lineage")

    captured: dict[str, Any] = {}

    def fake_post(url: str, body: bytes, signature: str) -> int:
        captured["body"] = body
        captured["signature"] = signature
        return 200

    monkeypatch.setattr(snippet, "_post", fake_post)
    snippet.notify(str(results_path))

    # Exactly what the callback emitted is accepted + parsed by the real receiver.
    _authenticate_dbt(captured["body"], captured["signature"], _store())
    update = DbtProvider().parse_event(captured["body"], {})
    assert update.pipeline_or_dag_id == "lineage_build"
    assert update.resource_name == "dataq_lineage"
    assert update.provider_run_id == "inv-777"
    assert update.status == "succeeded"


def test_callback_derives_project_from_nodes_when_env_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    results_path = tmp_path / "run_results.json"
    results_path.write_text(json.dumps(_run_results("success")))
    monkeypatch.setenv("DATAQ_WEBHOOK_URL", "https://x/api/v1/orchestration/events/dbt")
    monkeypatch.setenv("DATAQ_WEBHOOK_SECRET", _KEY)
    monkeypatch.setenv("DATAQ_DBT_JOB", "lineage_build")
    monkeypatch.delenv("DATAQ_DBT_PROJECT", raising=False)

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        snippet, "_post", lambda url, body, sig: captured.setdefault("body", body) or 200
    )
    snippet.notify(str(results_path))
    # project parsed from node id "model.dataq_lineage.m0"
    assert DbtProvider().parse_event(captured["body"], {}).resource_name == "dataq_lineage"
