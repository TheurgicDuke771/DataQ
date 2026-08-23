"""dbt orchestration provider (ADR 0029) — artifact-poll + HMAC callback."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any, ClassVar
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from backend.app.core.artifacts import ArtifactTooLargeError, load_json_artifact
from backend.app.core.credential_expiry import azure_sas_expiry
from backend.app.core.logging import get_logger
from backend.app.core.s3_endpoint import (
    S3AddressingStyle,
    addressing_config_kwargs,
    normalize_addressing_style,
    normalize_endpoint_url,
)
from backend.app.orchestration.base import MalformedEventError, RunUpdate

log = get_logger(__name__)

# Fail fast rather than hang the request/beat thread on an unreachable store.
_READ_TIMEOUT_SECONDS = 10.0

# The stable per-job artifact pointers the producer (upload_artifacts.py) overwrites
# every build; `runs/<UTC-ts>/` copies are retained alongside for audit/#596.
_RUN_RESULTS_RELPATH = "latest/run_results.json"
# The sibling model dependency graph, read for lineage (ADR 0034 slice 2, #759).
_MANIFEST_RELPATH = "latest/manifest.json"

# dbt node result statuses that mean the build failed (models: 'error'; tests: 'fail'/'error'; any:
# 'runtime error').
_DBT_FAILURE_STATUSES = frozenset({"error", "fail", "runtime error"})

# Overall-status words the callback may send (we own the snippet, but accept both
# dbt-native and DataQ-native spellings). Maps to PIPELINE_RUN_STATUSES.
_EVENT_STATUS_MAP = {
    "success": "succeeded",
    "succeeded": "succeeded",
    # "pass" here is a dbt result status, not a password.
    "pass": "succeeded",  # nosec B105
    "error": "failed",
    "fail": "failed",
    "failed": "failed",
}


class DbtConfig(BaseModel):
    """Non-secret dbt orchestration-connection config (credential comes from secrets)."""

    model_config = ConfigDict(extra="forbid")

    project_name: str
    artifacts_uri: str
    jobs: list[str]
    # S3-only (non-secret half of the credential).
    region: str | None = None
    access_key_id: str | None = None
    endpoint_url: str | None = None
    addressing_style: S3AddressingStyle = "auto"
    # Optional operator override for the lineage anchor namespace (ADR 0034, #759): dbt's manifest
    # has no namespace, so DataQ normally infers it from existing assets.
    lineage_namespace: str | None = None

    @field_validator("artifacts_uri")
    @classmethod
    def _known_scheme(cls, value: str) -> str:
        scheme = urlparse(value).scheme
        if scheme not in ("adls", "s3", "file"):
            raise ValueError("artifacts_uri must start with adls://, s3://, or file://")
        return value.rstrip("/")

    @field_validator("endpoint_url")
    @classmethod
    def _endpoint(cls, value: str | None) -> str | None:
        return normalize_endpoint_url(value)

    @field_validator("addressing_style", mode="before")
    @classmethod
    def _style(cls, value: Any) -> Any:
        return normalize_addressing_style(value)

    @field_validator("lineage_namespace")
    @classmethod
    def _non_empty_namespace(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("lineage_namespace, when set, must be a non-empty string")
        return value.strip() if value is not None else None

    @field_validator("jobs")
    @classmethod
    def _non_empty_jobs(cls, value: list[str]) -> list[str]:
        if not value or any(not j for j in value):
            raise ValueError("jobs must be a non-empty list of non-empty job names")
        return value

    @model_validator(mode="after")
    def _s3_needs_access_key(self) -> DbtConfig:
        if urlparse(self.artifacts_uri).scheme == "s3" and not (self.access_key_id and self.region):
            raise ValueError("s3:// artifacts_uri requires access_key_id and region")
        return self


def _read_artifact(
    config: DbtConfig, job: str, secret: str | None, relpath: str = _RUN_RESULTS_RELPATH
) -> bytes | None:
    """Read ``<artifacts_uri>/<job>/<relpath>``; None if absent."""
    parsed = urlparse(config.artifacts_uri)
    scheme = parsed.scheme

    if scheme == "file":
        from pathlib import Path

        path = Path(parsed.path) / job / relpath
        return path.read_bytes() if path.exists() else None

    if scheme == "adls":
        from azure.core.exceptions import ResourceNotFoundError
        from azure.storage.blob import BlobServiceClient

        account = parsed.netloc
        container, _, prefix = parsed.path.lstrip("/").partition("/")
        blob = f"{prefix}/{job}/{relpath}" if prefix else f"{job}/{relpath}"
        # Bound socket connect/read like the ADLS datasource adapter — `test()` runs this
        # synchronously in the request thread, so an unreachable account must fail fast, not hang.
        client = BlobServiceClient(
            account_url=f"https://{account}.blob.core.windows.net",
            credential=secret,
            connection_timeout=int(_READ_TIMEOUT_SECONDS),
            read_timeout=int(_READ_TIMEOUT_SECONDS),
        )
        try:
            blob_bytes: bytes = (
                client.get_blob_client(container, blob)
                .download_blob(timeout=int(_READ_TIMEOUT_SECONDS))
                .readall()
            )
            return blob_bytes
        except ResourceNotFoundError:
            return None

    # s3
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError

    bucket = parsed.netloc
    prefix = parsed.path.lstrip("/")
    key = f"{prefix}/{job}/{relpath}" if prefix else f"{job}/{relpath}"
    client = boto3.client(
        "s3",
        region_name=config.region,
        aws_access_key_id=config.access_key_id,
        aws_secret_access_key=secret,
        # S3-compatible store when set, AWS when None (#1063) — same resolution as the datasource
        # adapter.
        endpoint_url=config.endpoint_url,
        # Bound connect/read like the S3 datasource adapter — `test()` runs this in
        # the request thread; boto3's ~60s defaults would hang on a blackholed host.
        config=Config(
            connect_timeout=int(_READ_TIMEOUT_SECONDS),
            read_timeout=int(_READ_TIMEOUT_SECONDS),
            **addressing_config_kwargs(config.endpoint_url, config.addressing_style),
        ),
    )
    try:
        data: bytes = client.get_object(Bucket=bucket, Key=key)["Body"].read()
        return data
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return None
        raise


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _status_from_results(results: list[dict[str, Any]]) -> str:
    """Overall run status: failed if any node failed, else succeeded (ADR 0029)."""
    for node in results:
        if str(node.get("status", "")).lower() in _DBT_FAILURE_STATUSES:
            return "failed"
    return "succeeded"


class DbtConnectionAdapter:
    """`ConnectionAdapter` for dbt — config validation + an artifacts-read probe."""

    # #1401: `artifacts_uri` is the store the credential reads from, and
    # `endpoint_url` (#1063) overrides the S3 host it is signed against.
    destination_fields: ClassVar[dict[str, tuple[str, ...]]] = {
        "secret": ("artifacts_uri", "endpoint_url")
    }

    # A local `file://` artifacts path needs no credential (the class docstring above) — mirrored by
    # the frontend's `optionalSecret: true` for `dbt` in `connectionFormSpec.ts`.
    secret_optional = True

    def validate_config(self, raw: dict[str, Any]) -> DbtConfig:
        return DbtConfig.model_validate(raw)

    def credential_expiry(self, raw: dict[str, Any], secret: str, **_: Any) -> datetime | None:
        """When the artifacts-store credential stops working (#838), or ``None``."""
        if urlparse(self.validate_config(raw).artifacts_uri).scheme != "adls":
            return None
        return azure_sas_expiry(secret)

    def test(self, raw: dict[str, Any], secret: str | None, **_: Any) -> None:
        """Read the first job's `latest/run_results.json`; raise on any failure."""
        config = self.validate_config(raw)
        _read_artifact(config, config.jobs[0], secret)


class DbtProvider:
    """`OrchestrationProvider` for dbt — signed-callback parse + artifacts poll."""

    provider = "dbt"
    resource_config_key = "project_name"

    def parse_event(self, payload: bytes, headers: Mapping[str, str]) -> RunUpdate:
        try:
            body = json.loads(payload)
        except (ValueError, TypeError) as exc:
            raise MalformedEventError("event body is not valid JSON") from exc
        if not isinstance(body, dict):
            raise MalformedEventError("event body must be a JSON object")

        project_name = body.get("project_name")
        job_name = body.get("job_name")
        invocation_id = body.get("invocation_id")
        raw_status = body.get("status")
        missing = [
            name
            for name, value in (
                ("project_name", project_name),
                ("job_name", job_name),
                ("invocation_id", invocation_id),
                ("status", raw_status),
            )
            if not value
        ]
        if missing:
            raise MalformedEventError(
                "event missing required field(s)", detail={"missing": missing}
            )

        status = _EVENT_STATUS_MAP.get(str(raw_status).lower())
        if status is None:
            raise MalformedEventError("unrecognised dbt run status", detail={"status": raw_status})

        return RunUpdate(
            provider_run_id=str(invocation_id),
            pipeline_or_dag_id=str(job_name),
            resource_name=str(project_name),
            status=status,
            started_at=_parse_dt(body.get("started_at")),
            finished_at=_parse_dt(body.get("finished_at")),
            failure_reason=str(body["error"]) if status == "failed" and body.get("error") else None,
        )

    def fetch_run_detail(
        self, config: Mapping[str, Any], secret: str, provider_run_id: str
    ) -> RunUpdate:
        # The signed callback / artifact is authoritative; nothing to enrich. The
        # persistence layer treats NotImplementedError as "skip enrichment".
        raise NotImplementedError("dbt artifacts are authoritative; no REST enrichment")

    def read_manifest(self, config: dict[str, Any], secret: str, job: str) -> bytes | None:
        """Read a job's ``latest/manifest.json`` for lineage (ADR 0034, #759)."""
        cfg = DbtConfig.model_validate(dict(config))
        return _read_artifact(cfg, job, secret, _MANIFEST_RELPATH)

    def list_recent_runs(
        self, config: Mapping[str, Any], secret: str, since: datetime
    ) -> list[RunUpdate]:
        """Poll each configured job's `latest/run_results.json`, newest-only."""
        cfg = DbtConfig.model_validate(dict(config))
        updates: list[RunUpdate] = []
        for job in cfg.jobs:
            raw = _read_artifact(cfg, job, secret)
            if raw is None:
                continue
            try:
                # Shared capped+logged loader — the poll path must not `json.loads`
                # an unbounded artifact any more than the manifest parser does (#759).
                doc = load_json_artifact(raw, context=f"dbt run_results job={job}")
                metadata = doc["metadata"]
                invocation_id = metadata["invocation_id"]
                results = doc.get("results", [])
            except (ArtifactTooLargeError, ValueError, TypeError, KeyError, UnicodeDecodeError):
                # A malformed / oversized artifact for one job skips that job — but
                # now log it (the review: the silent `continue` hid a bad payload).
                log.warning("dbt_run_results_skipped", job=job, project=cfg.project_name)
                continue
            finished_at = _parse_dt(metadata.get("generated_at"))
            # `since` is always aware (UTC); only compare when generated_at parsed to an aware
            # datetime too.
            if finished_at is not None and finished_at.tzinfo is not None and finished_at < since:
                continue
            updates.append(
                RunUpdate(
                    provider_run_id=str(invocation_id),
                    pipeline_or_dag_id=job,
                    resource_name=cfg.project_name,
                    status=_status_from_results(results),
                    started_at=_parse_dt(metadata.get("invocation_started_at")),
                    finished_at=finished_at,
                )
            )
        return updates
