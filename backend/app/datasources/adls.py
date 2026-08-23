"""ADLS Gen2 (Azure Data Lake Storage) connection adapter."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from backend.app.core.credential_expiry import azure_sas_expiry

# Fail fast rather than hang the request thread on an unreachable account.
_TEST_TIMEOUT_SECONDS = 10


class AdlsConfig(BaseModel):
    """Non-secret ADLS Gen2 connection config (the SAS token comes from secrets)."""

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

    # #1401: an arbitrary URL the SAS/account key is presented to.
    destination_fields: ClassVar[dict[str, tuple[str, ...]]] = {"secret": ("account_url",)}

    def validate_config(self, raw: dict[str, Any]) -> AdlsConfig:
        return AdlsConfig.model_validate(raw)

    def credential_expiry(self, raw: dict[str, Any], secret: str, **_: Any) -> datetime | None:
        """When this connection's SAS stops working (#838), or ``None``."""
        return azure_sas_expiry(secret)

    def test(self, raw: dict[str, Any], secret: str | None, **_: Any) -> None:
        """Read the container's properties via SAS; raise on any failure."""
        if secret is None:
            raise ValueError("a credential is required to test an ADLS Gen2 connection")
        from azure.storage.blob import BlobServiceClient

        config = self.validate_config(raw)
        # The azure-storage-blob surface is only partially typed (e.g. close() is unannotated);
        # treat the client as dynamic so strict mypy doesn't flag no-untyped-call on the SDK
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
