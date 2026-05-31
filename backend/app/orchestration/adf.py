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

from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

# Azure AD OAuth2 endpoint + ARM management host. The client-credentials grant
# against this scope yields a bearer token usable for the factory GET below.
_AAD_OAUTH_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
_ARM_SCOPE = "https://management.azure.com/.default"
_ARM_FACTORY_URL = (
    "https://management.azure.com/subscriptions/{subscription_id}"
    "/resourceGroups/{resource_group}"
    "/providers/Microsoft.DataFactory/factories/{factory_name}"
)
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
