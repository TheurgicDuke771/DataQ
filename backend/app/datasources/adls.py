"""ADLS Gen2 (Azure Data Lake Storage) connection adapter.

Also serves any ADLS-compatible Blob endpoint set as ``account_url`` — e.g. Microsoft Fabric
OneLake (``https://onelake.blob.fabric.microsoft.com``, container = workspace), which accepts
Entra service-principal auth but not a long-lived SAS (#1680).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from backend.app.core.credential_expiry import azure_sas_expiry

# Fail fast rather than hang the request thread on an unreachable account.
_TEST_TIMEOUT_SECONDS = 10

# A GUID or a verified domain (`contoso.onmicrosoft.com`). It becomes a path segment of the Entra
# token URL, so nothing that could add a segment or a query is allowed.
_TENANT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
_CLIENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,127}$")


class AdlsConfig(BaseModel):
    """Non-secret ADLS Gen2 connection config.

    The secret is the SAS token (``auth_type='sas'``) or the Entra app's client secret
    (``auth_type='service_principal'``, with ``tenant_id`` + ``client_id`` here).
    """

    model_config = ConfigDict(extra="forbid")

    account_url: str
    container: str
    auth_type: Literal["sas", "service_principal", "managed_identity"] = "sas"
    tenant_id: str | None = None
    client_id: str | None = None

    @field_validator("account_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("account_url must start with http:// or https://")
        return value.rstrip("/")

    @model_validator(mode="after")
    def _auth_fields(self) -> AdlsConfig:
        if self.auth_type == "managed_identity":
            raise ValueError(
                "managed_identity auth is deferred to Week 7 (needs an ambient Azure "
                "identity to test against); use auth_type='sas' or 'service_principal'"
            )
        if self.auth_type == "service_principal":
            if not self.tenant_id or not _TENANT_ID.fullmatch(self.tenant_id):
                raise ValueError(
                    "tenant_id is required for service_principal auth: the directory "
                    "(tenant) ID or a verified domain"
                )
            if not self.client_id or not _CLIENT_ID.fullmatch(self.client_id):
                raise ValueError(
                    "client_id is required for service_principal auth: the application "
                    "(client) ID"
                )
        elif self.tenant_id is not None or self.client_id is not None:
            # A SAS connection would silently ignore them.
            raise ValueError("tenant_id / client_id apply only to service_principal auth")
        return self


class _CredentialOwningClient:
    """A `BlobServiceClient` whose ``close()`` also closes the token credential it owns —
    the two hold separate transport sessions, and closing the client leaves the
    credential's open.
    """

    def __init__(self, client: Any, credential: Any) -> None:
        self._client = client
        self._credential = credential

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def close(self) -> None:
        try:
            self._client.close()
        finally:
            self._credential.close()


def blob_service_client(config: AdlsConfig, secret: str, **client_kwargs: Any) -> Any:
    """The ONE place an ADLS connection's credential becomes a `BlobServiceClient`.

    Every consumer (the connection test, flat-file reads/listing/browse) goes through here, so an
    auth mode is added once rather than at each door. ``client_kwargs`` (timeouts, retries) also
    bound the Entra token request. The caller must ``close()`` the result.
    """
    from azure.storage.blob import BlobServiceClient

    if config.auth_type == "sas":
        return BlobServiceClient(account_url=config.account_url, credential=secret, **client_kwargs)
    if config.auth_type != "service_principal" or not config.tenant_id or not config.client_id:
        # Unreachable through a validated config; refuse rather than fall through to anonymous.
        raise ValueError(f"unsupported ADLS auth_type {config.auth_type!r}")

    from azure.identity import ClientSecretCredential

    credential = ClientSecretCredential(config.tenant_id, config.client_id, secret, **client_kwargs)
    try:
        # The client requests the Azure Storage audience, which OneLake also accepts.
        client = BlobServiceClient(
            account_url=config.account_url, credential=credential, **client_kwargs
        )
    except BaseException:
        credential.close()
        raise
    return _CredentialOwningClient(client, credential)


class AdlsConnectionAdapter:
    """`ConnectionAdapter` for ADLS Gen2 — config validation + a container probe."""

    # #1401: the SAS is presented to `account_url`; a service principal's secret is presented to
    # `tenant_id`'s token endpoint as `client_id`, and the token it yields to `account_url`.
    # `auth_type` decides which of those happens to the stored secret at all.
    destination_fields: ClassVar[dict[str, tuple[str, ...]]] = {
        "secret": ("account_url", "auth_type", "tenant_id", "client_id")
    }

    def validate_config(self, raw: dict[str, Any]) -> AdlsConfig:
        return AdlsConfig.model_validate(raw)

    def credential_expiry(self, raw: dict[str, Any], secret: str, **_: Any) -> datetime | None:
        """When this connection's SAS stops working (#838), or ``None``.

        A client secret carries no readable lifetime — its expiry lives in Entra — so a
        service-principal connection is always ``None`` ("not readable", never "does not
        expire"), even if the secret happens to look like a SAS.
        """
        if raw.get("auth_type", "sas") != "sas":
            return None
        return azure_sas_expiry(secret)

    def test(self, raw: dict[str, Any], secret: str | None, **_: Any) -> None:
        """Read the container's properties with the stored credential; raise on any failure."""
        if secret is None:
            raise ValueError("a credential is required to test an ADLS Gen2 connection")
        config = self.validate_config(raw)
        client = blob_service_client(
            config,
            secret,
            retry_total=0,
            connection_timeout=_TEST_TIMEOUT_SECONDS,
            read_timeout=_TEST_TIMEOUT_SECONDS,
        )
        try:
            container = client.get_container_client(config.container)
            container.get_container_properties()
            if config.auth_type == "service_principal":
                # Container properties are authorized by the control-plane `containers/read`
                # action, which a plain Contributor holds without any DATA role — so that call
                # alone passes for a principal every read will 403. One listed entry needs the
                # data role the flat-file reads need. Delimited: OneLake refuses a flat listing
                # of the container (workspace) root with a 400, but serves this one.
                next(iter(container.walk_blobs(delimiter="/", results_per_page=1)), None)
        finally:
            client.close()
