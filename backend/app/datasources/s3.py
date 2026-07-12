"""AWS S3 connection adapter.

A flat-file datasource (CLAUDE.md §4): DQ checks run against files in a bucket.
Week 2 ships only the `ConnectionAdapter` seam (config validation + connectivity
`test`); the GX pandas/S3 `CheckRunner` that reads the files is a Week-3 concern.

**Auth — v1 supports static access keys only.** The roadmap lists IAM role *and*
access key, but an IAM role has no stored secret and is only meaningfully
testable once DataQ runs on AWS with an instance/task role — so it (and the
broader ``connections.secret_ref`` nullability change) is deferred to Week 7. An
``iam_role`` config is accepted by the schema but rejected with a clear message
in v1; use an access key (``access_key_id`` in config, secret access key in the
SecretStore).

``test`` issues ``head_bucket`` — a green test means the credentials authenticate
and the bucket is reachable. ``boto3`` is imported lazily (per ``core/secrets.py``)
so non-S3 deployments don't pay the import cost; retries are disabled so an
auth/endpoint failure surfaces immediately. Like the other adapters it runs live
and fails-soft pending real credentials.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

# Fail fast rather than hang the request thread on an unreachable endpoint.
_TEST_TIMEOUT_SECONDS = 10


class S3Config(BaseModel):
    """Non-secret S3 connection config (the secret access key comes from secrets).

    Maps from ``Connection.config``. ``access_key_id`` is the non-secret half of
    the static credential; the secret access key is resolved from the SecretStore
    at test time.
    """

    model_config = ConfigDict(extra="forbid")

    bucket: str
    region: str
    auth_type: Literal["access_key", "iam_role"] = "access_key"
    access_key_id: str | None = None

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

    def validate_config(self, raw: dict[str, Any]) -> S3Config:
        return S3Config.model_validate(raw)

    def test(self, raw: dict[str, Any], secret: str, **_: Any) -> None:
        """Issue ``head_bucket`` with the static credential; raise on any failure.

        ``secret`` is the secret access key. Retries are disabled so an
        auth/permission/endpoint failure surfaces immediately.
        """
        import boto3
        from botocore.config import Config

        config = self.validate_config(raw)
        client = boto3.client(
            "s3",
            region_name=config.region,
            aws_access_key_id=config.access_key_id,
            aws_secret_access_key=secret,
            config=Config(
                connect_timeout=_TEST_TIMEOUT_SECONDS,
                read_timeout=_TEST_TIMEOUT_SECONDS,
                retries={"max_attempts": 1},
            ),
        )
        client.head_bucket(Bucket=config.bucket)
