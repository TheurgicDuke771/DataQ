"""S3 connection adapter — AWS S3 and any S3-compatible store."""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from backend.app.core.s3_endpoint import (
    S3AddressingStyle,
    addressing_config_kwargs,
    normalize_addressing_style,
    normalize_endpoint_url,
)

# Fail fast rather than hang the request thread on an unreachable endpoint.
_TEST_TIMEOUT_SECONDS = 10


class S3Config(BaseModel):
    """Non-secret S3 connection config (the secret access key comes from secrets)."""

    model_config = ConfigDict(extra="forbid")

    bucket: str
    region: str
    auth_type: Literal["access_key", "iam_role"] = "access_key"
    access_key_id: str | None = None
    # S3-compatible stores (#1063). Unset = AWS, byte-identical to pre-#1063.
    endpoint_url: str | None = None
    addressing_style: S3AddressingStyle = "auto"

    @field_validator("endpoint_url")
    @classmethod
    def _endpoint(cls, value: str | None) -> str | None:
        return normalize_endpoint_url(value)

    @field_validator("addressing_style", mode="before")
    @classmethod
    def _style(cls, value: Any) -> Any:
        return normalize_addressing_style(value)

    @model_validator(mode="after")
    def _check_auth(self) -> S3Config:
        if self.auth_type == "iam_role":
            raise ValueError(
                "iam_role auth is deferred to Week 7 (needs an ambient AWS role to "
                "test against); use auth_type='access_key' with an access key in v1"
            )
        if not self.access_key_id:
            raise ValueError("access_key_id is required when auth_type is 'access_key'")
        return self


class S3ConnectionAdapter:
    """`ConnectionAdapter` for AWS S3 — config validation + a head_bucket probe."""

    # #1401: `endpoint_url` (#1063, the S3-compatible path) redirects the signed request to any
    # host.
    destination_fields: ClassVar[dict[str, tuple[str, ...]]] = {"secret": ("endpoint_url",)}

    def validate_config(self, raw: dict[str, Any]) -> S3Config:
        return S3Config.model_validate(raw)

    def test(self, raw: dict[str, Any], secret: str | None, **_: Any) -> None:
        """Issue ``head_bucket`` with the static credential; raise on any failure."""
        if secret is None:
            raise ValueError("a credential is required to test an S3 connection")
        import boto3
        from botocore.config import Config

        config = self.validate_config(raw)
        client = boto3.client(
            "s3",
            region_name=config.region,
            aws_access_key_id=config.access_key_id,
            aws_secret_access_key=secret,
            # None is boto3's own default for endpoint_url, so an AWS connection is
            # constructed exactly as it was before #1063.
            endpoint_url=config.endpoint_url,
            config=Config(
                connect_timeout=_TEST_TIMEOUT_SECONDS,
                read_timeout=_TEST_TIMEOUT_SECONDS,
                retries={"max_attempts": 1},
                **addressing_config_kwargs(config.endpoint_url, config.addressing_style),
            ),
        )
        client.head_bucket(Bucket=config.bucket)
