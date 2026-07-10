"""DbtProvider / DbtConfig / DbtConnectionAdapter unit tests.

Pure unit tests (no auth here — HMAC verification is the endpoint's job): callback
parse + status mapping, config validation per artifacts scheme, and the artifacts
poll (`list_recent_runs`) with `_read_artifact` patched (no cloud SDK needed).
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from backend.app.orchestration import dbt as dbt_mod
from backend.app.orchestration.base import MalformedEventError
from backend.app.orchestration.dbt import DbtConfig, DbtConnectionAdapter, DbtProvider

_CALLBACK = {
    "project_name": "dataq_lineage",
    "job_name": "lineage_build",
    "invocation_id": "522104cf-f67a-463f-bc5b-b6057cc93a62",
    "status": "success",
    "started_at": "2026-07-05T10:31:04+00:00",
    "finished_at": "2026-07-05T10:31:14+00:00",
}


def _payload(**overrides: Any) -> bytes:
    return json.dumps({**_CALLBACK, **overrides}).encode()


def _run_results(
    *statuses: str, invocation_id: str = "inv-1", generated_at: str | None = None
) -> bytes:
    return json.dumps(
        {
            "metadata": {
                "invocation_id": invocation_id,
                "invocation_started_at": "2026-07-05T10:31:04Z",
                "generated_at": generated_at or "2026-07-05T10:31:14Z",
            },
            "results": [
                {"status": s, "unique_id": f"model.dataq_lineage.m{i}"}
                for i, s in enumerate(statuses)
            ],
        }
    ).encode()


# ── identity + parse_event ────────────────────────────────────────────────────


def test_provider_identity() -> None:
    p = DbtProvider()
    assert p.provider == "dbt"
    assert p.resource_config_key == "project_name"


def test_parse_success_maps_fields() -> None:
    update = DbtProvider().parse_event(_payload(), {})
    assert update.provider_run_id == "522104cf-f67a-463f-bc5b-b6057cc93a62"
    assert update.pipeline_or_dag_id == "lineage_build"  # job = pipeline_or_dag_id
    assert update.resource_name == "dataq_lineage"  # project resolves the connection
    assert update.status == "succeeded"
    assert update.started_at == datetime.fromisoformat("2026-07-05T10:31:04+00:00")
    assert update.finished_at == datetime.fromisoformat("2026-07-05T10:31:14+00:00")


def test_parse_failed_carries_error() -> None:
    update = DbtProvider().parse_event(_payload(status="error", error="model X failed"), {})
    assert update.status == "failed"
    assert update.failure_reason == "model X failed"


@pytest.mark.parametrize(
    ("status", "expected"),
    [("success", "succeeded"), ("pass", "succeeded"), ("error", "failed"), ("fail", "failed")],
)
def test_status_mapping(status: str, expected: str) -> None:
    assert DbtProvider().parse_event(_payload(status=status), {}).status == expected


def test_parse_unknown_status_is_422() -> None:
    with pytest.raises(MalformedEventError):
        DbtProvider().parse_event(_payload(status="banana"), {})


@pytest.mark.parametrize("missing", ["project_name", "job_name", "invocation_id", "status"])
def test_parse_missing_required_field_is_422(missing: str) -> None:
    body = {k: v for k, v in _CALLBACK.items() if k != missing}
    with pytest.raises(MalformedEventError):
        DbtProvider().parse_event(json.dumps(body).encode(), {})


def test_parse_non_json_is_422() -> None:
    with pytest.raises(MalformedEventError):
        DbtProvider().parse_event(b"not json", {})


def test_parse_non_object_is_422() -> None:
    with pytest.raises(MalformedEventError):
        DbtProvider().parse_event(b"[1, 2, 3]", {})


def test_fetch_run_detail_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        DbtProvider().fetch_run_detail({}, "secret", "inv-1")


# ── DbtConfig validation ──────────────────────────────────────────────────────


def _cfg(**overrides: Any) -> dict[str, Any]:
    base = {
        "project_name": "dataq_lineage",
        "artifacts_uri": "adls://acct/raw/dbt",
        "jobs": ["lineage_build"],
    }
    return {**base, **overrides}


def test_config_adls_ok() -> None:
    assert DbtConfig.model_validate(_cfg()).artifacts_uri == "adls://acct/raw/dbt"


def test_config_file_ok() -> None:
    assert DbtConfig.model_validate(_cfg(artifacts_uri="file:///data/dbt")).jobs == [
        "lineage_build"
    ]


def test_config_s3_requires_access_key_and_region() -> None:
    with pytest.raises(ValueError, match="access_key_id and region"):
        DbtConfig.model_validate(_cfg(artifacts_uri="s3://bucket/dbt"))
    ok = DbtConfig.model_validate(
        _cfg(artifacts_uri="s3://bucket/dbt", access_key_id="AK", region="us-east-1")
    )
    assert ok.access_key_id == "AK"


def test_config_bad_scheme_rejected() -> None:
    with pytest.raises(ValueError, match="adls://, s3://, or file://"):
        DbtConfig.model_validate(_cfg(artifacts_uri="gopher://x/y"))


def test_config_empty_jobs_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        DbtConfig.model_validate(_cfg(jobs=[]))


def test_config_forbids_extra() -> None:
    with pytest.raises(ValueError):
        DbtConfig.model_validate(_cfg(surprise="x"))


# ── list_recent_runs (artifacts poll) ─────────────────────────────────────────

_SINCE = datetime(2026, 7, 5, 0, 0, tzinfo=UTC)


def test_poll_reads_run_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        dbt_mod,
        "_read_artifact",
        lambda cfg, job, secret: _run_results("success", "pass", invocation_id="inv-42"),
    )
    updates = DbtProvider().list_recent_runs(_cfg(), "secret", _SINCE)
    assert len(updates) == 1
    u = updates[0]
    assert u.provider_run_id == "inv-42"
    assert u.pipeline_or_dag_id == "lineage_build"
    assert u.resource_name == "dataq_lineage"
    assert u.status == "succeeded"


def test_poll_failure_status_from_failed_node(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        dbt_mod, "_read_artifact", lambda cfg, job, secret: _run_results("success", "fail")
    )
    assert DbtProvider().list_recent_runs(_cfg(), "secret", _SINCE)[0].status == "failed"


def test_poll_skips_missing_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dbt_mod, "_read_artifact", lambda cfg, job, secret: None)
    assert DbtProvider().list_recent_runs(_cfg(), "secret", _SINCE) == []


def test_poll_skips_older_than_since(monkeypatch: pytest.MonkeyPatch) -> None:
    old = (_SINCE - timedelta(days=1)).isoformat()
    monkeypatch.setattr(
        dbt_mod,
        "_read_artifact",
        lambda cfg, job, secret: _run_results("success", generated_at=old),
    )
    assert DbtProvider().list_recent_runs(_cfg(), "secret", _SINCE) == []


def test_poll_skips_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dbt_mod, "_read_artifact", lambda cfg, job, secret: b"{not json")
    assert DbtProvider().list_recent_runs(_cfg(), "secret", _SINCE) == []


def test_poll_iterates_all_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_read(cfg: DbtConfig, job: str, secret: str) -> bytes:
        seen.append(job)
        return _run_results("success", invocation_id=f"inv-{job}")

    monkeypatch.setattr(dbt_mod, "_read_artifact", fake_read)
    updates = DbtProvider().list_recent_runs(_cfg(jobs=["a", "b"]), "secret", _SINCE)
    assert seen == ["a", "b"]
    assert {u.pipeline_or_dag_id for u in updates} == {"a", "b"}


# ── adapter ───────────────────────────────────────────────────────────────────


def test_adapter_test_reads_first_job(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, Any] = {}

    def fake_read(cfg: DbtConfig, job: str, secret: str) -> bytes | None:
        called["job"] = job
        return None  # not-yet-published is still a green test

    monkeypatch.setattr(dbt_mod, "_read_artifact", fake_read)
    DbtConnectionAdapter().test(_cfg(jobs=["first", "second"]), "secret")
    assert called["job"] == "first"


# ── _read_artifact (the reader seam itself, per scheme) ───────────────────────


def test_read_artifact_file_scheme_round_trip(tmp_path: Any) -> None:
    # Real filesystem — no mock of the seam under test.
    latest = tmp_path / "lineage_build" / "latest"
    latest.mkdir(parents=True)
    (latest / "run_results.json").write_bytes(_run_results("success", "pass"))
    cfg = DbtConfig.model_validate(_cfg(artifacts_uri=f"file://{tmp_path}"))
    updates = DbtProvider().list_recent_runs(cfg.model_dump(), "", _SINCE)
    assert len(updates) == 1
    assert updates[0].status == "succeeded"


def test_read_artifact_file_missing_returns_none(tmp_path: Any) -> None:
    cfg = DbtConfig.model_validate(_cfg(artifacts_uri=f"file://{tmp_path}"))
    assert dbt_mod._read_artifact(cfg, "nope", "") is None


def test_read_artifact_adls_builds_path_and_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class _Downloaded:
        def readall(self) -> bytes:
            return _run_results("success")

    class _BlobClient:
        def download_blob(self, **_: Any) -> _Downloaded:
            return _Downloaded()

    class _Service:
        def __init__(self, account_url: str, credential: str, **_: Any) -> None:
            seen["account_url"] = account_url
            seen["credential"] = credential

        def get_blob_client(self, container: str, blob: str) -> _BlobClient:
            seen["container"] = container
            seen["blob"] = blob
            return _BlobClient()

    monkeypatch.setattr("azure.storage.blob.BlobServiceClient", _Service)
    cfg = DbtConfig.model_validate(_cfg(artifacts_uri="adls://acct/raw/dbt"))
    data = dbt_mod._read_artifact(cfg, "lineage_build", "sas-token")
    assert data is not None
    assert seen["account_url"] == "https://acct.blob.core.windows.net"
    assert seen["credential"] == "sas-token"
    assert seen["container"] == "raw"
    assert seen["blob"] == "dbt/lineage_build/latest/run_results.json"


def test_read_artifact_adls_missing_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from azure.core.exceptions import ResourceNotFoundError

    class _BlobClient:
        def download_blob(self, **_: Any) -> Any:
            raise ResourceNotFoundError("nope")

    class _Service:
        def __init__(self, account_url: str, credential: str, **_: Any) -> None:
            pass

        def get_blob_client(self, container: str, blob: str) -> _BlobClient:
            return _BlobClient()

    monkeypatch.setattr("azure.storage.blob.BlobServiceClient", _Service)
    cfg = DbtConfig.model_validate(_cfg(artifacts_uri="adls://acct/raw/dbt"))
    assert dbt_mod._read_artifact(cfg, "job", "sas") is None


def test_read_artifact_s3_builds_key_and_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class _Body:
        def read(self) -> bytes:
            return _run_results("success")

    class _S3:
        def get_object(self, **kw: str) -> dict[str, Any]:
            seen["bucket"] = kw["Bucket"]
            seen["key"] = kw["Key"]
            return {"Body": _Body()}

    monkeypatch.setattr("boto3.client", lambda *a, **k: _S3())
    cfg = DbtConfig.model_validate(
        _cfg(artifacts_uri="s3://bucket/dbt", access_key_id="AK", region="us-east-1")
    )
    data = dbt_mod._read_artifact(cfg, "lineage_build", "secret-key")
    assert data is not None
    assert seen["bucket"] == "bucket"
    assert seen["key"] == "dbt/lineage_build/latest/run_results.json"


def test_read_artifact_s3_missing_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from botocore.exceptions import ClientError

    class _S3:
        def get_object(self, **kw: str) -> dict[str, Any]:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")

    monkeypatch.setattr("boto3.client", lambda *a, **k: _S3())
    cfg = DbtConfig.model_validate(
        _cfg(artifacts_uri="s3://bucket/dbt", access_key_id="AK", region="us-east-1")
    )
    assert dbt_mod._read_artifact(cfg, "job", "secret") is None


# ── read_manifest (the optional lineage capability, #759) ─────────────────────


def test_read_artifact_relpath_selects_the_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class _Downloaded:
        def readall(self) -> bytes:
            return b"{}"

    class _BlobClient:
        def download_blob(self, **_: Any) -> _Downloaded:
            return _Downloaded()

    class _Service:
        def __init__(self, account_url: str, credential: str, **_: Any) -> None:
            pass

        def get_blob_client(self, container: str, blob: str) -> _BlobClient:
            seen["blob"] = blob
            return _BlobClient()

    monkeypatch.setattr("azure.storage.blob.BlobServiceClient", _Service)
    cfg = DbtConfig.model_validate(_cfg(artifacts_uri="adls://acct/raw/dbt"))
    dbt_mod._read_artifact(cfg, "lineage_build", "sas", dbt_mod._MANIFEST_RELPATH)
    assert seen["blob"] == "dbt/lineage_build/latest/manifest.json"


def test_read_manifest_file_scheme_round_trip(tmp_path: Any) -> None:
    latest = tmp_path / "lineage_build" / "latest"
    latest.mkdir(parents=True)
    (latest / "manifest.json").write_bytes(b'{"metadata": {}}')
    cfg = _cfg(artifacts_uri=f"file://{tmp_path}")
    raw = DbtProvider().read_manifest(cfg, "", "lineage_build")
    assert raw == b'{"metadata": {}}'


def test_read_manifest_missing_returns_none(tmp_path: Any) -> None:
    cfg = _cfg(artifacts_uri=f"file://{tmp_path}")
    assert DbtProvider().read_manifest(cfg, "", "nope") is None


def test_other_providers_have_no_read_manifest() -> None:
    # The refresh hook probes `read_manifest` via getattr — only dbt exposes it, so
    # ADF / Airflow stay lineage-free with no provider branching (CLAUDE.md §11).
    from backend.app.orchestration.airflow import AirflowProvider

    assert getattr(AirflowProvider(), "read_manifest", None) is None
    assert getattr(DbtProvider(), "read_manifest", None) is not None
