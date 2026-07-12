"""ADLS Gen2 (Azure Data Lake Storage) connection adapter.

A flat-file datasource (CLAUDE.md §4): DQ checks run against files in a
container. Week 2 ships only the `ConnectionAdapter` seam (config validation +
connectivity `test`); the GX pandas/ABFS `CheckRunner` that reads the files is a
Week-3 concern.

**Auth — v1 supports SAS only.** The roadmap lists managed-identity *and* SAS,
but managed identity has no stored secret and is only meaningfully testable once
DataQ runs on Azure with an ambient identity — so it (and the broader
``connections.secret_ref`` nullability change) is deferred to Week 7. A
``managed_identity`` config is accepted by the schema but rejected with a clear
message in v1; use a SAS token (held in the SecretStore).

``test`` builds a ``BlobServiceClient`` from the account URL + SAS and reads the
container's properties — a green test means the endpoint is reachable, the SAS
authenticates, and the container exists. The Azure SDK is imported lazily (per
``core/secrets.py``) so non-ADLS deployments don't pay the import cost; like the
other adapters it runs live and fails-soft pending real credentials.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

# Fail fast rather than hang the request thread on an unreachable account.
_TEST_TIMEOUT_SECONDS = 10


class AdlsConfig(BaseModel):
    """Non-secret ADLS Gen2 connection config (the SAS token comes from secrets).

    Maps from ``Connection.config``. ``account_url`` is the storage endpoint
    (e.g. ``https://<account>.blob.core.windows.net``); ``container`` is the
    filesystem. The SAS token is resolved from the SecretStore at test time.
    """

    model_config = ConfigDict(extra="forbid")

    account_url: str
    container: str
    auth_type: Literal["sas", "managed_identity"] = "sas"

    @field_validator("account_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("account_url must start with http:// or https://")
        return value.rstrip("/")

    @model_validator(mode="after")
    def _managed_identity_deferred(self) -> AdlsConfig:
        if self.auth_type == "managed_identity":
            raise ValueError(
                "managed_identity auth is deferred to Week 7 (needs an ambient Azure "
                "identity to test against); use auth_type='sas' with a SAS token in v1"
            )
        return self


class AdlsConnectionAdapter:
    """`ConnectionAdapter` for ADLS Gen2 — config validation + a container probe."""

    def validate_config(self, raw: dict[str, Any]) -> AdlsConfig:
        return AdlsConfig.model_validate(raw)

    def test(self, raw: dict[str, Any], secret: str, **_: Any) -> None:
        """Read the container's properties via SAS; raise on any failure.

        ``secret`` is the SAS token. Retries are disabled so an auth/endpoint
        failure surfaces immediately rather than after the SDK's retry budget.
        """
        from azure.storage.blob import BlobServiceClient

        config = self.validate_config(raw)
        # The azure-storage-blob surface is only partially typed (e.g. close() is
        # unannotated); treat the client as dynamic so strict mypy doesn't flag
        # no-untyped-call on the SDK methods.
        client: Any = BlobServiceClient(
            account_url=config.account_url,
            credential=secret,
            retry_total=0,
            connection_timeout=_TEST_TIMEOUT_SECONDS,
            read_timeout=_TEST_TIMEOUT_SECONDS,
        )
        try:
            client.get_container_client(config.container).get_container_properties()
        finally:
            client.close()
