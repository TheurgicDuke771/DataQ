"""dbt orchestration provider (ADR 0029) — artifact-poll + HMAC callback.

dbt Core has no runs API. DataQ observes dbt builds through their **universal
surface** — the `run_results.json` artifact plus a post-build callback — so the
same contract works wherever dbt runs (dbt Cloud, dbt-on-Snowflake, Databricks dbt
tasks, local compose): neutrality by construction (ADR 0010/0013). This mirrors the
Airflow callback model (ADR 0007): a signed webhook is the near-real-time channel,
an artifacts poll is the 10-min fallback.

Orchestration provider, **not a datasource** (CLAUDE.md §4): this module implements
the `ConnectionAdapter` seam (config validation + connectivity test) and the
`OrchestrationProvider` seam (event parse + poll), never `CheckRunner`.

Grain (ADR 0029): the connection is a dbt **project** (one artifacts deployment,
resolved by ``project_name``); a **job** is the fine-grained trigger unit
(``pipeline_or_dag_id``), the analog of Airflow's instance→DAG. The poll reads
``<artifacts_uri>/<job>/latest/run_results.json`` per configured job.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from backend.app.orchestration.base import MalformedEventError, RunUpdate

# Fail fast rather than hang the request/beat thread on an unreachable store.
_READ_TIMEOUT_SECONDS = 10.0

# The stable per-job artifact pointer the producer (upload_artifacts.py) overwrites
# every build; `runs/<UTC-ts>/` copies are retained alongside for audit/#596.
_RUN_RESULTS_RELPATH = "latest/run_results.json"

# dbt node result statuses that mean the build failed (models: 'error';
# tests: 'fail'/'error'; any: 'runtime error'). Everything else — 'success',
# 'pass', 'skipped', 'warn' — is a non-failing outcome.
_DBT_FAILURE_STATUSES = frozenset({"error", "fail", "runtime error"})

# Overall-status words the callback may send (we own the snippet, but accept both
# dbt-native and DataQ-native spellings). Maps to PIPELINE_RUN_STATUSES.
_EVENT_STATUS_MAP = {
    "success": "succeeded",
    "succeeded": "succeeded",
    "pass": "succeeded",  # nosec B105 — dbt result status, not a password
    "error": "failed",
    "fail": "failed",
    "failed": "failed",
}


class DbtConfig(BaseModel):
    """Non-secret dbt orchestration-connection config (credential comes from secrets).

    Maps from ``Connection.config``. ``project_name`` resolves a run to this
    connection (``resource_config_key``). ``artifacts_uri`` is the base location of
    the dbt artifacts — ``adls://<account>/<container>/<prefix>``,
    ``s3://<bucket>/<prefix>``, or ``file:///<path>``; the poll reads
    ``<artifacts_uri>/<job>/latest/run_results.json`` for each name in ``jobs``.

    The per-connection secret is the artifacts-store read credential (ADLS SAS / S3
    secret key / unused for local). ``access_key_id``/``region`` are the non-secret
    S3 halves (required only for ``s3://``). The HMAC webhook signing key is a
    separate app-level secret (``settings.dbt_webhook_secret_name``), not here.
    """

    model_config = ConfigDict(extra="forbid")

    project_name: str
    artifacts_uri: str
    jobs: list[str]
    # S3-only (non-secret half of the credential).
    region: str | None = None
    access_key_id: str | None = None

    @field_validator("artifacts_uri")
    @classmethod
    def _known_scheme(cls, value: str) -> str:
        scheme = urlparse(value).scheme
        if scheme not in ("adls", "s3", "file"):
            raise ValueError("artifacts_uri must start with adls://, s3://, or file://")
        return value.rstrip("/")

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


def _read_artifact(config: DbtConfig, job: str, secret: str) -> bytes | None:
    """Read ``<artifacts_uri>/<job>/latest/run_results.json``; None if absent.

    Dispatches on the ``artifacts_uri`` scheme. Cloud SDKs are imported lazily (per
    ``core/secrets.py``) so the module — and its unit tests, which patch this
    function — never require azure/boto3. Transport/auth errors propagate (the poll
    task fails soft per connection).
    """
    parsed = urlparse(config.artifacts_uri)
    scheme = parsed.scheme

    if scheme == "file":
        from pathlib import Path

        path = Path(parsed.path) / job / _RUN_RESULTS_RELPATH
        return path.read_bytes() if path.exists() else None

    if scheme == "adls":
        from azure.core.exceptions import ResourceNotFoundError
        from azure.storage.blob import BlobServiceClient

        account = parsed.netloc
        container, _, prefix = parsed.path.lstrip("/").partition("/")
        blob = (
            f"{prefix}/{job}/{_RUN_RESULTS_RELPATH}" if prefix else f"{job}/{_RUN_RESULTS_RELPATH}"
        )
        # Bound socket connect/read like the ADLS datasource adapter — `test()` runs
        # this synchronously in the request thread, so an unreachable account must
        # fail fast, not hang. (`download_blob(timeout=)` is only the server-side op
        # timeout, so set the client-level socket timeouts too.)
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
    key = f"{prefix}/{job}/{_RUN_RESULTS_RELPATH}" if prefix else f"{job}/{_RUN_RESULTS_RELPATH}"
    client = boto3.client(
        "s3",
        region_name=config.region,
        aws_access_key_id=config.access_key_id,
        aws_secret_access_key=secret,
        # Bound connect/read like the S3 datasource adapter — `test()` runs this in
        # the request thread; boto3's ~60s defaults would hang on a blackholed host.
        config=Config(
            connect_timeout=int(_READ_TIMEOUT_SECONDS), read_timeout=int(_READ_TIMEOUT_SECONDS)
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

    def validate_config(self, raw: dict[str, Any]) -> DbtConfig:
        return DbtConfig.model_validate(raw)

    def test(self, raw: dict[str, Any], secret: str) -> None:
        """Read the first job's `latest/run_results.json`; raise on any failure.

        A green test means the artifacts store is reachable, the credential
        authenticates, and the first configured job has published a build. A
        not-yet-published job (None) is still a green test — the store and
        credential are proven; the run simply hasn't happened yet.
        """
        config = self.validate_config(raw)
        _read_artifact(config, config.jobs[0], secret)


class DbtProvider:
    """`OrchestrationProvider` for dbt — signed-callback parse + artifacts poll.

    `parse_event` consumes the JSON our `integrations/dbt/` callback POSTs (we own
    the shape): ``project_name``, ``job_name``, ``invocation_id``, ``status``
    (+ optional ``started_at`` / ``finished_at`` / ``error``). The callback is
    HMAC-authenticated over the raw body (ADR 0007/0029) and authoritative, so
    there is no REST enrichment — `fetch_run_detail` is intentionally unimplemented.
    ``invocation_id`` is the `pipeline_runs` idempotency key; it (with
    ``project_name`` / ``job_name`` / ``status``) is required.

    Both success and failure arrive on this channel; a ``succeeded`` run fires
    `trigger_bindings`. `list_recent_runs` is the poll fallback for projects that
    don't POST the callback — it reads each job's `run_results.json`.
    """

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

    def list_recent_runs(
        self, config: Mapping[str, Any], secret: str, since: datetime
    ) -> list[RunUpdate]:
        """Poll each configured job's `latest/run_results.json`, newest-only.

        Reads `<artifacts_uri>/<job>/latest/run_results.json` per job; emits a
        `RunUpdate` when `metadata.generated_at >= since`. Trigger-on-success is
        enforced downstream (`ingest_polled_runs`); the `(provider, invocation_id)`
        upsert makes re-reading the stable `latest/` pointer idempotent. A missing
        artifact (job not yet built) is skipped; a malformed one is skipped;
        transport/auth errors raise (the polling task fails soft per connection).
        """
        cfg = DbtConfig.model_validate(dict(config))
        updates: list[RunUpdate] = []
        for job in cfg.jobs:
            raw = _read_artifact(cfg, job, secret)
            if raw is None:
                continue
            try:
                doc = json.loads(raw)
                metadata = doc["metadata"]
                invocation_id = metadata["invocation_id"]
                results = doc.get("results", [])
            except (ValueError, TypeError, KeyError):
                continue
            finished_at = _parse_dt(metadata.get("generated_at"))
            # `since` is always aware (UTC); only compare when generated_at parsed to
            # an aware datetime too — a tz-naive one would TypeError and fail-soft the
            # WHOLE connection poll (dropping every job), so include it rather than skip.
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
