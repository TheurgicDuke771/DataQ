"""Google Cloud Storage connection adapter — the third object store beside ADLS Gen2 and S3 (#1683).

The stored secret is a service-account **key file** (the whole JSON, ``auth_type:
service_account_key``); ``anonymous`` reads a public bucket with no credential. Keyless /
workload-identity auth is #2309. ``endpoint_url`` points the same connection at an emulator
(``fake-gcs-server``) or a GCS-compatible endpoint.
"""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from backend.app.core.uri_credentials import uri_password

#: Connect / read timeouts (s) for the save-time probe — fail fast on an unreachable endpoint.
_TEST_TIMEOUT = (10, 10)

#: Read-only OAuth scope: the credential can never be used to write through DataQ.
READ_ONLY_SCOPE = "https://www.googleapis.com/auth/devstorage.read_only"

GcsAuthType = Literal["service_account_key", "anonymous"]

# GCS bucket naming: lower-case letters, digits, dashes, underscores and dots, 3-63 chars, or up
# to 222 when dotted with every dot-separated part at most 63; never `goog`-prefixed or an IP.
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]$")
_IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def _bucket_problem(value: str) -> bool:
    if not _BUCKET.fullmatch(value) or ".." in value:
        return True
    if "." not in value and len(value) > 63:
        return True
    if any(len(part) > 63 for part in value.split(".")):
        return True
    return value.startswith("goog") or "google" in value or bool(_IPV4.fullmatch(value))


_PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")

_KEY_FIELDS = ("client_email", "private_key", "token_uri")


class GcsKeyInvalidError(ValueError):
    """The stored secret is not a usable service-account key file."""


def parse_service_account_key(secret: str) -> dict[str, Any]:
    """The key file as a dict, or raise `GcsKeyInvalidError` (never echoing the secret)."""
    try:
        info = json.loads(secret)
    except ValueError as exc:
        raise GcsKeyInvalidError(
            "the credential must be the service account's JSON key file, pasted whole"
        ) from exc
    if not isinstance(info, dict) or info.get("type") != "service_account":
        raise GcsKeyInvalidError(
            'the credential must be a service-account key file ("type": "service_account")'
        )
    missing = [f for f in _KEY_FIELDS if not isinstance(info.get(f), str) or not info[f]]
    if missing:
        raise GcsKeyInvalidError(f"the service-account key file is missing {', '.join(missing)}")
    return info


def normalize_endpoint_url(value: str | None) -> str | None:
    """Validate + tidy a GCS-compatible endpoint; ``None``/blank means Google Cloud Storage."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if not stripped.startswith(("http://", "https://")):
        raise ValueError("endpoint_url must start with http:// or https://")
    # The settled rule for a URL-shaped non-secret field (#754/#826).
    if uri_password(stripped):
        raise ValueError(
            "endpoint_url must not embed a credential (config is stored and returned in "
            "plaintext). Store the service-account key file as the credential."
        )
    return stripped.rstrip("/")


class GcsConfig(BaseModel):
    """Non-secret GCS connection config (the service-account key file is the secret)."""

    model_config = ConfigDict(extra="forbid")

    bucket: str
    auth_type: GcsAuthType = "service_account_key"
    # Billing project for the reads (sent as `userProject`, so a Requester Pays bucket works);
    # blank bills the bucket's owner and uses the key file's own project for the client.
    project_id: str | None = None
    # An emulator or GCS-compatible endpoint. Unset = Google Cloud Storage.
    endpoint_url: str | None = None

    @field_validator("bucket")
    @classmethod
    def _bucket(cls, value: str) -> str:
        value = value.strip()
        if _bucket_problem(value):
            raise ValueError(
                "bucket must be a GCS bucket name: 3-63 lower-case letters, digits, dashes or "
                "underscores (up to 222 with dots, each dotted part at most 63), starting and "
                "ending with a letter or digit, not starting with 'goog' and not an IP address"
            )
        return value

    @field_validator("project_id")
    @classmethod
    def _project(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not _PROJECT_ID.fullmatch(value):
            raise ValueError(
                "project_id must be a Google Cloud project ID (6-30 lower-case letters, digits "
                "or dashes, starting with a letter)"
            )
        return value

    @field_validator("endpoint_url")
    @classmethod
    def _endpoint(cls, value: str | None) -> str | None:
        return normalize_endpoint_url(value)

    def requires_secret(self) -> bool:
        return self.auth_type != "anonymous"


def gcs_client(config: GcsConfig, secret: str | None, *, timeout: Any = None) -> Any:
    """The ONE place a GCS connection's credential becomes a `storage.Client`.

    Every consumer (the connection test, flat-file reads/listing/browse) goes through here, so an
    auth mode is added once rather than at each door. The caller must ``close()`` the result.
    """
    from google.cloud import storage

    client_options: dict[str, Any] = {}
    if config.endpoint_url is not None:
        client_options["api_endpoint"] = config.endpoint_url

    if config.auth_type == "anonymous":
        from google.auth.credentials import AnonymousCredentials

        return storage.Client(
            project=config.project_id or "anonymous",
            credentials=AnonymousCredentials(),
            client_options=client_options or None,
        )
    if secret is None:
        raise ValueError("a service-account key file is required for service_account_key auth")
    from google.oauth2 import service_account

    info = parse_service_account_key(secret)
    try:
        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=[READ_ONLY_SCOPE]
        )
    except (ValueError, TypeError) as exc:
        # google-auth's own message can quote the material it could not parse.
        raise GcsKeyInvalidError(
            "the service-account key file's private_key could not be read"
        ) from exc
    project = config.project_id or info.get("project_id") or None
    return storage.Client(
        project=project, credentials=credentials, client_options=client_options or None
    )


def gcs_bucket(client: Any, config: GcsConfig) -> Any:
    """The configured bucket, billing ``project_id`` for every request made through it."""
    return client.bucket(config.bucket, user_project=config.project_id)


def is_not_found(exc: BaseException) -> bool:
    """Whether ``exc`` is the client's 404 — a missing object or bucket."""
    from google.api_core.exceptions import NotFound

    return isinstance(exc, NotFound)


class GcsConnectionAdapter:
    """`ConnectionAdapter` for Google Cloud Storage — config validation + a one-object listing."""

    # The key file's token goes to Google (its own `token_uri`, inside the secret); the bearer it
    # yields is presented to `endpoint_url` (#1401). `auth_type` decides whether the stored secret
    # is used at all.
    destination_fields: ClassVar[dict[str, tuple[str, ...]]] = {
        "secret": ("endpoint_url", "auth_type")
    }
    #: `anonymous` stores no secret; `requires_secret` on the config says which.
    secret_optional: ClassVar[bool] = True

    def validate_config(self, raw: dict[str, Any]) -> GcsConfig:
        return GcsConfig.model_validate(raw)

    def test(self, raw: dict[str, Any], secret: str | None, **_: Any) -> None:
        """List one object with the stored credential; raise on any failure.

        A listing, not a bucket GET: `roles/storage.objectViewer` — the least privilege a reader
        needs — holds ``storage.objects.list`` but not ``storage.buckets.get``, and listing is
        what batch resolution and browsing do.
        """
        config = self.validate_config(raw)
        if config.requires_secret() and secret is None:
            raise ValueError("a credential is required to test a GCS connection")
        client = gcs_client(config, secret)
        try:
            page = client.list_blobs(
                gcs_bucket(client, config), max_results=1, timeout=_TEST_TIMEOUT, retry=None
            )
            next(iter(page), None)
        finally:
            client.close()
