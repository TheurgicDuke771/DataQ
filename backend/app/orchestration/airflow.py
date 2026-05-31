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

from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

# Lightest authenticated stable-REST call — proves reachability + auth + that the
# REST API is enabled, without listing or mutating anything.
_DAGS_PROBE_PATH = "/api/v1/dags"
_DAGS_PROBE_PARAMS = {"limit": 1}

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
        headers: dict[str, str] = {}
        auth: httpx.Auth | None = None
        if config.auth_type == "token":
            headers["Authorization"] = f"Bearer {secret}"
        else:  # basic — username guaranteed present by the model validator
            auth = httpx.BasicAuth(str(config.username), secret)

        response = httpx.get(
            f"{config.base_url}{_DAGS_PROBE_PATH}",
            params=_DAGS_PROBE_PARAMS,
            headers=headers,
            auth=auth,
            timeout=_TEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
