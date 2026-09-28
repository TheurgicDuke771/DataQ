"""ADLS Gen2 connection adapter tests — config validation + the container probe."""

import time
from typing import Any, ClassVar

import azure.storage.blob as azblob
import pytest
from pydantic import ValidationError

from backend.app.datasources import adls
from backend.app.datasources.adls import AdlsConfig, AdlsConnectionAdapter, blob_service_client

_SAS_CONFIG = {
    "account_url": "https://acct.blob.core.windows.net",
    "container": "data",
    "auth_type": "sas",
}


# ───────────────────────── validate_config ─────────────────────────


def test_validate_config_accepts_sas_config() -> None:
    cfg = AdlsConnectionAdapter().validate_config(dict(_SAS_CONFIG))
    assert isinstance(cfg, AdlsConfig)
    assert cfg.container == "data"


def test_validate_config_defaults_auth_type_to_sas() -> None:
    cfg = AdlsConnectionAdapter().validate_config(
        {"account_url": "https://a.blob.core.windows.net", "container": "c"}
    )
    assert cfg.auth_type == "sas"


def test_validate_config_rejects_managed_identity_as_deferred() -> None:
    with pytest.raises(ValidationError, match="deferred to Week 7"):
        AdlsConnectionAdapter().validate_config({**_SAS_CONFIG, "auth_type": "managed_identity"})


def test_validate_config_rejects_non_http_account_url() -> None:
    with pytest.raises(ValidationError, match="http"):
        AdlsConnectionAdapter().validate_config({"account_url": "acct.blob", "container": "c"})


def test_validate_config_strips_trailing_slash() -> None:
    cfg = AdlsConnectionAdapter().validate_config(
        {"account_url": "https://a.blob.core.windows.net/", "container": "c"}
    )
    assert cfg.account_url == "https://a.blob.core.windows.net"


def test_validate_config_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        AdlsConnectionAdapter().validate_config({**_SAS_CONFIG, "region": "westeurope"})


# ───────────────────────── test() connectivity ─────────────────────


def test_test_reads_container_properties_with_sas(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

    class _FakeContainer:
        def get_container_properties(self) -> dict[str, str]:
            calls["props"] = True
            return {"name": "data"}

    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            calls["ctor"] = kwargs

        def get_container_client(self, name: str) -> _FakeContainer:
            calls["container"] = name
            return _FakeContainer()

        def close(self) -> None:
            calls["closed"] = True

    monkeypatch.setattr(azblob, "BlobServiceClient", _FakeClient)
    AdlsConnectionAdapter().test(dict(_SAS_CONFIG), "sas-token")  # no raise

    assert calls["ctor"]["account_url"] == "https://acct.blob.core.windows.net"
    assert calls["ctor"]["credential"] == "sas-token"
    assert calls["ctor"]["retry_total"] == 0
    assert calls["container"] == "data"
    assert calls["props"] is True
    assert calls["closed"] is True


def test_test_raises_and_closes_when_probe_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: dict[str, bool] = {}

    class _FakeContainer:
        def get_container_properties(self) -> None:
            raise RuntimeError("403 forbidden")

    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def get_container_client(self, name: str) -> _FakeContainer:
            return _FakeContainer()

        def close(self) -> None:
            closed["v"] = True

    monkeypatch.setattr(azblob, "BlobServiceClient", _FakeClient)
    with pytest.raises(RuntimeError, match="403"):
        AdlsConnectionAdapter().test(dict(_SAS_CONFIG), "sas-token")
    assert closed["v"] is True  # finally-closes even on failure


# ───────────────────── service principal (#1680) ─────────────────────

_TENANT = "0b1c2d3e-0000-4000-8000-000000000001"
_CLIENT = "0b1c2d3e-0000-4000-8000-000000000002"
_SP_CONFIG = {
    "account_url": "https://onelake.blob.fabric.microsoft.com",
    "container": "my-workspace",
    "auth_type": "service_principal",
    "tenant_id": _TENANT,
    "client_id": _CLIENT,
}


def test_validate_config_accepts_service_principal() -> None:
    cfg = AdlsConnectionAdapter().validate_config(dict(_SP_CONFIG))
    assert (cfg.auth_type, cfg.tenant_id, cfg.client_id) == ("service_principal", _TENANT, _CLIENT)


def test_validate_config_accepts_a_verified_domain_tenant() -> None:
    cfg = AdlsConnectionAdapter().validate_config(
        {**_SP_CONFIG, "tenant_id": "contoso.onmicrosoft.com"}
    )
    assert cfg.tenant_id == "contoso.onmicrosoft.com"


@pytest.mark.parametrize("missing", ["tenant_id", "client_id"])
def test_validate_config_service_principal_requires_tenant_and_client(missing: str) -> None:
    raw = {k: v for k, v in _SP_CONFIG.items() if k != missing}
    with pytest.raises(ValidationError, match=missing):
        AdlsConnectionAdapter().validate_config(raw)


@pytest.mark.parametrize(
    "tenant",
    ["", " ", "../other", "tenant/oauth2", "tenant?x=1", "tenant#f", "a b", "-leading", "é"],
)
def test_validate_config_rejects_a_tenant_that_could_reshape_the_token_url(tenant: str) -> None:
    """The tenant becomes a path segment of the Entra token URL."""
    with pytest.raises(ValidationError, match="tenant_id"):
        AdlsConnectionAdapter().validate_config({**_SP_CONFIG, "tenant_id": tenant})


@pytest.mark.parametrize("client", ["", "a b", "id/../x", "id?x", "id.with.dots"])
def test_validate_config_rejects_a_malformed_client_id(client: str) -> None:
    with pytest.raises(ValidationError, match="client_id"):
        AdlsConnectionAdapter().validate_config({**_SP_CONFIG, "client_id": client})


@pytest.mark.parametrize("field", ["tenant_id", "client_id"])
def test_validate_config_rejects_service_principal_fields_on_a_sas_connection(field: str) -> None:
    """A SAS connection would silently ignore them."""
    with pytest.raises(ValidationError, match="only to service_principal"):
        AdlsConnectionAdapter().validate_config({**_SAS_CONFIG, field: _TENANT})


class _FakeCredential:
    instances: ClassVar[list["_FakeCredential"]] = []
    #: Every token request that reached "Entra", as (scopes, options).
    requests: ClassVar[list[tuple[tuple[str, ...], Any]]] = []
    lifetime_s: ClassVar[float] = 3600.0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args, self.kwargs, self.closed = args, kwargs, False
        _FakeCredential.instances.append(self)

    def get_token_info(self, *scopes: str, options: Any = None) -> Any:
        from azure.core.credentials import AccessTokenInfo

        _FakeCredential.requests.append((scopes, options))
        n = len(_FakeCredential.requests)
        return AccessTokenInfo(f"token-{n}", int(time.time() + _FakeCredential.lifetime_s))

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _empty_token_cache() -> Any:
    adls._token_cache.clear()
    _FakeCredential.requests = []
    _FakeCredential.lifetime_s = 3600.0
    yield
    adls._token_cache.clear()


class _RecordingClient:
    """A BlobServiceClient double recording ctor kwargs and every data-plane call."""

    last: "_RecordingClient | None" = None
    walk_error: Exception | None = None

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.calls: list[str] = []
        self.closed = False
        _RecordingClient.last = self

    def get_container_client(self, name: str) -> Any:
        client = self

        class _Container:
            def get_container_properties(self) -> dict[str, str]:
                client.calls.append(f"props:{name}")
                return {}

            def walk_blobs(self, **kwargs: Any) -> Any:
                client.calls.append(f"walk:{sorted(kwargs.items())}")
                if _RecordingClient.walk_error is not None:
                    raise _RecordingClient.walk_error
                return iter([])

        return _Container()

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> type[_RecordingClient]:
    import azure.identity as azid

    _FakeCredential.instances = []
    _RecordingClient.last = None
    _RecordingClient.walk_error = None
    monkeypatch.setattr(azid, "ClientSecretCredential", _FakeCredential)
    monkeypatch.setattr(azblob, "BlobServiceClient", _RecordingClient)
    return _RecordingClient


def _last(fake: type[_RecordingClient]) -> _RecordingClient:
    assert fake.last is not None
    return fake.last


def test_factory_builds_a_client_secret_credential_for_service_principal(
    fake_sdk: type[_RecordingClient],
) -> None:
    client = blob_service_client(AdlsConfig.model_validate(_SP_CONFIG), "the-secret", retry_total=0)

    inner = _last(fake_sdk)
    wrapper = inner.kwargs["credential"]
    assert isinstance(wrapper, adls._CachedClientSecretCredential)  # never the raw secret string
    assert inner.kwargs["account_url"] == _SP_CONFIG["account_url"]
    wrapper.get_token_info("https://storage.azure.com/.default")
    (cred,) = _FakeCredential.instances
    assert cred.args == (_TENANT, _CLIENT, "the-secret")
    # The client's bounds also bound the token request.
    assert cred.kwargs == {"retry_total": 0}

    client.close()
    assert inner.closed and cred.closed


def test_factory_passes_the_sas_string_through_unchanged(fake_sdk: type[_RecordingClient]) -> None:
    client = blob_service_client(AdlsConfig.model_validate(_SAS_CONFIG), "sv=1&sig=x")
    assert _last(fake_sdk).kwargs["credential"] == "sv=1&sig=x"
    assert _FakeCredential.instances == []
    client.close()


def test_factory_closes_the_credential_even_if_closing_the_client_fails(
    fake_sdk: type[_RecordingClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    client = blob_service_client(AdlsConfig.model_validate(_SP_CONFIG), "s")
    _last(fake_sdk).kwargs["credential"].get_token_info("scope")

    def _boom() -> None:
        raise RuntimeError("transport already gone")

    monkeypatch.setattr(_last(fake_sdk), "close", _boom)
    with pytest.raises(RuntimeError, match="transport"):
        client.close()
    assert _FakeCredential.instances[0].closed


def test_factory_closes_the_credential_if_the_client_cannot_be_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import azure.identity as azid

    _FakeCredential.instances = []
    monkeypatch.setattr(azid, "ClientSecretCredential", _FakeCredential)

    def _refuse(**_: Any) -> None:
        raise ValueError("bad account url")

    monkeypatch.setattr(azblob, "BlobServiceClient", _refuse)
    with pytest.raises(ValueError, match="bad account url"):
        blob_service_client(AdlsConfig.model_validate(_SP_CONFIG), "s")
    # The credential is built only for a token, and none was requested.
    assert _FakeCredential.instances == []


def test_factory_refuses_an_unvalidated_auth_mode() -> None:
    """Never fall through to an anonymous client."""
    cfg = AdlsConfig.model_validate(_SP_CONFIG).model_copy(update={"auth_type": "managed_identity"})
    with pytest.raises(ValueError, match="unsupported ADLS auth_type"):
        blob_service_client(cfg, "s")


def test_test_with_service_principal_also_lists_one_entry(fake_sdk: type[_RecordingClient]) -> None:
    """Container properties pass on the control-plane `containers/read` a plain Contributor
    holds (live-verified 2026-09-27), so the probe must also touch the data plane.
    """
    AdlsConnectionAdapter().test(dict(_SP_CONFIG), "s")

    inner = _last(fake_sdk)
    assert inner.calls[0] == "props:my-workspace"
    # Delimited — OneLake 400s a flat listing of the workspace root.
    assert inner.calls[1] == "walk:[('delimiter', '/'), ('results_per_page', 1)]"
    assert inner.closed


def test_test_with_service_principal_fails_when_the_data_plane_refuses(
    fake_sdk: type[_RecordingClient],
) -> None:
    from azure.core.exceptions import HttpResponseError

    fake_sdk.walk_error = HttpResponseError(message="AuthorizationPermissionMismatch")
    with pytest.raises(HttpResponseError, match="AuthorizationPermissionMismatch"):
        AdlsConnectionAdapter().test(dict(_SP_CONFIG), "s")
    assert _last(fake_sdk).closed
    assert all(cred.closed for cred in _FakeCredential.instances)


def test_test_with_sas_does_not_list(fake_sdk: type[_RecordingClient]) -> None:
    """A read-only SAS without `l` keeps testing green — the SAS-authorized properties call
    is already a data-plane request.
    """
    AdlsConnectionAdapter().test(dict(_SAS_CONFIG), "sv=1&sig=x")
    assert _last(fake_sdk).calls == ["props:data"]


_SAS_WITH_EXPIRY = "sv=2024-01-01&se=2031-01-01T00:00:00Z&sig=abc"


def test_credential_expiry_reads_a_sas_including_a_legacy_config_without_auth_type() -> None:
    legacy = {"account_url": "https://a.blob.core.windows.net", "container": "c"}
    assert AdlsConnectionAdapter().credential_expiry(legacy, _SAS_WITH_EXPIRY) is not None
    assert AdlsConnectionAdapter().credential_expiry(dict(_SAS_CONFIG), _SAS_WITH_EXPIRY)


def test_credential_expiry_is_none_for_service_principal_even_for_a_sas_shaped_secret() -> None:
    """A client secret's expiry lives in Entra: None is "not readable", never "does not
    expire" — and a SAS-shaped string must not be misread as one.
    """
    assert AdlsConnectionAdapter().credential_expiry(dict(_SP_CONFIG), _SAS_WITH_EXPIRY) is None


def test_destination_fields_cover_every_field_that_steers_the_secret() -> None:
    assert set(AdlsConnectionAdapter.destination_fields["secret"]) == {
        "account_url",
        "auth_type",
        "tenant_id",
        "client_id",
    }


# ── #2127: tokens outlive the per-operation client ───────────────────────────

_SCOPE = "https://storage.azure.com/.default"


def _token(secret: str = "s", *, options: Any = None, **cfg: Any) -> Any:
    config = AdlsConfig.model_validate({**_SP_CONFIG, **cfg})
    client = blob_service_client(config, secret)
    try:
        return client.kwargs["credential"].get_token_info(_SCOPE, options=options)
    finally:
        client.close()


def test_a_second_client_for_the_same_principal_reuses_the_token(
    fake_sdk: type[_RecordingClient],
) -> None:
    first, second = _token(), _token()
    assert first.token == second.token == "token-1"
    assert len(_FakeCredential.requests) == 1
    # The second client never even built a credential of its own.
    assert len(_FakeCredential.instances) == 1


def test_a_rotated_secret_or_another_principal_misses_the_cache(
    fake_sdk: type[_RecordingClient],
) -> None:
    _token("old")
    _token("new")
    _token("new", client_id="22222222-2222-2222-2222-222222222222")
    assert len(_FakeCredential.requests) == 3


def test_a_token_near_expiry_is_refetched(fake_sdk: type[_RecordingClient]) -> None:
    _FakeCredential.lifetime_s = adls._TOKEN_REFRESH_MARGIN_S - 1
    _token()
    _token()
    assert len(_FakeCredential.requests) == 2


def test_a_claims_challenge_always_reaches_entra(fake_sdk: type[_RecordingClient]) -> None:
    _token()
    _token(options={"claims": '{"access_token":{}}'})
    assert len(_FakeCredential.requests) == 2
    assert _FakeCredential.requests[1][1] == {"claims": '{"access_token":{}}'}


def test_the_legacy_get_token_path_shares_the_cache(fake_sdk: type[_RecordingClient]) -> None:
    _token()
    client = blob_service_client(AdlsConfig.model_validate(_SP_CONFIG), "s")
    try:
        legacy = client.kwargs["credential"].get_token(_SCOPE)
    finally:
        client.close()
    assert legacy.token == "token-1"
    assert len(_FakeCredential.requests) == 1


def test_a_forked_child_starts_with_an_empty_cache_and_a_fresh_lock(
    fake_sdk: type[_RecordingClient],
) -> None:
    _token()
    parent_lock = adls._token_lock
    adls._reset_token_cache_in_child()
    assert adls._token_cache == {} and adls._token_lock is not parent_lock
    _token()
    assert len(_FakeCredential.requests) == 2


def test_the_cache_holds_no_secret(fake_sdk: type[_RecordingClient]) -> None:
    _token("plain-secret-value")
    assert "plain-secret-value" not in repr(adls._token_cache)


def test_the_real_blob_sdk_authenticates_two_clients_with_one_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through azure-core's own bearer-token policy, not a stubbed client: two operations, each
    on its own `BlobServiceClient`, send the same token after a single request to Entra."""
    import azure.identity as azid
    from azure.core.pipeline.transport import HttpResponse, HttpTransport

    _FakeCredential.instances = []
    monkeypatch.setattr(azid, "ClientSecretCredential", _FakeCredential)
    sent: list[str] = []

    class _Response(HttpResponse):  # type: ignore[misc]
        def __init__(self, request: Any) -> None:
            super().__init__(request, None)
            self.status_code = 200
            self.headers = {"x-ms-version": "2021-08-06"}
            self.reason = "OK"
            self.content_type = "application/xml"

        def body(self) -> bytes:
            return b""

    class _Transport(HttpTransport):  # type: ignore[misc]
        def send(self, request: Any, **_: Any) -> Any:
            sent.append(request.headers["Authorization"])
            return _Response(request)

        def open(self) -> None: ...
        def close(self) -> None: ...
        def __exit__(self, *_: Any) -> None: ...

    config = AdlsConfig.model_validate(_SP_CONFIG)
    for _ in range(2):
        client = blob_service_client(config, "s", transport=_Transport())
        try:
            client.get_container_client("c").get_container_properties()
        finally:
            client.close()
    assert sent == ["Bearer token-1", "Bearer token-1"]
    assert len(_FakeCredential.requests) == 1


def test_a_connection_test_always_presents_the_secret_to_entra(
    fake_sdk: type[_RecordingClient],
) -> None:
    """A cached token would keep a revoked or expired secret testing green for up to an hour."""
    _token()
    AdlsConnectionAdapter().test(dict(_SP_CONFIG), "s")
    client = fake_sdk.last
    assert client is not None
    client.kwargs["credential"].get_token_info(_SCOPE)
    assert len(_FakeCredential.requests) == 2
    # And the fresh token does not stand in for a cached one either way.
    assert _token().token == "token-1"
