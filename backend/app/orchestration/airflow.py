"""Apache Airflow connection adapter (orchestration provider, not a datasource).

Airflow is an orchestration provider (CLAUDE.md §4): DataQ observes its DAG runs
and triggers suites on success — never a queryable datasource, so this module
implements only the `ConnectionAdapter` seam (config validation + connectivity
test), never `CheckRunner`.

The connection points at an Airflow **webserver REST API** (the polling-fallback
channel from [ADR 0007](../../docs/adr/0007-airflow-callback-model.md): the
`dagRuns` endpoint backfills runs for DAGs that don't adopt the HMAC callback
snippet). It is distinct from the webhook signing key — that HMAC secret lives
in Key Vault and is consumed by the (separate) Airflow event receiver.

Auth is **token-based by default** (a Bearer token in the SecretStore), with HTTP
basic as an option (username in config, password in the SecretStore). ``test``
probes `GET /api/v1/dags?limit=1` — the lightest authenticated stable-REST call —
so a green test means the webserver is reachable, the REST API is enabled, and
the credential authenticates. Like the other adapters it runs live but fails-soft
pending real credentials; the connection-service test path wraps and never echoes
the adapter exception, so tokens can't leak to the client.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from backend.app.orchestration.base import MalformedEventError, RunUpdate

# Lightest authenticated stable-REST call — proves reachability + auth + that the
# REST API is enabled, without listing or mutating anything.
_DAGS_PROBE_PATH = "/api/v1/dags"
_DAGS_PROBE_PARAMS = {"limit": 1}

# Batch dagRuns list across all DAGs (`~`) — the polling-fallback endpoint.
_DAGRUNS_LIST_PATH = "/api/v1/dags/~/dagRuns/list"
_DAGRUNS_PAGE_LIMIT = 100

# Fail fast rather than hang the request thread on an unreachable webserver.
_TEST_TIMEOUT_SECONDS = 10.0


class AirflowConfig(BaseModel):
    """Non-secret Airflow REST connection config (the credential comes from secrets).

    Maps from ``Connection.config``. ``base_url`` is the webserver root (e.g.
    ``https://airflow.example.com``); the credential is a Bearer token
    (``auth_type='token'``, v1 default) or the password for ``username``
    (``auth_type='basic'``), resolved from the SecretStore at test time.
    """

    model_config = ConfigDict(extra="forbid")

    base_url: str
    auth_type: Literal["token", "basic"] = "token"
    username: str | None = None

    @field_validator("base_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return value.rstrip("/")

    @model_validator(mode="after")
    def _basic_needs_username(self) -> AirflowConfig:
        if self.auth_type == "basic" and not self.username:
            raise ValueError("username is required when auth_type is 'basic'")
        return self


def _auth(config: AirflowConfig, secret: str) -> tuple[dict[str, str], httpx.Auth | None]:
    """(headers, httpx auth) for a request — Bearer token or HTTP basic."""
    if config.auth_type == "token":
        return {"Authorization": f"Bearer {secret}"}, None
    # basic — username guaranteed present by the model validator
    return {}, httpx.BasicAuth(str(config.username), secret)


class AirflowConnectionAdapter:
    """`ConnectionAdapter` for Apache Airflow — config validation + a REST probe."""

    def validate_config(self, raw: dict[str, Any]) -> AirflowConfig:
        return AirflowConfig.model_validate(raw)

    def test(self, raw: dict[str, Any], secret: str) -> None:
        """GET the DAGs list (limit 1) with the credential; raise on any failure.

        ``secret`` is the Bearer token (token auth) or the password (basic auth,
        paired with ``config.username``).
        """
        config = self.validate_config(raw)
        headers, auth = _auth(config, secret)
        response = httpx.get(
            f"{config.base_url}{_DAGS_PROBE_PATH}",
            params=_DAGS_PROBE_PARAMS,
            headers=headers,
            auth=auth,
            timeout=_TEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()


# Airflow DagRun state → DataQ `PIPELINE_RUN_STATUSES`.
_AIRFLOW_STATE_MAP = {
    "success": "succeeded",
    "failed": "failed",
    "running": "running",
    "queued": "queued",
}


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class AirflowProvider:
    """`OrchestrationProvider` for Apache Airflow — signed-callback parse (v1).

    `parse_event` consumes the JSON our `on_*_callback` snippet POSTs (we author
    it, so we own the shape): ``dag_id``, ``run_id``, ``state``, ``base_url``
    (+ optional ``start_date`` / ``end_date`` / ``error``). The callback is
    already authenticated (HMAC over the raw body, ADR 0007) and authoritative,
    so there is **no REST enrichment** — `fetch_run_detail` is intentionally
    unimplemented and the persistence layer skips enrichment for this provider.
    ``run_id`` is the idempotency key for the `pipeline_runs` upsert, so it (with
    ``dag_id`` / ``state`` / ``base_url``) is required; absence is a
    `MalformedEventError` (422).

    Unlike ADF, both success and failure arrive on this channel (the snippet sets
    both `on_success_callback` and `on_failure_callback`); a ``success`` run is
    what fires `trigger_bindings`. The `dagRuns` REST polling fallback
    (`list_recent_runs`) for DAGs that don't adopt the snippet lands in Week 5.
    """

    provider = "airflow"
    resource_config_key = "base_url"

    def parse_event(self, payload: bytes, headers: Mapping[str, str]) -> RunUpdate:
        try:
            body = json.loads(payload)
        except (ValueError, TypeError) as exc:
            raise MalformedEventError("event body is not valid JSON") from exc
        if not isinstance(body, dict):
            raise MalformedEventError("event body must be a JSON object")

        dag_id = body.get("dag_id")
        run_id = body.get("run_id")
        base_url = body.get("base_url")
        raw_state = body.get("state")
        missing = [
            name
            for name, value in (
                ("dag_id", dag_id),
                ("run_id", run_id),
                ("state", raw_state),
                ("base_url", base_url),
            )
            if not value
        ]
        if missing:
            raise MalformedEventError(
                "event missing required field(s)", detail={"missing": missing}
            )

        status = _AIRFLOW_STATE_MAP.get(str(raw_state).lower())
        if status is None:
            raise MalformedEventError("unrecognised Airflow run state", detail={"state": raw_state})

        return RunUpdate(
            provider_run_id=str(run_id),
            pipeline_or_dag_id=str(dag_id),
            # match the connection's normalised base_url (AirflowConfig rstrips it)
            resource_name=str(base_url).rstrip("/"),
            status=status,
            started_at=_parse_dt(body.get("start_date")),
            finished_at=_parse_dt(body.get("end_date")),
            failure_reason=str(body["error"]) if status == "failed" and body.get("error") else None,
        )

    def fetch_run_detail(
        self, config: Mapping[str, Any], secret: str, provider_run_id: str
    ) -> RunUpdate:
        # The signed callback is authoritative; there is nothing to enrich. The
        # persistence layer treats NotImplementedError as "skip enrichment".
        raise NotImplementedError("Airflow callbacks are authoritative; no REST enrichment")

    def list_recent_runs(
        self, config: Mapping[str, Any], secret: str, since: datetime
    ) -> list[RunUpdate]:
        """Poll recent DAG runs (**all states**) via the batch ``dagRuns/list`` endpoint.

        POSTs ``{start_date_gte: since}`` across all DAGs (``~``) with **no state
        filter** and maps each run to a `RunUpdate`. The poll records every state
        for the monitor view (#490); trigger-on-success is enforced downstream in
        ``ingest_polled_runs`` (only ``succeeded`` triggers, ADR 0004). DAG-run
        states outside the status map are skipped; malformed rows are skipped;
        transport/auth errors raise (the polling task fails soft per connection).
        """
        cfg = AirflowConfig.model_validate(dict(config))
        headers, auth = _auth(cfg, secret)
        response = httpx.post(
            f"{cfg.base_url}{_DAGRUNS_LIST_PATH}",
            headers=headers,
            auth=auth,
            json={
                "start_date_gte": since.isoformat(),
                "order_by": "-start_date",
                "page_limit": _DAGRUNS_PAGE_LIMIT,
            },
            timeout=_TEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

        # Single page only (page_limit): a window with >page_limit succeeded runs
        # leaves the overflow for a later poll. Fine for the v1 10-min window.
        updates: list[RunUpdate] = []
        for item in response.json().get("dag_runs", []):
            status = _AIRFLOW_STATE_MAP.get(str(item.get("state")).lower())
            dag_id = item.get("dag_id")
            run_id = item.get("dag_run_id")
            if status is None or not dag_id or not run_id:
                continue
            updates.append(
                RunUpdate(
                    provider_run_id=str(run_id),
                    pipeline_or_dag_id=str(dag_id),
                    resource_name=cfg.base_url,
                    status=status,
                    started_at=_parse_dt(item.get("start_date")),
                    finished_at=_parse_dt(item.get("end_date")),
                )
            )
        return updates
