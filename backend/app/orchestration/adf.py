"""Azure Data Factory connection adapter (orchestration provider, not a datasource).

ADF is an orchestration provider (CLAUDE.md §4): DataQ monitors its pipeline
runs and triggers suites on success — it is never a queryable datasource, so
this module implements only the `ConnectionAdapter` seam (config validation +
connectivity test), never `CheckRunner`.

The connection identifies one data factory (subscription / resource group /
factory) and authenticates with an Azure AD service principal — `client_id` in
config, the SP `client_secret` in the SecretStore. ``test`` proves both: it
acquires a service-principal token (OAuth2 client-credentials) and GETs the
factory through the ARM REST API, so a green test means the credentials are
valid *and* the named factory is reachable. It uses ``httpx`` only — no Azure
SDK dependency — and, like the Snowflake adapter, runs live but fails-soft
pending real credentials (the connection-service test path wraps and never
echoes the adapter exception, so tokens/secrets can't leak to the client).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from backend.app.orchestration.base import MalformedEventError, RunUpdate

# Azure AD OAuth2 endpoint + ARM management host. The client-credentials grant
# against this scope yields a bearer token usable for the factory GET below.
_AAD_OAUTH_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
_ARM_SCOPE = "https://management.azure.com/.default"
_ARM_FACTORY_URL = (
    "https://management.azure.com/subscriptions/{subscription_id}"
    "/resourceGroups/{resource_group}"
    "/providers/Microsoft.DataFactory/factories/{factory_name}"
)
# Single pipeline-run detail (GET): authoritative status + runStart/runEnd + message.
_ARM_PIPELINE_RUN_URL = _ARM_FACTORY_URL + "/pipelineruns/{run_id}"
# Query recent runs (POST): the polling-fallback list endpoint, filtered by a
# lastUpdatedAfter/Before window and a Status==Succeeded filter.
_ARM_QUERY_RUNS_URL = _ARM_FACTORY_URL + "/queryPipelineRuns"
_ARM_API_VERSION = "2018-06-01"

# Fail fast rather than hang the request thread on an unreachable endpoint.
_TEST_TIMEOUT_SECONDS = 10.0


class ADFConfig(BaseModel):
    """Non-secret ADF connection config (the SP client secret comes from secrets).

    Maps from ``Connection.config``. Identifies one data factory plus the service
    principal's non-secret half (`tenant_id` / `client_id`); the `client_secret`
    is resolved from the SecretStore at test time and never stored here.
    """

    model_config = ConfigDict(extra="forbid")

    subscription_id: str
    resource_group: str
    factory_name: str
    tenant_id: str
    client_id: str


def _acquire_token(config: ADFConfig, client_secret: str) -> str:
    """OAuth2 client-credentials token for the ARM management scope."""
    response = httpx.post(
        _AAD_OAUTH_URL.format(tenant_id=config.tenant_id),
        data={
            "grant_type": "client_credentials",
            "client_id": config.client_id,
            "client_secret": client_secret,
            "scope": _ARM_SCOPE,
        },
        timeout=_TEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    token = response.json().get("access_token")
    if not token:
        raise ValueError("Azure AD token response contained no access_token")
    return str(token)


class ADFConnectionAdapter:
    """`ConnectionAdapter` for Azure Data Factory — config validation + live test."""

    def validate_config(self, raw: dict[str, Any]) -> ADFConfig:
        return ADFConfig.model_validate(raw)

    def test(self, raw: dict[str, Any], secret: str) -> None:
        """Acquire an SP token and GET the factory; raise on any failure.

        ``secret`` is the service-principal client secret. A successful return
        means the SP authenticated AND the named factory is reachable.
        """
        config = self.validate_config(raw)
        token = _acquire_token(config, secret)
        response = httpx.get(
            _ARM_FACTORY_URL.format(
                subscription_id=config.subscription_id,
                resource_group=config.resource_group,
                factory_name=config.factory_name,
            ),
            params={"api-version": _ARM_API_VERSION},
            headers={"Authorization": f"Bearer {token}"},
            timeout=_TEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()


# ── Webhook receiver (OrchestrationProvider) ─────────────────────────────────

# ADF RunStatus (and Azure Monitor monitorCondition) → DataQ PIPELINE_RUN_STATUSES.
# Keys are lower-cased before lookup so casing differences across Azure payloads
# don't matter. "fired" is the Common-Alert-Schema monitorCondition for a
# failed-pipeline-runs alert — the v1 webhook is the failure channel (ADR 0004).
_ADF_STATUS_MAP = {
    "succeeded": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "inprogress": "running",
    "in_progress": "running",
    "queued": "queued",
    "fired": "failed",
    "resolved": "succeeded",
}


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        # Azure emits ISO-8601 with a trailing 'Z'; fromisoformat wants +00:00.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class AdfProvider:
    """`OrchestrationProvider` for Azure Data Factory — webhook parse (v1).

    `parse_event` consumes the JSON the Azure Monitor alert delivers, projected
    to the fields DataQ needs (roadmap Week-2 task): ``factoryName``,
    ``pipelineName``, ``runId``, ``status``, ``firedDateTime``. ``runId`` is the
    idempotency key for the `pipeline_runs` upsert, so it is required; a missing
    factory / pipeline / runId is a `MalformedEventError` (422). ``status``
    defaults to ``failed`` when absent because the v1 ADF webhook is the failure
    channel (success → trigger arrives via the REST polling path, ADR 0004).

    NOTE: the exact Azure Monitor Common-Alert-Schema → these-fields mapping is
    validated at the Week-7 deploy smoke test (we cannot exercise it against live
    Azure before deployment). `fetch_run_detail` / `list_recent_runs` (REST
    enrichment + polling fallback) land in the follow-up PR.
    """

    provider = "adf"
    resource_config_key = "factory_name"

    def parse_event(self, payload: bytes, headers: Mapping[str, str]) -> RunUpdate:
        try:
            body = json.loads(payload)
        except (ValueError, TypeError) as exc:
            raise MalformedEventError("event body is not valid JSON") from exc
        if not isinstance(body, dict):
            raise MalformedEventError("event body must be a JSON object")

        factory = body.get("factoryName")
        pipeline = body.get("pipelineName")
        run_id = body.get("runId")
        missing = [
            name
            for name, value in (
                ("factoryName", factory),
                ("pipelineName", pipeline),
                ("runId", run_id),
            )
            if not value
        ]
        if missing:
            raise MalformedEventError(
                "event missing required field(s)", detail={"missing": missing}
            )

        raw_status = body.get("status") or "failed"
        status = _ADF_STATUS_MAP.get(str(raw_status).lower())
        if status is None:
            raise MalformedEventError("unrecognised ADF run status", detail={"status": raw_status})

        return RunUpdate(
            provider_run_id=str(run_id),
            pipeline_or_dag_id=str(pipeline),
            resource_name=str(factory),
            status=status,
            started_at=_parse_dt(body.get("start")),
            finished_at=_parse_dt(body.get("end") or body.get("firedDateTime")),
            failure_reason=body.get("message"),
        )

    def fetch_run_detail(
        self, config: Mapping[str, Any], secret: str, provider_run_id: str
    ) -> RunUpdate:
        """Authoritative ARM REST lookup of one pipeline run → `RunUpdate`.

        Used to enrich a thin webhook alert with the real status / timing /
        message. Reuses the service-principal token flow; raises (httpx errors,
        validation) on failure — the caller (`orchestration_service`) decides
        whether to fail soft back to the parsed event.
        """
        cfg = ADFConfig.model_validate(dict(config))
        token = _acquire_token(cfg, secret)
        response = httpx.get(
            _ARM_PIPELINE_RUN_URL.format(
                subscription_id=cfg.subscription_id,
                resource_group=cfg.resource_group,
                factory_name=cfg.factory_name,
                run_id=provider_run_id,
            ),
            params={"api-version": _ARM_API_VERSION},
            headers={"Authorization": f"Bearer {token}"},
            timeout=_TEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()

        raw_status = data.get("status")
        status = _ADF_STATUS_MAP.get(str(raw_status).lower()) if raw_status else None
        if status is None:
            raise ValueError(f"ADF run detail has unrecognised status {raw_status!r}")

        return RunUpdate(
            provider_run_id=str(data.get("runId") or provider_run_id),
            pipeline_or_dag_id=str(data["pipelineName"]),
            resource_name=cfg.factory_name,
            status=status,
            started_at=_parse_dt(data.get("runStart")),
            finished_at=_parse_dt(data.get("runEnd")),
            failure_reason=data.get("message"),
        )

    def list_recent_runs(
        self, config: Mapping[str, Any], secret: str, since: datetime
    ) -> list[RunUpdate]:
        """Poll the factory's recent **succeeded** runs via ARM queryPipelineRuns.

        POSTs a ``lastUpdatedAfter=since`` / ``lastUpdatedBefore=now`` window with
        a ``Status==Succeeded`` filter (polling is the trigger-on-success channel,
        ADR 0004 — failures arrive on the webhook), mapping each returned run to a
        `RunUpdate`. Malformed rows are skipped rather than failing the whole poll;
        transport/auth errors raise (the polling task fails soft per connection).
        """
        cfg = ADFConfig.model_validate(dict(config))
        token = _acquire_token(cfg, secret)
        response = httpx.post(
            _ARM_QUERY_RUNS_URL.format(
                subscription_id=cfg.subscription_id,
                resource_group=cfg.resource_group,
                factory_name=cfg.factory_name,
            ),
            params={"api-version": _ARM_API_VERSION},
            headers={"Authorization": f"Bearer {token}"},
            json={
                "lastUpdatedAfter": since.isoformat(),
                "lastUpdatedBefore": datetime.now(UTC).isoformat(),
                "filters": [{"operand": "Status", "operator": "Equals", "values": ["Succeeded"]}],
            },
            timeout=_TEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

        updates: list[RunUpdate] = []
        for item in response.json().get("value", []):
            raw_status = item.get("status")
            status = _ADF_STATUS_MAP.get(str(raw_status).lower()) if raw_status else None
            run_id = item.get("runId")
            pipeline = item.get("pipelineName")
            if status is None or not run_id or not pipeline:
                continue
            updates.append(
                RunUpdate(
                    provider_run_id=str(run_id),
                    pipeline_or_dag_id=str(pipeline),
                    resource_name=cfg.factory_name,
                    status=status,
                    started_at=_parse_dt(item.get("runStart")),
                    finished_at=_parse_dt(item.get("runEnd")),
                    failure_reason=item.get("message"),
                )
            )
        return updates
