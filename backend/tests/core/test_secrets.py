import contextlib
import json
import os
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import ClassVar
from unittest import mock

import httpx
import pytest
import structlog
from pydantic import ValidationError
from structlog.testing import capture_logs
from structlog.typing import EventDict

from backend.app.core import secrets
from backend.app.core.config import Settings, get_settings
from backend.app.core.secrets import (
    AzureKeyVaultStore,
    EnvSecretStore,
    OpenBaoSecretStore,
    SecretNotFoundError,
    SecretStoreUnavailableError,
    SecretWriteError,
    _build_store,
    _env_key,
    get_secret_store,
)

# ───────────────────────── EnvSecretStore ──────────────────────────


def test_env_key_normalises_dashes_and_case() -> None:
    assert _env_key("snowflake-uat-finance") == "KV_SECRET_SNOWFLAKE_UAT_FINANCE"
    assert _env_key("adf-prod") == "KV_SECRET_ADF_PROD"
    assert _env_key("UPPER-already") == "KV_SECRET_UPPER_ALREADY"


def test_env_store_returns_value_when_set(
    clean_kv_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KV_SECRET_SNOWFLAKE_UAT_FINANCE", "s3cr3t")
    assert EnvSecretStore().get("snowflake-uat-finance") == "s3cr3t"


def test_env_store_raises_when_missing(clean_kv_env: None) -> None:
    with pytest.raises(SecretNotFoundError, match="KV_SECRET_MISSING"):
        EnvSecretStore().get("missing")


def test_env_store_set_then_get_roundtrips(
    clean_kv_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Isolate writes to a throwaway copy so the new var doesn't leak across tests.
    monkeypatch.setattr(os, "environ", dict(os.environ))
    store = EnvSecretStore()
    store.set("conn-snowflake-dev-finance", "p@ss")
    assert store.get("conn-snowflake-dev-finance") == "p@ss"


def test_env_store_set_writes_normalised_key(
    clean_kv_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "environ", dict(os.environ))
    EnvSecretStore().set("conn-snowflake-dev-finance", "p@ss")
    assert os.environ["KV_SECRET_CONN_SNOWFLAKE_DEV_FINANCE"] == "p@ss"


# ───────────────────────── AzureKeyVaultStore ──────────────────────


def test_akv_store_lazy_client_not_built_on_init() -> None:
    """Constructing the store must not import or build any Azure SDK client."""
    store = AzureKeyVaultStore("https://example.vault.azure.net/")
    assert store._client is None


def test_akv_store_get_returns_value(monkeypatch: pytest.MonkeyPatch) -> None:
    store = AzureKeyVaultStore("https://example.vault.azure.net/")
    fake_client = SimpleNamespace(get_secret=lambda name: SimpleNamespace(value="vault-value"))
    monkeypatch.setattr(store, "_client_lazy", lambda: fake_client)
    assert store.get("snowflake-uat-finance") == "vault-value"


def test_akv_store_get_wraps_sdk_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AzureKeyVaultStore("https://example.vault.azure.net/")

    def _boom(name: str) -> None:
        raise RuntimeError("network down")

    fake_client = SimpleNamespace(get_secret=_boom)
    monkeypatch.setattr(store, "_client_lazy", lambda: fake_client)
    # NOT SecretNotFoundError: callers degrade on that ("no webhook configured"), so
    # a Key Vault outage would render as "nothing is set" across the workspace. This
    # store made that mistake from the start; ADR 0039 fixes it for both backends.
    with pytest.raises(SecretStoreUnavailableError, match="network down"):
        store.get("snowflake-uat-finance")
    assert not isinstance(SecretStoreUnavailableError("x"), SecretNotFoundError)


def test_akv_store_get_raises_when_secret_value_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AzureKeyVaultStore("https://example.vault.azure.net/")
    fake_client = SimpleNamespace(get_secret=lambda name: SimpleNamespace(value=None))
    monkeypatch.setattr(store, "_client_lazy", lambda: fake_client)
    with pytest.raises(SecretNotFoundError, match="has no value"):
        store.get("snowflake-uat-finance")


def test_akv_store_set_calls_set_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    store = AzureKeyVaultStore("https://example.vault.azure.net/")
    calls: list[tuple[str, str]] = []
    fake_client = SimpleNamespace(set_secret=lambda name, value: calls.append((name, value)))
    monkeypatch.setattr(store, "_client_lazy", lambda: fake_client)
    store.set("conn-snowflake-dev-finance", "p@ss")
    assert calls == [("conn-snowflake-dev-finance", "p@ss")]


def test_akv_store_set_wraps_sdk_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    store = AzureKeyVaultStore("https://example.vault.azure.net/")

    def _boom(name: str, value: str) -> None:
        raise RuntimeError("network down")

    fake_client = SimpleNamespace(set_secret=_boom)
    monkeypatch.setattr(store, "_client_lazy", lambda: fake_client)
    with pytest.raises(SecretWriteError, match="network down"):
        store.set("conn-snowflake-dev-finance", "p@ss")


class _StubCredential:
    """Stands in for DefaultAzureCredential — records construction and close.

    `close()` is real SDK surface (`ChainedTokenCredential.close` closes the
    transport session of each credential in the chain), so the stub carries it —
    otherwise the store's close path would be untestable for the production store.
    """

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _StubSecretClient:
    """Stands in for SecretClient — records ctor args and serves get/set."""

    instances: ClassVar[list["_StubSecretClient"]] = []

    def __init__(self, *, vault_url: str, credential: object) -> None:
        self.vault_url = vault_url
        self.credential = credential
        self.set_calls: list[tuple[str, str]] = []
        self.closed = False
        # Listing surface for the orphan sweep (#1059). `properties` is what the SDK
        # returns from `list_properties_of_secrets`; `list_raises` simulates an
        # outage, which the store must surface rather than answer with a short list.
        self.properties: list[SimpleNamespace] = []
        self.list_raises = False
        _StubSecretClient.instances.append(self)

    def close(self) -> None:
        self.closed = True

    def list_properties_of_secrets(self) -> list[SimpleNamespace]:
        if self.list_raises:
            raise RuntimeError("vault unreachable")
        return self.properties

    def get_secret(self, name: str) -> SimpleNamespace:
        return SimpleNamespace(value=f"value-of-{name}")

    def set_secret(self, name: str, value: str) -> None:
        self.set_calls.append((name, value))


@pytest.fixture()
def stub_azure_sdk(monkeypatch: pytest.MonkeyPatch) -> type[_StubSecretClient]:
    """Patch the real SDK classes so `_client_lazy`'s import branch runs for real.

    Unlike the tests above (which monkeypatch `_client_lazy` itself and so skip
    the branch entirely), these patch `azure.identity.DefaultAzureCredential` and
    `azure.keyvault.secrets.SecretClient` at module level — the in-function
    `from azure... import ...` then resolves to the stubs, exercising the whole
    lazy-construction path without any network. Discharges WEEK7 A4 (the 0%-cov
    tail of #169).
    """
    _StubSecretClient.instances = []
    monkeypatch.setattr("azure.identity.DefaultAzureCredential", _StubCredential)
    monkeypatch.setattr("azure.keyvault.secrets.SecretClient", _StubSecretClient)
    return _StubSecretClient


def test_akv_client_lazy_constructs_sdk_client_with_vault_url_and_credential(
    stub_azure_sdk: type[_StubSecretClient],
) -> None:
    store = AzureKeyVaultStore("https://example.vault.azure.net/")
    assert store.get("snowflake-uat-finance") == "value-of-snowflake-uat-finance"
    (client,) = stub_azure_sdk.instances
    assert client.vault_url == "https://example.vault.azure.net/"
    assert isinstance(client.credential, _StubCredential)


def test_akv_client_lazy_caches_client_across_calls(
    stub_azure_sdk: type[_StubSecretClient],
) -> None:
    store = AzureKeyVaultStore("https://example.vault.azure.net/")
    first = store._client_lazy()
    second = store._client_lazy()
    assert first is second
    assert len(stub_azure_sdk.instances) == 1


def test_akv_store_set_reaches_sdk_through_lazy_branch(
    stub_azure_sdk: type[_StubSecretClient],
) -> None:
    store = AzureKeyVaultStore("https://example.vault.azure.net/")
    store.set("conn-snowflake-dev-finance", "p@ss")
    (client,) = stub_azure_sdk.instances
    assert client.set_calls == [("conn-snowflake-dev-finance", "p@ss")]


# ───────────────────────── Factory + cache ─────────────────────────


def _settings(**overrides: object) -> object:
    """Hand-rolled stand-in for the states a REAL `Settings` cannot reach.

    Kept deliberately, and only for the backstop tests below (#1058 item 3). Those
    assert that `_build_store` still raises on an openbao config with no addr/token,
    or on `redis` — but `Settings` now rejects exactly those at construction, so a
    real model can never be put into that state. Using one here would silently test
    the validator instead of the backstop, i.e. assert nothing about `_build_store`.

    Every test that CAN use a real `Settings` now does (`_settings_from_env`), so
    drift between `config.py`'s field names/Literal and what `_build_store` reads is
    caught where it is catchable.
    """
    base: dict[str, object] = {
        "secret_store": "env",
        "azure_key_vault_url": None,
        "redis_url": "redis://localhost:6379/0",
        "openbao_addr": None,
        "openbao_token": None,
        "openbao_mount": "secret",
        "openbao_role_id": None,
        "openbao_secret_id": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _settings_from_env(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    """A REAL `Settings` built from env alone, ignoring any developer .env.app.

    Same construction as `_real_settings` below, but returns the model so the
    factory tests can feed it to `_build_store` — that pairing is what makes a
    renamed config field or a changed `Literal` fail a test instead of passing one.
    """
    for key in (
        "SECRET_STORE",
        "OPENBAO_ADDR",
        "OPENBAO_TOKEN",
        "OPENBAO_MOUNT",
        # Added with #1054. Omitting these let an ambient OPENBAO_ROLE_ID decide the
        # outcome of the validator tests — the environment answering for the test,
        # which is how the blank-role-id defect got through in the first place.
        "OPENBAO_ROLE_ID",
        "OPENBAO_SECRET_ID",
        "AZURE_KEY_VAULT_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_build_store_returns_env_store_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _build_store(_settings_from_env(monkeypatch))
    assert isinstance(store, EnvSecretStore)


def test_build_store_returns_akv_store_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _build_store(
        _settings_from_env(
            monkeypatch,
            SECRET_STORE="azure_key_vault",
            AZURE_KEY_VAULT_URL="https://example.vault.azure.net/",
        )
    )
    assert isinstance(store, AzureKeyVaultStore)


def test_build_store_raises_when_akv_url_missing() -> None:
    with pytest.raises(RuntimeError, match="requires AZURE_KEY_VAULT_URL"):
        _build_store(_settings(secret_store="azure_key_vault"))  # type: ignore[arg-type]


def test_build_store_returns_openbao_store_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _build_store(
        _settings_from_env(
            monkeypatch,
            SECRET_STORE="openbao",
            OPENBAO_ADDR="http://openbao:8200",
            OPENBAO_TOKEN="tok",
        )
    )
    assert isinstance(store, OpenBaoSecretStore)


def test_build_store_raises_when_openbao_addr_missing() -> None:
    with pytest.raises(RuntimeError, match="requires OPENBAO_ADDR"):
        _build_store(_settings(secret_store="openbao", openbao_token="tok"))  # type: ignore[arg-type]


def test_build_store_raises_when_no_openbao_credential_at_all() -> None:
    """Neither a static token nor an AppRole pair — the message must name BOTH ways
    out, or an operator moving to AppRole reads it as "you must use a token"."""
    with pytest.raises(RuntimeError, match="OPENBAO_TOKEN") as exc:
        _build_store(
            _settings(secret_store="openbao", openbao_addr="http://openbao:8200")  # type: ignore[arg-type]
        )
    assert "OPENBAO_ROLE_ID" in str(exc.value)


def test_build_store_accepts_approle_without_a_static_token() -> None:
    store = _build_store(
        _settings(  # type: ignore[arg-type]
            secret_store="openbao",
            openbao_addr="http://openbao:8200",
            openbao_role_id="role",
            openbao_secret_id="sid",
        )
    )
    assert isinstance(store, OpenBaoSecretStore)


def test_build_store_rejects_half_an_approle() -> None:
    """A partial AppRole must not silently fall back to the static token: an operator
    who set ROLE_ID meant to stop using it, and a quiet downgrade of a credential
    path is a security regression."""
    with pytest.raises(RuntimeError, match="must be set together"):
        _build_store(
            _settings(  # type: ignore[arg-type]
                secret_store="openbao",
                openbao_addr="http://openbao:8200",
                openbao_token="tok",
                openbao_role_id="role",
            )
        )


def test_build_store_passes_configured_mount_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-default mount must reach the store — a prod vault rarely uses `secret/`,
    and silently reading from the wrong mount looks exactly like a missing secret."""
    store = _build_store(
        _settings_from_env(
            monkeypatch,
            SECRET_STORE="openbao",
            OPENBAO_ADDR="http://openbao:8200",
            OPENBAO_TOKEN="tok",
            OPENBAO_MOUNT="dataq-kv",
        )
    )
    assert isinstance(store, OpenBaoSecretStore)
    assert store._path("data", "x") == "/v1/dataq-kv/data/x"


def test_build_store_redis_mode_raises_with_migration_path() -> None:
    """ADR 0039 removed the plaintext store. The failure must NAME the replacement —
    a bare pydantic Literal rejection would say what is valid but not what happened."""
    with pytest.raises(RuntimeError, match="ADR 0039") as exc:
        _build_store(_settings(secret_store="redis"))  # type: ignore[arg-type]
    assert "SECRET_STORE=openbao" in str(exc.value)


# ── startup validation (review finding: the "raises at startup" claim was false) ──
#
# These build the REAL `Settings` model, not the `SimpleNamespace` the factory tests
# use — so they catch drift between config.py's field names/Literal and the store,
# which a hand-rolled stand-in cannot see.


def _real_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    """Build Settings from env alone, ignoring any developer .env.app on disk.

    Env vars rather than kwargs: `Settings` runs `extra="forbid"`, and the
    case-insensitive mapping applies to the ENVIRONMENT, not to constructor
    arguments — so `Settings(SECRET_STORE=...)` is rejected as an unknown field.
    """
    for key in (
        "SECRET_STORE",
        "OPENBAO_ADDR",
        "OPENBAO_TOKEN",
        "OPENBAO_MOUNT",
        # Added with #1054. Omitting these let an ambient OPENBAO_ROLE_ID decide the
        # outcome of the validator tests — the environment answering for the test,
        # which is how the blank-role-id defect got through in the first place.
        "OPENBAO_ROLE_ID",
        "OPENBAO_SECRET_ID",
        "AZURE_KEY_VAULT_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    Settings(_env_file=None)


def test_settings_rejects_openbao_without_addr_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lazily-built stores fail on FIRST USE — in a Celery task, mid-run, reported as
    a connection failure. That is the #954 shape; config must fail at boot instead."""
    with pytest.raises(ValidationError, match="requires OPENBAO_ADDR"):
        _real_settings(monkeypatch, SECRET_STORE="openbao", OPENBAO_TOKEN="tok")


def test_settings_rejects_openbao_without_token_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="requires OPENBAO_TOKEN"):
        _real_settings(monkeypatch, SECRET_STORE="openbao", OPENBAO_ADDR="http://openbao:8200")


def test_settings_accepts_approle_without_a_static_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _real_settings(
        monkeypatch,
        SECRET_STORE="openbao",
        OPENBAO_ADDR="http://openbao:8200",
        OPENBAO_ROLE_ID="role",
        OPENBAO_SECRET_ID="sid",
    )  # must not raise


@pytest.mark.parametrize("half", ["OPENBAO_ROLE_ID", "OPENBAO_SECRET_ID"])
def test_settings_rejects_half_an_approle_at_startup(
    monkeypatch: pytest.MonkeyPatch, half: str
) -> None:
    """A partial AppRole must not silently fall back to the static token: an operator
    who set ROLE_ID meant to stop using it."""
    with pytest.raises(ValidationError, match="AppRole auth needs both"):
        _real_settings(
            monkeypatch,
            SECRET_STORE="openbao",
            OPENBAO_ADDR="http://openbao:8200",
            OPENBAO_TOKEN="tok",
            **{half: "x"},
        )


def test_settings_treats_blank_approle_values_as_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real pydantic path, which the SimpleNamespace factory stand-in cannot
    reproduce: blank env values arrive as `""`, and a token-only config that also
    carries the blank AppRole keys (as `.env.app.example` ships them) must validate."""
    _real_settings(
        monkeypatch,
        SECRET_STORE="openbao",
        OPENBAO_ADDR="http://openbao:8200",
        OPENBAO_TOKEN="tok",
        OPENBAO_ROLE_ID="",
        OPENBAO_SECRET_ID="",
    )  # must not raise


def test_settings_names_every_missing_openbao_value_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Collected, not short-circuited: an operator missing both should not fix one,
    re-run, and only then learn about the other."""
    with pytest.raises(ValidationError) as exc:
        _real_settings(monkeypatch, SECRET_STORE="openbao")
    assert "OPENBAO_ADDR" in str(exc.value)
    assert "OPENBAO_TOKEN" in str(exc.value)


def test_settings_rejects_whitespace_only_openbao_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """A blank-but-present value passes a bare truthiness check and then fails much
    later as "vault unreachable" — pointing at the network, not the env file."""
    with pytest.raises(ValidationError, match="requires OPENBAO_TOKEN"):
        _real_settings(
            monkeypatch,
            SECRET_STORE="openbao",
            OPENBAO_ADDR="http://openbao:8200",
            OPENBAO_TOKEN="   ",
        )


def test_settings_rejects_openbao_addr_without_a_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    """httpx raises UnsupportedProtocol (an HTTPError), so the store would report a
    one-word config typo as `openbao_unreachable` — a network diagnosis."""
    with pytest.raises(ValidationError, match="must start with http"):
        _real_settings(
            monkeypatch, SECRET_STORE="openbao", OPENBAO_ADDR="openbao:8200", OPENBAO_TOKEN="tok"
        )


def test_settings_rejects_an_empty_mount(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty mount builds `/v1//data/<name>`, which the vault 404s — i.e. every
    credential in the workspace reports as missing."""
    with pytest.raises(ValidationError, match="OPENBAO_MOUNT"):
        _real_settings(
            monkeypatch,
            SECRET_STORE="openbao",
            OPENBAO_ADDR="http://openbao:8200",
            OPENBAO_TOKEN="tok",
            OPENBAO_MOUNT="/",
        )


def test_settings_rejects_the_removed_redis_mode_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="ADR 0039") as exc:
        _real_settings(monkeypatch, SECRET_STORE="redis")
    assert "SECRET_STORE=openbao" in str(exc.value)


def test_settings_accepts_a_complete_openbao_config(monkeypatch: pytest.MonkeyPatch) -> None:
    _real_settings(
        monkeypatch, SECRET_STORE="openbao", OPENBAO_ADDR="http://openbao:8200", OPENBAO_TOKEN="tok"
    )


def test_settings_does_not_require_openbao_fields_in_other_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate must stay mode-scoped: a Key Vault deploy carries no OPENBAO_*."""
    _real_settings(monkeypatch, SECRET_STORE="env")
    _real_settings(
        monkeypatch,
        SECRET_STORE="azure_key_vault",
        AZURE_KEY_VAULT_URL="https://v.vault.azure.net/",
    )


# ───────────────────────── OpenBaoSecretStore ──────────────────────
#
# Driven through a real `httpx.Client` over `MockTransport`: the URL building,
# header encoding, status handling and JSON decoding under test are genuinely
# executed, and only the socket is faked. Stubbing `_client_lazy` instead would
# mock the very seam these tests exist to check.


def _bao(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    addr: str = "http://openbao:8200",
    mount: str = "secret",
) -> OpenBaoSecretStore:
    return OpenBaoSecretStore(
        addr,
        "s3cr3t-token",
        mount=mount,
        client=httpx.Client(base_url=addr, transport=httpx.MockTransport(handler)),
    )


def _events(logs: Sequence[Mapping[str, object]]) -> list[str]:
    """Event names captured by structlog, in order. `capture_logs` is used rather
    than `caplog` because the stdlib bridge is only installed by `configure_logging`
    at app startup, which a bare unit test never runs — asserting on `caplog.text`
    silently passes an empty string."""
    return [str(entry["event"]) for entry in logs]


@contextmanager
def _captured_secret_logs(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[EventDict]]:
    """`capture_logs`, with the module logger rebound INSIDE the capture.

    Necessary because `configure_logging()` sets `cache_logger_on_first_use=True`,
    and once some earlier test in the suite has run it, `secrets.log` caches its
    bound logger and bypasses the processors `capture_logs` installs. The capture
    then yields an EMPTY list and every assertion here passes vacuously — these
    tests were green in isolation and failed only in the full suite, which is the
    same "passes for the wrong reason" shape the project keeps hitting.
    """
    with capture_logs() as logs:
        monkeypatch.setattr(secrets, "log", structlog.get_logger("backend.app.core.secrets"))
        yield logs


def _kv_payload(value: object) -> dict[str, object]:
    """The KV v2 read envelope, as captured from openbao/openbao v2.6.1."""
    return {"data": {"data": {"value": value}, "metadata": {"version": 1}}}


def test_openbao_lazy_client_not_built_on_init() -> None:
    """Constructing the store must not open a connection."""
    store = OpenBaoSecretStore("http://openbao:8200", "tok")
    assert store._client is None


def test_openbao_get_returns_value() -> None:
    store = _bao(lambda _r: httpx.Response(200, json=_kv_payload("p@ss")))
    assert store.get("conn-1") == "p@ss"


def test_openbao_get_hits_the_kv_v2_data_path_with_the_token_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_kv_payload("v"))

    _bao(handler).get("snowflake-uat-finance")
    assert seen[0].url.path == "/v1/secret/data/snowflake-uat-finance"
    # OpenBao keeps Vault's header name; getting this wrong 403s every read.
    assert seen[0].headers["X-Vault-Token"] == "s3cr3t-token"


def test_openbao_get_quotes_a_name_containing_a_slash() -> None:
    """`secret_ref` is caller data. An unescaped `/` would retarget the read at a
    different KV path — silently reading someone else's secret, or a 404."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_kv_payload("v"))

    _bao(handler).get("evil/../other")
    # raw_path is what goes on the wire. `.url.path` DECODES %2F back to '/', so
    # asserting on it would still pass with the escaping removed.
    assert seen[0].url.raw_path == b"/v1/secret/data/evil%2F..%2Fother"


def test_openbao_get_uses_the_configured_mount() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_kv_payload("v"))

    _bao(handler, mount="dataq-kv").get("conn-1")
    assert seen[0].url.path == "/v1/dataq-kv/data/conn-1"


def test_openbao_get_raises_not_found_on_404() -> None:
    store = _bao(lambda _r: httpx.Response(404, json={"errors": []}))
    with pytest.raises(SecretNotFoundError, match="not set"):
        store.get("missing")


def test_openbao_get_raises_not_found_on_soft_deleted_secret() -> None:
    """KV v2 answers 404 for a soft-deleted secret too (verified against v2.6.1),
    so no body inspection is needed to tell 'deleted' from 'never existed'."""
    body = {"data": {"data": None, "metadata": {"deletion_time": "2026-07-26T00:00:00Z"}}}
    store = _bao(lambda _r: httpx.Response(404, json=body))
    with pytest.raises(SecretNotFoundError, match="not set"):
        store.get("soft-deleted")


def test_openbao_get_403_is_not_reported_as_a_missing_secret() -> None:
    """A dead/expired token must be distinguishable from an absent credential —
    the #954 failure where dead PATs had no visible state anywhere (ADR 0039 §6).

    The distinction must live in the exception TYPE, not the message: every caller
    branches on the class and none reads the text, so raising `SecretNotFoundError`
    here would make an admin page render "not set" and alert delivery skip silently
    during a vault outage.
    """
    store = _bao(lambda _r: httpx.Response(403, json={"errors": ["permission denied"]}))
    with pytest.raises(SecretStoreUnavailableError) as exc:
        store.get("conn-1")
    assert "token invalid, expired" in str(exc.value)
    # The load-bearing assertion: callers that degrade on "not found" must NOT catch it.
    assert not issubclass(SecretStoreUnavailableError, SecretNotFoundError)


def test_openbao_get_503_names_the_sealed_vault() -> None:
    store = _bao(lambda _r: httpx.Response(503, json={"errors": ["Vault is sealed"]}))
    with pytest.raises(SecretStoreUnavailableError, match="sealed or standby"):
        store.get("conn-1")


def test_openbao_get_403_logs_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exception reaches the caller, but only a LOG reaches the operator."""
    store = _bao(lambda _r: httpx.Response(403, json={"errors": ["permission denied"]}))
    with _captured_secret_logs(monkeypatch) as logs, pytest.raises(SecretStoreUnavailableError):
        store.get("conn-1")
    assert _events(logs) == ["openbao_permission_denied"]


def test_openbao_get_503_logs_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _bao(lambda _r: httpx.Response(503, json={"errors": ["Vault is sealed"]}))
    with _captured_secret_logs(monkeypatch) as logs, pytest.raises(SecretStoreUnavailableError):
        store.get("conn-1")
    # The vault ANSWERED — a different investigation from a refused connection.
    assert _events(logs) == ["openbao_server_error"]


def test_openbao_get_wraps_transport_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(SecretStoreUnavailableError, match="unreachable"):
        _bao(handler).get("conn-1")


def test_openbao_get_transport_error_logs_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with _captured_secret_logs(monkeypatch) as logs, pytest.raises(SecretStoreUnavailableError):
        _bao(handler).get("conn-1")
    assert _events(logs) == ["openbao_unreachable"]


def test_openbao_get_raises_when_field_absent() -> None:
    """A secret written by something other than DataQ (different field name) must
    fail loudly rather than return the wrong string."""
    store = _bao(lambda _r: httpx.Response(200, json={"data": {"data": {"password": "x"}}}))
    with pytest.raises(SecretNotFoundError, match="has no 'value' field"):
        store.get("foreign")


def test_openbao_get_non_json_200_is_an_availability_fault_not_a_foreign_secret() -> None:
    """A 200 whose body is not JSON means we are not talking to the vault at all —
    a proxy error page, a captive portal, another service on :8200. Reporting that
    as "not written by DataQ" sends the operator to the wrong layer entirely."""
    store = _bao(lambda _r: httpx.Response(200, content=b"<html>502 Bad Gateway</html>"))
    with pytest.raises(SecretStoreUnavailableError, match="non-JSON 200"):
        store.get("conn-1")


def test_openbao_get_raises_when_value_is_null() -> None:
    store = _bao(lambda _r: httpx.Response(200, json=_kv_payload(None)))
    with pytest.raises(SecretNotFoundError, match="has no value"):
        store.get("conn-1")


# ── the silent band (review finding): every non-success status must signal ──


@pytest.mark.parametrize(
    ("status", "why"),
    [
        (400, "what a KV **v1** mount answers for a versioned path"),
        (401, "what a gateway in front of the vault returns when its 403 never reaches us"),
        (429, "HCP rate-limiting under check-run load — every run resolves a credential"),
        (500, "the vault answered, but failed"),
        (502, "a proxy between us and the vault"),
    ],
)
def test_openbao_get_never_fails_silently(
    monkeypatch: pytest.MonkeyPatch, status: int, why: str
) -> None:
    """An earlier version logged only 403 and 5xx, leaving 400-499 completely silent —
    and that band is exactly where the likely misconfigurations live ({why})."""
    store = _bao(lambda _r: httpx.Response(status, json={"errors": ["nope"]}))
    with _captured_secret_logs(monkeypatch) as logs, pytest.raises(SecretStoreUnavailableError):
        store.get("conn-1")
    assert len(_events(logs)) == 1, f"HTTP {status} produced no operator signal"


@pytest.mark.parametrize("status", [400, 401, 429, 500, 502, 503])
def test_openbao_set_never_fails_silently(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    """The write path's half of ADR 0039 §6 had NO regression cover: deleting the
    warning from `set` left all 53 tests green (found by mutation in review)."""
    store = _bao(lambda _r: httpx.Response(status, json={"errors": ["nope"]}))
    with _captured_secret_logs(monkeypatch) as logs, pytest.raises(SecretWriteError):
        store.set("conn-1", "p@ss")
    assert len(_events(logs)) == 1, f"HTTP {status} on write produced no operator signal"


def test_openbao_set_transport_error_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with _captured_secret_logs(monkeypatch) as logs, pytest.raises(SecretWriteError):
        _bao(handler).set("conn-1", "p@ss")
    assert _events(logs) == ["openbao_unreachable"]


# ── a typo'd mount must not report every credential as missing ──


def _missing_mount_404() -> httpx.Response:
    """The shape openbao/openbao v2.6.1 returns for a route that does not exist —
    distinct from an absent secret's `{"errors": []}`."""
    return httpx.Response(
        404, json={"errors": ['no handler for route "typo/data/conn-1". route entry not found.']}
    )


def test_openbao_missing_mount_is_not_reported_as_a_missing_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`OPENBAO_MOUNT` is operator-settable and production vaults commonly mount
    per-team paths. One typo would otherwise report EVERY credential in the
    workspace as "not set" — the precise masquerade ADR 0039 §6 forbids."""
    store = _bao(lambda _r: _missing_mount_404(), mount="typo")
    with _captured_secret_logs(monkeypatch) as logs, pytest.raises(SecretStoreUnavailableError):
        store.get("conn-1")
    assert _events(logs) == ["openbao_mount_missing"]


def test_openbao_kv_v1_envelope_is_a_config_fault_not_a_foreign_secret() -> None:
    """A KV **v1** mount answers `{"data": {...}}` with no inner `data`. That is a
    misconfigured mount, not somebody else's secret, and saying otherwise sends the
    operator hunting for a secret that is sitting right there."""
    store = _bao(lambda _r: httpx.Response(200, json={"data": {"value": "x"}}))
    with pytest.raises(SecretStoreUnavailableError, match="KV v2 mount"):
        store.get("conn-1")


def test_openbao_set_posts_the_kv_v2_envelope() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": {"version": 1}})

    _bao(handler).set("conn-1", "p@ss")
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/v1/secret/data/conn-1"
    # KV v2 nests the map under "data" — a flat body writes nothing readable back.
    assert json.loads(seen[0].content) == {"data": {"value": "p@ss"}}


def test_openbao_set_raises_write_error_on_403() -> None:
    store = _bao(lambda _r: httpx.Response(403, json={"errors": ["permission denied"]}))
    with pytest.raises(SecretWriteError, match="token invalid, expired"):
        store.set("conn-1", "p@ss")


def test_openbao_set_raises_write_error_on_transport_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(SecretWriteError, match="vault unreachable"):
        _bao(handler).set("conn-1", "p@ss")


def test_openbao_set_then_get_roundtrips_through_a_fake_vault() -> None:
    """The #86 property: a value written through the store reads back through it,
    end-to-end over the real httpx stack, with only the socket faked."""
    vault: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        if request.method == "POST":
            vault[name] = json.loads(request.content)["data"]["value"]
            return httpx.Response(200, json={"data": {"version": 1}})
        if name not in vault:
            return httpx.Response(404, json={"errors": []})
        return httpx.Response(200, json=_kv_payload(vault[name]))

    store = _bao(handler)
    store.set("conn-1", "shared-secret")
    assert store.get("conn-1") == "shared-secret"


def test_get_secret_store_caches_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """The identity assertion alone passes for ANY cached object, so also pin the
    type (#1058).

    The `cache_clear()` is what makes the setenv mean anything, and it is required,
    not defensive. `get_settings()` is `lru_cache`d; the autouse `_reset_caches`
    clears it, but the autouse `stub_run_dispatch` then imports `run_dispatch` →
    `create_celery_app()` → `get_settings()`, repopulating it from the AMBIENT
    environment. That import happens once per process, so in a whole-file run the
    cache is empty here by luck of ordering and the setenv wins; run this test alone
    against a developer `.env.app` (which ships `SECRET_STORE=openbao`) and it loses.

    Without the clear, this test asserts what the developer's env file says rather
    than what the test says — green in CI, red alone, which is precisely the
    single-test loop the local-verification rule depends on.
    """
    monkeypatch.setenv("SECRET_STORE", "env")
    get_settings.cache_clear()
    first = get_secret_store()
    second = get_secret_store()
    assert first is second
    assert isinstance(first, EnvSecretStore)


def test_reset_secret_store_cache_closes_the_outgoing_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reset must release the outgoing store's pool, not just drop the reference
    (#1058). Asserted through the module-level singleton — the leak was in the reset,
    so testing `close()` directly would not have caught it."""
    closed: list[bool] = []
    monkeypatch.setattr(
        secrets, "_store_singleton", SimpleNamespace(close=lambda: closed.append(True))
    )
    secrets.reset_secret_store_cache()
    assert closed == [True]
    assert secrets._store_singleton is None


def test_reset_secret_store_cache_tolerates_a_store_without_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`close()` is duck-typed, not on the Protocol, so a store lacking it (every
    test double, and `EnvSecretStore`) must still reset rather than AttributeError."""
    monkeypatch.setattr(secrets, "_store_singleton", SimpleNamespace())
    secrets.reset_secret_store_cache()
    assert secrets._store_singleton is None


def test_openbao_close_releases_a_client_it_built() -> None:
    store = OpenBaoSecretStore("http://openbao:8200", "tok")
    client = store._client_lazy()
    store.close()
    assert client.is_closed
    # Cleared, not merely closed — otherwise a store used after a reset would keep
    # handing out a dead pool.
    assert store._client is None


def test_openbao_close_leaves_an_injected_client_attached_and_open() -> None:
    """An injected client belongs to its caller, so `close()` must be a full no-op.

    Both halves matter. Closing it would break a caller reusing the pool. But merely
    DETACHING it is worse: the store would then build a real
    `httpx.Client(base_url=addr)` on next use, so a MockTransport-backed store would
    silently start opening live sockets to the vault after any reset — and the reset
    runs from an autouse fixture. Asserting only `not is_closed` would miss that, so
    the association is pinned too."""
    injected = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200)))
    store = OpenBaoSecretStore("http://openbao:8200", "tok", client=injected)
    store.close()
    assert not injected.is_closed
    assert store._client is injected
    assert store._client_lazy() is injected  # still the mock, not a fresh real pool
    injected.close()


def test_openbao_close_is_idempotent_and_reusable() -> None:
    store = OpenBaoSecretStore("http://openbao:8200", "tok")
    store._client_lazy()
    store.close()
    store.close()  # must not raise on an already-closed store
    rebuilt = store._client_lazy()  # rebuilt for continued use
    assert not rebuilt.is_closed
    rebuilt.close()


# ── AppRole auth (#1054, ADR 0039 phase 2) ───────────────────────────────────


def _approle_store(handler: Callable[[httpx.Request], httpx.Response]) -> OpenBaoSecretStore:
    return OpenBaoSecretStore(
        "http://openbao:8200",
        None,
        role_id="role",
        secret_id="sid",
        client=httpx.Client(base_url="http://openbao:8200", transport=httpx.MockTransport(handler)),
    )


def test_approle_logs_in_lazily_and_uses_the_issued_token() -> None:
    """No login at construction — the store must stay cheap to build (it is created
    per process at import-adjacent time), and a vault that is down must not make
    building it fail."""
    seen: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers.get("X-Vault-Token")))
        if request.url.path == "/v1/auth/approle/login":
            return httpx.Response(
                200, json={"auth": {"client_token": "issued-token", "lease_duration": 3600}}
            )
        return httpx.Response(200, json={"data": {"data": {"value": "s3cr3t"}}})

    store = _approle_store(handler)
    assert seen == []  # nothing on construction
    assert store.get("conn-a") == "s3cr3t"
    assert seen[0][0] == "/v1/auth/approle/login"
    # The KV read carries the token the login issued, not a static one.
    assert seen[1] == ("/v1/secret/data/conn-a", "issued-token")


def test_approle_token_is_reused_across_calls() -> None:
    """One login per token lifetime, not one per request: every check run resolves a
    credential, so a login on the hot path would double the vault traffic."""
    logins = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal logins
        if request.url.path == "/v1/auth/approle/login":
            logins += 1
            return httpx.Response(200, json={"auth": {"client_token": "t", "lease_duration": 3600}})
        return httpx.Response(200, json={"data": {"data": {"value": "v"}}})

    store = _approle_store(handler)
    store.get("a")
    store.get("b")
    store.get("c")
    assert logins == 1


def test_approle_renews_inside_the_margin_while_still_within_lease() -> None:
    """The margin's actual semantics: token NOT yet expired, but inside the renewal
    window, must renew.

    Driven by a fake `time.monotonic`, because the previous version of this test used
    `lease_duration: 30` against a 60s margin — which makes the token already expired
    at issue, so it could not distinguish "renews inside the margin" from "renews only
    once expired". It passed without testing the thing it named.
    """
    clock = [1000.0]
    logins = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal logins
        if request.url.path == "/v1/auth/approle/login":
            logins += 1
            return httpx.Response(
                200, json={"auth": {"client_token": f"t{logins}", "lease_duration": 600}}
            )
        return httpx.Response(200, json={"data": {"data": {"value": "v"}}})

    with mock.patch("backend.app.core.secrets.time.monotonic", lambda: clock[0]):
        store = _approle_store(handler)
        store.get("a")
        assert logins == 1
        # 500s in: still 100s of lease left, but inside the 60s margin? No — renewal
        # is due at 1000 + max(600-60, 300, 1) = 1540.
        after_first = logins
        clock[0] = 1500.0
        store.get("b")
        assert logins == after_first, "renewed too early"
        clock[0] = 1545.0
        store.get("c")
        assert logins == 2, "did not renew inside the margin"


def test_approle_short_lease_does_not_log_in_on_every_request() -> None:
    """A hardened AppRole with `token_ttl=60s` is a reasonable production setting, and
    a bare `lease - margin` would put its expiry in the past at issue: two round-trips
    per credential resolution, forever, on the documented hot path. The lease is
    floored at half its length so a short token renews once mid-life instead."""
    clock = [1000.0]
    logins = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal logins
        if request.url.path == "/v1/auth/approle/login":
            logins += 1
            return httpx.Response(
                200, json={"auth": {"client_token": f"t{logins}", "lease_duration": 60}}
            )
        return httpx.Response(200, json={"data": {"data": {"value": "v"}}})

    with mock.patch("backend.app.core.secrets.time.monotonic", lambda: clock[0]):
        store = _approle_store(handler)
        store.get("a")
        store.get("b")
        store.get("c")
    assert logins == 1


@pytest.mark.parametrize(
    ("lease", "renews"),
    [
        (3600, True),
        (3600.0, True),  # a float from a proxy must not disable renewal silently
        ("3600", True),  # nor a numeric string
        (0, False),  # "never expires"
        (True, False),  # bool is an int in Python — must NOT become a 1s lease
        (10**19, True),  # must not raise OverflowError out of get/set/delete
        ("nonsense", False),
        (None, False),
    ],
)
def test_lease_duration_crosses_a_driver_boundary(lease: object, renews: bool) -> None:
    """`lease_duration` is whatever the SERVER put in the JSON. Every fixture in the
    first version of this feature handed in a clean int, which is the #953/#823 shape:
    the test asserts our model rather than the driver's."""
    store = OpenBaoSecretStore("http://openbao:8200", None, role_id="r", secret_id="s")
    remaining = store._lease_seconds(lease)
    assert (remaining is not None) is renews


def test_approle_never_re_logs_in_for_a_non_expiring_token() -> None:
    """`lease_duration: 0` is how a vault reports a token that does not expire.
    Treating that as "stale" would turn it into a login storm."""
    logins = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal logins
        if request.url.path == "/v1/auth/approle/login":
            logins += 1
            return httpx.Response(200, json={"auth": {"client_token": "t", "lease_duration": 0}})
        return httpx.Response(200, json={"data": {"data": {"value": "v"}}})

    store = _approle_store(handler)
    store.get("a")
    store.get("b")
    assert logins == 1


def test_approle_re_logs_in_once_when_a_CACHED_token_is_revoked() -> None:
    """The whole point of #1054: a token revoked mid-flight used to 403 every
    subsequent request until the process was RESTARTED.

    The first call must SUCCEED so a token is cached — a 403 on a freshly-minted token
    means the policy is wrong, not the token, and is deliberately not retried.
    """
    logins = 0
    reads = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal logins, reads
        if request.url.path == "/v1/auth/approle/login":
            logins += 1
            return httpx.Response(
                200, json={"auth": {"client_token": f"t{logins}", "lease_duration": 3600}}
            )
        reads += 1
        if reads == 2:  # the cached token has been revoked out from under us
            return httpx.Response(403, json={"errors": ["permission denied"]})
        return httpx.Response(200, json={"data": {"data": {"value": "recovered"}}})

    store = _approle_store(handler)
    assert store.get("a") == "recovered"  # caches t1
    assert store.get("b") == "recovered"  # 403 -> re-login -> retry succeeds
    assert logins == 2
    assert reads == 3


def test_approle_does_not_retry_a_403_on_a_freshly_minted_token() -> None:
    """A 403 on a token minted for THIS request means the AppRole's policy does not
    cover the path — re-logging in buys nothing, and on a least-privilege vault it
    would cost one extra login per secret for the whole of an orphan sweep."""
    logins = 0
    reads = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal logins, reads
        if request.url.path == "/v1/auth/approle/login":
            logins += 1
            return httpx.Response(200, json={"auth": {"client_token": "t", "lease_duration": 3600}})
        reads += 1
        return httpx.Response(403, json={"errors": ["permission denied"]})

    store = _approle_store(handler)
    with pytest.raises(SecretStoreUnavailableError):
        store.get("a")
    assert logins == 1  # NOT 2 — no wasted re-login
    assert reads == 1


def test_approle_403_relogin_is_compare_and_swap_under_concurrency() -> None:
    """N threads whose CACHED token is revoked together must not each log in.

    The scenario matters: an earlier version of this test raced threads on a *fresh*
    token, where the fresh-token suppression already prevents a stampede — so it
    passed with CAS reverted and proved nothing. A mutation check caught that. The
    token must be cached and then revoked, which is the only path that reaches the
    compare-and-swap at all.

    With an unconditional `_forget_token()`, each thread discards a peer's
    freshly-obtained token and logs in again — one per thread.
    """
    logins = 0
    revoked = False
    lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal logins
        if request.url.path == "/v1/auth/approle/login":
            with lock:
                logins += 1
                issued = logins
            return httpx.Response(
                200, json={"auth": {"client_token": f"t{issued}", "lease_duration": 3600}}
            )
        if revoked:
            return httpx.Response(403, json={"errors": ["permission denied"]})
        return httpx.Response(200, json={"data": {"data": {"value": "v"}}})

    store = _approle_store(handler)
    store.get("warm")  # caches t1 — now every thread below takes the non-fresh path
    assert logins == 1
    revoked = True

    threads = [threading.Thread(target=_ignore_unavailable(store)) for _ in range(8)]
    barrier = threading.Barrier(len(threads))
    store._test_barrier = barrier  # type: ignore[attr-defined]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 1 warm-up login + a small constant. Unconditional clearing gives one per thread.
    assert logins <= 3, f"login stampede: {logins} logins for {len(threads)} threads"


def _ignore_unavailable(store: OpenBaoSecretStore) -> Callable[[], None]:
    def run() -> None:
        barrier = getattr(store, "_test_barrier", None)
        if barrier is not None:
            barrier.wait()
        with contextlib.suppress(SecretStoreUnavailableError):
            store.get("a")

    return run


def test_static_token_mode_does_not_retry_a_403() -> None:
    """With no AppRole there is nothing to re-acquire, so a 403 is the operator's
    answer and must surface unchanged rather than doubling every failing request."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(403, json={"errors": ["permission denied"]})

    store = OpenBaoSecretStore(
        "http://openbao:8200",
        "static",
        client=httpx.Client(base_url="http://openbao:8200", transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(SecretStoreUnavailableError):
        store.get("a")
    assert calls == 1


def test_blank_approle_env_does_not_engage_approle_mode() -> None:
    """`.env.app.example` ships these keys BLANK and `setup.sh` copies it verbatim,
    and pydantic-settings resolves a present-but-empty value to `""`, not `None`.

    Treating `""` as "AppRole configured" made the process boot clean and then fail
    EVERY secret operation at runtime — the "dies mid-run in a worker" failure the
    ADR 0039 startup validator exists to prevent, reintroduced one layer down. The
    `SimpleNamespace` factory stand-in cannot produce `""` the way real pydantic does,
    which is exactly how this got through (#124's "missed it by construction" shape).
    """
    for blank in ("", "   "):
        store = OpenBaoSecretStore(
            "http://openbao:8200", "static-token", role_id=blank, secret_id=blank
        )
        assert store._role_id is None
        assert store._current_token() == ("static-token", False)


def test_delete_stays_fail_soft_when_the_login_fails() -> None:
    """`delete`'s fail-soft contract is depended on BY NAME at three call sites — the
    post-commit cleanup in connection_service, the #1059 purge loop, and
    notification_service. A login failure arrives as `SecretStoreUnavailableError`,
    not an httpx error, so it would sail past the old handler and 500 a delete whose
    row is already committed."""
    store = _approle_store(lambda _r: httpx.Response(503))
    store.delete("conn-a")  # must not raise


def test_set_still_raises_secret_write_error_when_the_login_fails() -> None:
    """`set` promises `SecretWriteError`, which connection_service maps to a 502
    `ConnectionSecretWriteError`. A different type bypasses that mapping and becomes
    a bare 500."""
    store = _approle_store(lambda _r: httpx.Response(503))
    with pytest.raises(SecretWriteError):
        store.set("conn-a", "v")


def test_created_at_degrades_to_none_when_the_login_fails() -> None:
    """The documented degradation is "unknown age", which the sweep reads as "too
    young to purge". Letting the login failure escape would abort the whole sweep."""
    store = _approle_store(lambda _r: httpx.Response(503))
    assert store._created_at("conn-a") is None


def test_failed_login_never_logs_the_response_body() -> None:
    """The NON-200 path is the one that matters: a login error body can echo the
    submitted secret_id back. The previous version of this test exercised only the
    200 path, so adding `body=response.text` to the warning would have failed
    nothing."""
    canary = "SECRET-ID-CANARY-8f3a9c"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"errors": [f"invalid secret_id {canary}"]})

    store = OpenBaoSecretStore(
        "http://openbao:8200",
        None,
        role_id="role",
        secret_id=canary,
        client=httpx.Client(base_url="http://openbao:8200", transport=httpx.MockTransport(handler)),
    )
    with capture_logs() as logs:
        with pytest.raises(SecretStoreUnavailableError) as exc:
            store.get("conn-a")
    # Not in the log, and not in the exception message either — that ends up in
    # tracebacks and error responses.
    assert canary not in json.dumps(logs)
    assert canary not in str(exc.value)


def test_approle_login_failure_is_unavailable_not_missing() -> None:
    """A login that cannot complete is an outage or a bad credential. Reporting it as
    `SecretNotFoundError` would make callers degrade silently — ADR 0039 decision 6."""
    store = _approle_store(lambda _r: httpx.Response(400, json={"errors": ["invalid role"]}))
    with pytest.raises(SecretStoreUnavailableError):
        store.get("a")


def test_approle_login_never_logs_the_token_or_the_body() -> None:
    """The login body can echo the submitted secret_id on some error paths, and the
    response carries a live token. Neither may reach the log."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/auth/approle/login":
            return httpx.Response(
                200, json={"auth": {"client_token": "super-secret-token", "lease_duration": 60}}
            )
        return httpx.Response(200, json={"data": {"data": {"value": "v"}}})

    with capture_logs() as logs:
        _approle_store(handler).get("a")
    rendered = json.dumps(logs)
    assert "super-secret-token" not in rendered
    assert "sid" not in json.dumps(
        [entry for entry in logs if entry.get("event") != "openbao_approle_login"]
    )


# ── listing, for the orphan sweep (#1059) ─────────────────────────────────────


def test_vault_timestamp_parses_nanosecond_precision() -> None:
    """Characterisation test: the REAL payload shape, nine fractional digits.

    Kept even though Python 3.13's `fromisoformat` handles it natively — that is a
    property of the pinned interpreter, not of our code, and this is what would fail
    if the pin moved or the parser were reimplemented. It is here because the
    opposite belief (that `fromisoformat` raises on nanoseconds) produced a
    truncation branch that a mutation check then proved to be dead code.
    """
    parsed = secrets._parse_vault_timestamp("2026-07-27T04:53:23.123456789Z")
    assert parsed == datetime(2026, 7, 27, 4, 53, 23, 123456, tzinfo=UTC)


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "not-a-time", None, 12345, "2026-13-45T99:99:99Z"],
)
def test_vault_timestamp_returns_none_rather_than_raising(raw: object) -> None:
    """None is the SAFE direction: the sweep reads an unknown age as "too young to
    purge", so a malformed timestamp can never cause a delete."""
    assert secrets._parse_vault_timestamp(raw) is None


def test_vault_timestamp_assumes_utc_when_naive() -> None:
    parsed = secrets._parse_vault_timestamp("2026-07-27T04:53:23")
    assert parsed is not None and parsed.tzinfo is UTC


def test_openbao_list_secrets_returns_names_and_creation_times() -> None:
    """Drives the real request/response stack: LIST for names, then one metadata GET
    per name for `created_time`."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("list") == "true":
            return httpx.Response(200, json={"data": {"keys": ["conn-a", "nested/"]}})
        return httpx.Response(
            200, json={"data": {"created_time": "2026-07-27T04:53:23.123456789Z"}}
        )

    store = OpenBaoSecretStore(
        "http://openbao:8200",
        "tok",
        client=httpx.Client(base_url="http://openbao:8200", transport=httpx.MockTransport(handler)),
    )
    listed = store.list_secrets()
    # The trailing-slash entry is a KV v2 "directory", not a secret DataQ wrote.
    assert [info.name for info in listed] == ["conn-a"]
    assert listed[0].created_at == datetime(2026, 7, 27, 4, 53, 23, 123456, tzinfo=UTC)


def test_openbao_list_secrets_treats_an_empty_mount_as_empty_not_an_error() -> None:
    """KV v2 answers 404 with no errors for a mount holding nothing."""
    store = OpenBaoSecretStore(
        "http://openbao:8200",
        "tok",
        client=httpx.Client(
            base_url="http://openbao:8200",
            transport=httpx.MockTransport(lambda _r: httpx.Response(404, json={"errors": []})),
        ),
    )
    assert store.list_secrets() == []


def test_openbao_list_secrets_raises_on_a_missing_mount() -> None:
    """A typo'd OPENBAO_MOUNT must not read as "no secrets" — to a sweep that means
    "everything is an orphan"."""
    store = OpenBaoSecretStore(
        "http://openbao:8200",
        "tok",
        client=httpx.Client(
            base_url="http://openbao:8200",
            transport=httpx.MockTransport(
                lambda _r: httpx.Response(404, json={"errors": ["no handler for route"]})
            ),
        ),
    )
    with pytest.raises(SecretStoreUnavailableError):
        store.list_secrets()


def test_openbao_list_secrets_raises_when_the_vault_is_sealed() -> None:
    store = OpenBaoSecretStore(
        "http://openbao:8200",
        "tok",
        client=httpx.Client(
            base_url="http://openbao:8200",
            transport=httpx.MockTransport(lambda _r: httpx.Response(503)),
        ),
    )
    with pytest.raises(SecretStoreUnavailableError):
        store.list_secrets()


def test_openbao_created_at_degrades_to_none_on_a_metadata_failure() -> None:
    """Per-name metadata failure must NOT fail the whole listing — but the secret
    then has an unknown age, which the sweep treats as too young to purge."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("list") == "true":
            return httpx.Response(200, json={"data": {"keys": ["conn-a"]}})
        return httpx.Response(500)

    store = OpenBaoSecretStore(
        "http://openbao:8200",
        "tok",
        client=httpx.Client(base_url="http://openbao:8200", transport=httpx.MockTransport(handler)),
    )
    (info,) = store.list_secrets()
    assert info.created_at is None


def test_akv_list_secrets_reads_properties_without_fetching_values(
    stub_azure_sdk: type[_StubSecretClient],
) -> None:
    """`list_properties_of_secrets`, never `get_secret`: a sweep has no business
    pulling every credential in the workspace through this process."""
    created = datetime(2026, 5, 1, tzinfo=UTC)
    store = AzureKeyVaultStore("https://example.vault.azure.net/")
    store._client_lazy()
    # Off the stub registry, not `store._client`: the attribute is declared
    # `SecretClient | None`, and the real type has no `properties` hook.
    (client,) = stub_azure_sdk.instances
    client.properties = [
        SimpleNamespace(name="conn-a", created_on=created),
        SimpleNamespace(name=None, created_on=created),
    ]
    listed = store.list_secrets()
    assert [(i.name, i.created_at) for i in listed] == [("conn-a", created)]


def test_akv_list_secrets_raises_rather_than_returning_a_partial_list(
    stub_azure_sdk: type[_StubSecretClient],
) -> None:
    store = AzureKeyVaultStore("https://example.vault.azure.net/")
    store._client_lazy()
    (client,) = stub_azure_sdk.instances
    client.list_raises = True
    with pytest.raises(SecretStoreUnavailableError):
        store.list_secrets()


def test_akv_close_releases_both_the_client_and_the_credential(
    stub_azure_sdk: type[_StubSecretClient],
) -> None:
    """The PRODUCTION store (ADR 0024 runs `azure_key_vault`) must close BOTH.

    They hold separate transport sessions: `SecretClient.close()` frees only its own
    pipeline, while `DefaultAzureCredential` opens one per credential in its chain.
    Closing just the client leaves the token-acquisition sockets open — the same leak
    #1058 is about, half-fixed.
    """
    store = AzureKeyVaultStore("https://example.vault.azure.net/")
    store._client_lazy()
    # Read the client off the stub registry, not `store._client`: the attribute is
    # declared `SecretClient | None`, whose real type has no `closed` flag.
    (client,) = stub_azure_sdk.instances
    credential = store._credential
    store.close()
    assert client.closed
    assert credential is not None and credential.closed
    assert store._client is None and store._credential is None


def test_akv_close_is_idempotent_and_rebuilds(stub_azure_sdk: type[_StubSecretClient]) -> None:
    store = AzureKeyVaultStore("https://example.vault.azure.net/")
    store._client_lazy()
    store.close()
    store.close()  # no second close, no AttributeError on the cleared handles
    store._client_lazy()
    assert len(stub_azure_sdk.instances) == 2  # a genuinely fresh client


def test_reset_secret_store_cache_rebuilds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_STORE", "env")
    first = get_secret_store()
    secrets.reset_secret_store_cache()
    second = get_secret_store()
    assert first is not second


# ───────────────────────── delete (#372) ───────────────────────────


def test_env_store_delete_removes_var(clean_kv_env: None) -> None:
    store = EnvSecretStore()
    store.set("conn-x", "v")
    store.delete("conn-x")
    with pytest.raises(SecretNotFoundError):
        store.get("conn-x")


def test_env_store_delete_missing_is_noop(clean_kv_env: None) -> None:
    EnvSecretStore().delete("never-set")  # idempotent — must not raise


def test_akv_store_delete_calls_begin_delete_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    store = AzureKeyVaultStore("https://example.vault.azure.net/")
    calls: list[str] = []
    monkeypatch.setattr(
        store, "_client_lazy", lambda: SimpleNamespace(begin_delete_secret=calls.append)
    )
    store.delete("conn-x")
    assert calls == ["conn-x"]


def test_akv_store_delete_swallows_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    from azure.core.exceptions import ResourceNotFoundError

    store = AzureKeyVaultStore("https://example.vault.azure.net/")

    def _gone(name: str) -> None:
        raise ResourceNotFoundError("already deleted")

    monkeypatch.setattr(store, "_client_lazy", lambda: SimpleNamespace(begin_delete_secret=_gone))
    store.delete("conn-x")  # clean no-op — must not raise


def test_akv_store_delete_fails_soft_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    store = AzureKeyVaultStore("https://example.vault.azure.net/")

    def _boom(name: str) -> None:
        raise RuntimeError("kv down")

    monkeypatch.setattr(store, "_client_lazy", lambda: SimpleNamespace(begin_delete_secret=_boom))
    store.delete("conn-x")  # fail-soft: logged, never raised (#372)


def test_openbao_delete_targets_the_metadata_path() -> None:
    """Must purge every version via `metadata/`, NOT soft-delete via `data/`: the
    caller is orphan cleanup after a connection was deleted, and a recoverable
    warehouse credential behind a deleted entity is the wrong default (ADR 0039 §7)."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    _bao(handler).delete("conn-1")
    assert seen[0].method == "DELETE"
    assert seen[0].url.path == "/v1/secret/metadata/conn-1"


def test_openbao_delete_of_absent_secret_is_a_clean_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KV v2 answers 204 even for a name that never existed — nothing to log."""
    store = _bao(lambda _r: httpx.Response(204))
    with _captured_secret_logs(monkeypatch) as logs:
        store.delete("never-existed")
        # Positive control: this is the one assertion in the file whose PASS would
        # otherwise be indistinguishable from a capture that records nothing at all.
        secrets.log.warning("canary")
    assert _events(logs) == ["canary"]


def test_openbao_delete_fails_soft_on_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with _captured_secret_logs(monkeypatch) as logs:
        _bao(handler).delete("conn-1")  # fail-soft: logged, never raised (#372)
    assert _events(logs) == ["openbao_unreachable", "secret_delete_failed"]


def test_openbao_delete_fails_soft_on_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _bao(lambda _r: httpx.Response(403, json={"errors": ["permission denied"]}))
    with _captured_secret_logs(monkeypatch) as logs:
        store.delete("conn-1")  # fail-soft: an entity delete must not 500 on this
    # A connection delete is often the FIRST vault call after a token expires, so it
    # must emit the dead-token event an operator alerts on — not only the generic one.
    assert _events(logs) == ["openbao_permission_denied", "secret_delete_failed"]
    assert logs[1]["credential_still_present"] is True  # the purge did NOT happen
