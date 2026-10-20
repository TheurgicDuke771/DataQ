"""Google Cloud Storage adapter tests — config validation + the one-object listing probe (#1683)."""

from __future__ import annotations

import json
from typing import Any, ClassVar

import pytest
from google.cloud import storage
from pydantic import ValidationError

from backend.app.datasources import registry
from backend.app.datasources.engines import engines_for
from backend.app.datasources.gcs import (
    READ_ONLY_SCOPE,
    GcsConfig,
    GcsConnectionAdapter,
    GcsKeyInvalidError,
    parse_service_account_key,
)
from backend.app.datasources.sql_engines import authenticates_without_secret

_CONFIG = {"bucket": "dataq-landing", "auth_type": "service_account_key"}


def _pem() -> str:
    """A throwaway RSA key in the PKCS8 PEM shape a downloaded key file carries."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


_KEY = {
    "type": "service_account",
    "project_id": "dq-project",
    "private_key_id": "k1",
    "private_key": _pem(),
    "client_email": "dq@dq-project.iam.gserviceaccount.com",
    "client_id": "1",
    "token_uri": "https://oauth2.googleapis.com/token",
}
_SECRET = json.dumps(_KEY)


# ───────────────────────── validate_config ─────────────────────────


def test_validate_config_defaults_to_a_service_account_key_on_google() -> None:
    cfg = GcsConnectionAdapter().validate_config({"bucket": "dataq-landing"})
    assert isinstance(cfg, GcsConfig)
    assert (cfg.auth_type, cfg.endpoint_url, cfg.project_id) == ("service_account_key", None, None)
    assert cfg.requires_secret()


def test_anonymous_auth_needs_no_secret() -> None:
    cfg = GcsConfig.model_validate({"bucket": "public-data", "auth_type": "anonymous"})
    assert not cfg.requires_secret()
    assert authenticates_without_secret("gcs", {"bucket": "public-data", "auth_type": "anonymous"})
    assert not authenticates_without_secret("gcs", _CONFIG)
    assert GcsConnectionAdapter.secret_optional


@pytest.mark.parametrize(
    "bucket",
    [
        "",
        "ab",
        "Upper-Case",
        "has space",
        "a" * 223,
        "double..dot",
        "-leading",
        "trailing-",
        "nul\x00byte",
        "bucket/with/path",
        "gs://bucket",
    ],
)
def test_validate_config_rejects_a_name_that_is_not_a_bucket(bucket: str) -> None:
    with pytest.raises(ValidationError, match="bucket must be a GCS bucket name"):
        GcsConfig.model_validate({"bucket": bucket})


def test_validate_config_strips_and_accepts_every_legal_bucket_shape() -> None:
    for name in ("abc", "my_bucket.example.com", "a-1_2.3", " padded "):
        assert GcsConfig.model_validate({"bucket": name}).bucket == name.strip()


def test_validate_config_rejects_unknown_fields_and_auth_types() -> None:
    with pytest.raises(ValidationError):
        GcsConfig.model_validate({**_CONFIG, "endpoint": "http://x"})
    with pytest.raises(ValidationError):
        GcsConfig.model_validate({**_CONFIG, "auth_type": "workload_identity"})


def test_validate_config_normalizes_the_endpoint_url() -> None:
    cfg = GcsConfig.model_validate({**_CONFIG, "endpoint_url": " http://fake-gcs:4443/ "})
    assert cfg.endpoint_url == "http://fake-gcs:4443"
    assert GcsConfig.model_validate({**_CONFIG, "endpoint_url": ""}).endpoint_url is None


def test_validate_config_rejects_a_schemeless_endpoint_url() -> None:
    with pytest.raises(ValidationError, match="must start with http:// or https://"):
        GcsConfig.model_validate({**_CONFIG, "endpoint_url": "fake-gcs:4443"})


def test_validate_config_rejects_a_credential_in_the_endpoint_url() -> None:
    """`config` is plaintext JSONB (#754/#826) — same rule as the S3 adapter."""
    with pytest.raises(ValidationError, match="must not embed a credential"):
        GcsConfig.model_validate({**_CONFIG, "endpoint_url": "https://user:token@fake-gcs:4443"})


@pytest.mark.parametrize("project", ["", "  "])
def test_validate_config_treats_a_blank_project_as_unset(project: str) -> None:
    assert GcsConfig.model_validate({**_CONFIG, "project_id": project}).project_id is None


@pytest.mark.parametrize("project", ["ab", "Has-Upper", "1starts-digit", "x" * 31, "a b"])
def test_validate_config_rejects_a_malformed_project_id(project: str) -> None:
    with pytest.raises(ValidationError, match="project_id must be"):
        GcsConfig.model_validate({**_CONFIG, "project_id": project})


# ───────────────────────── the key file ─────────────────────────


def test_parse_service_account_key_accepts_the_download_shape() -> None:
    assert parse_service_account_key(_SECRET)["client_email"] == _KEY["client_email"]


@pytest.mark.parametrize(
    ("secret", "fragment"),
    [
        ("not json", "pasted whole"),
        ("[]", "service-account key file"),
        (json.dumps({**_KEY, "type": "authorized_user"}), "service-account key file"),
        (json.dumps({k: v for k, v in _KEY.items() if k != "private_key"}), "missing private_key"),
        (json.dumps({**_KEY, "client_email": ""}), "missing client_email"),
        (json.dumps({**_KEY, "token_uri": 7}), "missing token_uri"),
    ],
)
def test_parse_service_account_key_refuses_anything_else(secret: str, fragment: str) -> None:
    with pytest.raises(GcsKeyInvalidError, match=fragment) as exc:
        parse_service_account_key(secret)
    # The refusal never echoes the material it refused.
    assert "private" not in str(exc.value).lower() or "missing private_key" in str(exc.value)
    assert _KEY["client_email"] not in str(exc.value)


# ───────────────────────── test() connectivity ─────────────────────


class _FakeIterator:
    def __init__(self, items: list[Any], error: Exception | None = None) -> None:
        self._items = items
        self._error = error

    def __iter__(self) -> Any:
        if self._error is not None:
            raise self._error
        return iter(self._items)


class _FakeClient:
    instances: ClassVar[list[_FakeClient]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.list_calls: list[dict[str, Any]] = []
        self.closed = False
        self.error: Exception | None = None
        _FakeClient.instances.append(self)

    def list_blobs(self, bucket: str, **kwargs: Any) -> _FakeIterator:
        self.list_calls.append({"bucket": bucket, **kwargs})
        return _FakeIterator([object()], self.error)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> type[_FakeClient]:
    _FakeClient.instances = []
    monkeypatch.setattr(storage, "Client", _FakeClient)
    return _FakeClient


def test_test_lists_one_object_with_a_read_only_key(fake_client: type[_FakeClient]) -> None:
    GcsConnectionAdapter().test(dict(_CONFIG), _SECRET)
    (client,) = fake_client.instances
    assert client.kwargs["project"] == "dq-project"
    assert client.kwargs["client_options"] is None
    creds = client.kwargs["credentials"]
    assert creds.service_account_email == _KEY["client_email"]
    assert set(creds.scopes) == {READ_ONLY_SCOPE}
    assert client.list_calls == [
        {"bucket": "dataq-landing", "max_results": 1, "timeout": (10, 10), "retry": None}
    ]
    assert client.closed


def test_test_targets_an_emulator_endpoint_and_the_configured_project(
    fake_client: type[_FakeClient],
) -> None:
    GcsConnectionAdapter().test(
        {**_CONFIG, "endpoint_url": "http://fake-gcs:4443/", "project_id": "billing-proj"}, _SECRET
    )
    (client,) = fake_client.instances
    assert client.kwargs["client_options"] == {"api_endpoint": "http://fake-gcs:4443"}
    assert client.kwargs["project"] == "billing-proj"


def test_test_anonymous_presents_no_credential(fake_client: type[_FakeClient]) -> None:
    from google.auth.credentials import AnonymousCredentials

    GcsConnectionAdapter().test({"bucket": "public-data", "auth_type": "anonymous"}, None)
    (client,) = fake_client.instances
    assert isinstance(client.kwargs["credentials"], AnonymousCredentials)
    assert client.closed


def test_test_requires_a_secret_for_key_auth(fake_client: type[_FakeClient]) -> None:
    with pytest.raises(ValueError, match="credential is required"):
        GcsConnectionAdapter().test(dict(_CONFIG), None)
    assert fake_client.instances == []


def test_test_refuses_a_malformed_key_before_any_request(fake_client: type[_FakeClient]) -> None:
    with pytest.raises(GcsKeyInvalidError):
        GcsConnectionAdapter().test(dict(_CONFIG), "{}")
    assert fake_client.instances == []


def test_test_refuses_an_unreadable_private_key_without_quoting_it(
    fake_client: type[_FakeClient],
) -> None:
    # A truncated PEM: the header of a real key with no body.
    bad = json.dumps({**_KEY, "private_key": _KEY["private_key"].splitlines()[0] + "\nnope\n"})
    with pytest.raises(GcsKeyInvalidError, match="private_key could not be read") as exc:
        GcsConnectionAdapter().test(dict(_CONFIG), bad)
    assert "nope" not in str(exc.value)
    assert fake_client.instances == []


def test_test_propagates_a_listing_failure_and_still_closes(
    fake_client: type[_FakeClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    from google.api_core.exceptions import Forbidden

    original = fake_client.list_blobs

    def _forbidden(self: _FakeClient, bucket: str, **kwargs: Any) -> _FakeIterator:
        self.error = Forbidden("403 GET …: dq@… does not have storage.objects.list access")
        return original(self, bucket, **kwargs)

    monkeypatch.setattr(fake_client, "list_blobs", _forbidden)
    with pytest.raises(Forbidden):
        GcsConnectionAdapter().test(dict(_CONFIG), _SECRET)
    (client,) = fake_client.instances
    assert client.closed


# ───────────────────────── registry wiring ─────────────────────


def test_gcs_is_registered_as_a_flat_file_datasource() -> None:
    assert isinstance(registry.get_connection_adapter("gcs"), GcsConnectionAdapter)
    assert registry.destination_fields("gcs") == {"secret": ("endpoint_url", "auth_type")}
    assert "gcs" in registry.SAMPLING_CAPABLE_TYPES
    assert engines_for("gcs") == frozenset({"gx"})
    resolved = registry.resolve_target_shape("gcs", {"path": "raw/orders.csv"})
    assert (resolved.table, resolved.batch) == ("raw/orders.csv", None)
