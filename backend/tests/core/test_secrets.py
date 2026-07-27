import json
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from types import SimpleNamespace
from typing import ClassVar

import httpx
import pytest
import structlog
from pydantic import ValidationError
from structlog.testing import capture_logs
from structlog.typing import EventDict

from backend.app.core import secrets
from backend.app.core.config import Settings
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
    """Stands in for DefaultAzureCredential — records that it was constructed."""


class _StubSecretClient:
    """Stands in for SecretClient — records ctor args and serves get/set."""

    instances: ClassVar[list["_StubSecretClient"]] = []

    def __init__(self, *, vault_url: str, credential: object) -> None:
        self.vault_url = vault_url
        self.credential = credential
        self.set_calls: list[tuple[str, str]] = []
        _StubSecretClient.instances.append(self)

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
    base: dict[str, object] = {
        "secret_store": "env",
        "azure_key_vault_url": None,
        "redis_url": "redis://localhost:6379/0",
        "openbao_addr": None,
        "openbao_token": None,
        "openbao_mount": "secret",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_build_store_returns_env_store_by_default() -> None:
    store = _build_store(_settings())  # type: ignore[arg-type]
    assert isinstance(store, EnvSecretStore)


def test_build_store_returns_akv_store_when_configured() -> None:
    store = _build_store(
        _settings(
            secret_store="azure_key_vault",
            azure_key_vault_url="https://example.vault.azure.net/",
        )  # type: ignore[arg-type]
    )
    assert isinstance(store, AzureKeyVaultStore)


def test_build_store_raises_when_akv_url_missing() -> None:
    with pytest.raises(RuntimeError, match="requires AZURE_KEY_VAULT_URL"):
        _build_store(_settings(secret_store="azure_key_vault"))  # type: ignore[arg-type]


def test_build_store_returns_openbao_store_when_configured() -> None:
    store = _build_store(
        _settings(
            secret_store="openbao",
            openbao_addr="http://openbao:8200",
            openbao_token="tok",
        )  # type: ignore[arg-type]
    )
    assert isinstance(store, OpenBaoSecretStore)


def test_build_store_raises_when_openbao_addr_missing() -> None:
    with pytest.raises(RuntimeError, match="requires OPENBAO_ADDR"):
        _build_store(_settings(secret_store="openbao", openbao_token="tok"))  # type: ignore[arg-type]


def test_build_store_raises_when_openbao_token_missing() -> None:
    with pytest.raises(RuntimeError, match="requires OPENBAO_TOKEN"):
        _build_store(
            _settings(secret_store="openbao", openbao_addr="http://openbao:8200")  # type: ignore[arg-type]
        )


def test_build_store_passes_configured_mount_through() -> None:
    """A non-default mount must reach the store — a prod vault rarely uses `secret/`,
    and silently reading from the wrong mount looks exactly like a missing secret."""
    store = _build_store(
        _settings(
            secret_store="openbao",
            openbao_addr="http://openbao:8200",
            openbao_token="tok",
            openbao_mount="dataq-kv",
        )  # type: ignore[arg-type]
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
    monkeypatch.setenv("SECRET_STORE", "env")
    first = get_secret_store()
    second = get_secret_store()
    assert first is second


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
