"""S3 connection adapter tests — config validation + the head_bucket probe.

No live AWS: ``boto3.client`` is monkeypatched so the head_bucket probe runs
against a fake. The adapter is DB-free, so these are pure unit tests (no
db_session).
"""

from typing import Any

import boto3
import pytest
from pydantic import ValidationError

from backend.app.datasources.s3 import S3Config, S3ConnectionAdapter

_ACCESS_KEY_CONFIG = {
    "bucket": "dataq-lake",
    "region": "eu-west-1",
    "auth_type": "access_key",
    "access_key_id": "AKIAEXAMPLE",
}


# ───────────────────────── validate_config ─────────────────────────


def test_validate_config_accepts_access_key_config() -> None:
    cfg = S3ConnectionAdapter().validate_config(dict(_ACCESS_KEY_CONFIG))
    assert isinstance(cfg, S3Config)
    assert cfg.bucket == "dataq-lake"


def test_validate_config_defaults_auth_type_to_access_key() -> None:
    cfg = S3ConnectionAdapter().validate_config(
        {"bucket": "b", "region": "us-east-1", "access_key_id": "AKIA"}
    )
    assert cfg.auth_type == "access_key"


def test_validate_config_rejects_iam_role_as_deferred() -> None:
    with pytest.raises(ValidationError, match="deferred to Week 7"):
        S3ConnectionAdapter().validate_config(
            {"bucket": "b", "region": "us-east-1", "auth_type": "iam_role"}
        )


def test_validate_config_rejects_access_key_without_key_id() -> None:
    with pytest.raises(ValidationError, match="access_key_id is required"):
        S3ConnectionAdapter().validate_config({"bucket": "b", "region": "us-east-1"})


def test_validate_config_rejects_unknown_field() -> None:
    # `endpoint` is deliberately the near-miss of the real `endpoint_url` field:
    # extra="forbid" must still reject it rather than silently ignoring a typo
    # that would leave the connection quietly pointing at AWS.
    with pytest.raises(ValidationError):
        S3ConnectionAdapter().validate_config({**_ACCESS_KEY_CONFIG, "endpoint": "x"})


def test_validate_config_defaults_to_aws_with_auto_addressing() -> None:
    cfg = S3ConnectionAdapter().validate_config(dict(_ACCESS_KEY_CONFIG))
    assert cfg.endpoint_url is None
    assert cfg.addressing_style == "auto"


def test_validate_config_normalizes_the_endpoint_url() -> None:
    cfg = S3ConnectionAdapter().validate_config(
        {**_ACCESS_KEY_CONFIG, "endpoint_url": " http://minio:9000/ "}
    )
    assert cfg.endpoint_url == "http://minio:9000"


def test_validate_config_treats_a_cleared_endpoint_as_aws() -> None:
    """The connection form submits "" for an optional field the user cleared."""
    cfg = S3ConnectionAdapter().validate_config({**_ACCESS_KEY_CONFIG, "endpoint_url": ""})
    assert cfg.endpoint_url is None


def test_validate_config_rejects_a_schemeless_endpoint_url() -> None:
    with pytest.raises(ValidationError, match="must start with http:// or https://"):
        S3ConnectionAdapter().validate_config({**_ACCESS_KEY_CONFIG, "endpoint_url": "minio:9000"})


def test_validate_config_rejects_a_credential_in_the_endpoint_url() -> None:
    """`config` is plaintext JSONB — a credential here would be persisted outside
    the secret store (#754/#826; same rule as `IcebergConfig.catalog_uri`)."""
    with pytest.raises(ValidationError, match="must not embed a credential"):
        S3ConnectionAdapter().validate_config(
            {**_ACCESS_KEY_CONFIG, "endpoint_url": "https://AKIAKEY:secretkey@minio:9000"}
        )


def test_validate_config_allows_a_username_only_endpoint_url() -> None:
    """A bare username is an identifier, not a credential — `uri_password` says so,
    and rejecting it would be a false positive on a legitimate URL."""
    cfg = S3ConnectionAdapter().validate_config(
        {**_ACCESS_KEY_CONFIG, "endpoint_url": "https://tenant@minio:9000"}
    )
    assert cfg.endpoint_url == "https://tenant@minio:9000"


def test_validate_config_treats_a_cleared_addressing_style_as_auto() -> None:
    """Same cleared-field shape — but "" would be rejected by the Literal."""
    cfg = S3ConnectionAdapter().validate_config({**_ACCESS_KEY_CONFIG, "addressing_style": ""})
    assert cfg.addressing_style == "auto"


def test_validate_config_rejects_an_unknown_addressing_style() -> None:
    with pytest.raises(ValidationError):
        S3ConnectionAdapter().validate_config({**_ACCESS_KEY_CONFIG, "addressing_style": "pth"})


# ───────────────────────── test() connectivity ─────────────────────


def test_test_head_buckets_with_access_key(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

    class _FakeClient:
        def head_bucket(self, **kwargs: Any) -> None:
            calls["head_bucket"] = kwargs

    def fake_client(service: str, **kwargs: Any) -> _FakeClient:
        calls["service"] = service
        calls["client_kwargs"] = kwargs
        return _FakeClient()

    monkeypatch.setattr(boto3, "client", fake_client)
    S3ConnectionAdapter().test(dict(_ACCESS_KEY_CONFIG), "sekret-access-key")  # no raise

    assert calls["service"] == "s3"
    assert calls["client_kwargs"]["region_name"] == "eu-west-1"
    assert calls["client_kwargs"]["aws_access_key_id"] == "AKIAEXAMPLE"
    assert calls["client_kwargs"]["aws_secret_access_key"] == "sekret-access-key"
    assert calls["head_bucket"] == {"Bucket": "dataq-lake"}


def test_test_targets_a_compatible_endpoint_with_path_addressing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A MinIO-shaped config must reach boto3 as endpoint + path addressing (#1063).

    Both halves matter: without the endpoint the probe hits AWS, and without path
    addressing boto3 resolves ``dataq-lake.minio:9000`` — a host that does not
    exist — so head_bucket fails for a reason that looks like a network fault.
    """
    calls: dict[str, Any] = {}

    class _FakeClient:
        def head_bucket(self, **kwargs: Any) -> None:
            calls["head_bucket"] = kwargs

    def fake_client(service: str, **kwargs: Any) -> _FakeClient:
        calls["client_kwargs"] = kwargs
        return _FakeClient()

    monkeypatch.setattr(boto3, "client", fake_client)
    S3ConnectionAdapter().test(
        {**_ACCESS_KEY_CONFIG, "endpoint_url": "http://minio:9000"}, "sekret"
    )

    assert calls["client_kwargs"]["endpoint_url"] == "http://minio:9000"
    assert calls["client_kwargs"]["config"].s3 == {"addressing_style": "path"}


def test_test_leaves_the_aws_client_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    """The no-endpoint path must be byte-identical to pre-#1063.

    `endpoint_url=None` is boto3's own default, and `config.s3` stays unset rather
    than being pinned to an addressing style — so adding S3-compatible support
    cannot change how an existing AWS connection resolves.
    """
    calls: dict[str, Any] = {}

    class _FakeClient:
        def head_bucket(self, **kwargs: Any) -> None: ...

    def fake_client(service: str, **kwargs: Any) -> _FakeClient:
        calls["client_kwargs"] = kwargs
        return _FakeClient()

    monkeypatch.setattr(boto3, "client", fake_client)
    S3ConnectionAdapter().test(dict(_ACCESS_KEY_CONFIG), "sekret")

    assert calls["client_kwargs"]["endpoint_url"] is None
    assert calls["client_kwargs"]["config"].s3 is None


def test_test_raises_when_head_bucket_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeClient:
        def head_bucket(self, **kwargs: Any) -> None:
            raise RuntimeError("403 Forbidden")

    monkeypatch.setattr(boto3, "client", lambda service, **kw: _FakeClient())
    with pytest.raises(RuntimeError, match="403"):
        S3ConnectionAdapter().test(dict(_ACCESS_KEY_CONFIG), "sekret-access-key")
