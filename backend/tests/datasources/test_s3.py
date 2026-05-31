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
    with pytest.raises(ValidationError):
        S3ConnectionAdapter().validate_config({**_ACCESS_KEY_CONFIG, "endpoint": "x"})


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


def test_test_raises_when_head_bucket_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeClient:
        def head_bucket(self, **kwargs: Any) -> None:
            raise RuntimeError("403 Forbidden")

    monkeypatch.setattr(boto3, "client", lambda service, **kw: _FakeClient())
    with pytest.raises(RuntimeError, match="403"):
        S3ConnectionAdapter().test(dict(_ACCESS_KEY_CONFIG), "sekret-access-key")
