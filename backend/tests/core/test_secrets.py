import os
from types import SimpleNamespace
from typing import ClassVar

import pytest

from backend.app.core import secrets
from backend.app.core.secrets import (
    AzureKeyVaultStore,
    EnvSecretStore,
    RedisSecretStore,
    SecretNotFoundError,
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
    with pytest.raises(SecretNotFoundError, match="network down"):
        store.get("snowflake-uat-finance")


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


def test_build_store_returns_redis_store_when_configured() -> None:
    store = _build_store(_settings(secret_store="redis"))  # type: ignore[arg-type]
    assert isinstance(store, RedisSecretStore)


# ───────────────────────── RedisSecretStore ────────────────────────


def test_redis_store_lazy_client_not_built_on_init() -> None:
    """Constructing the store must not connect to Redis."""
    store = RedisSecretStore("redis://localhost:6379/0")
    assert store._client is None


def test_redis_store_get_returns_value(monkeypatch: pytest.MonkeyPatch) -> None:
    store = RedisSecretStore("redis://localhost:6379/0")
    fake_client = SimpleNamespace(get=lambda key: "redis-value")
    monkeypatch.setattr(store, "_client_lazy", lambda: fake_client)
    assert store.get("snowflake-uat-finance") == "redis-value"


def test_redis_store_get_namespaces_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    store = RedisSecretStore("redis://localhost:6379/0")
    seen: list[str] = []

    def _get(key: str) -> str:
        seen.append(key)
        return "v"

    monkeypatch.setattr(store, "_client_lazy", lambda: SimpleNamespace(get=_get))
    store.get("conn-1")
    assert seen == ["dataq:secret:conn-1"]


def test_redis_store_get_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    store = RedisSecretStore("redis://localhost:6379/0")
    monkeypatch.setattr(store, "_client_lazy", lambda: SimpleNamespace(get=lambda key: None))
    with pytest.raises(SecretNotFoundError, match="not set"):
        store.get("missing")


def test_redis_store_get_wraps_client_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    store = RedisSecretStore("redis://localhost:6379/0")

    def _boom(key: str) -> None:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(store, "_client_lazy", lambda: SimpleNamespace(get=_boom))
    with pytest.raises(SecretNotFoundError, match="connection refused"):
        store.get("x")


def test_redis_store_set_calls_set_with_namespaced_key(monkeypatch: pytest.MonkeyPatch) -> None:
    store = RedisSecretStore("redis://localhost:6379/0")
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        store, "_client_lazy", lambda: SimpleNamespace(set=lambda k, v: calls.append((k, v)))
    )
    store.set("conn-1", "p@ss")
    assert calls == [("dataq:secret:conn-1", "p@ss")]


def test_redis_store_set_wraps_client_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    store = RedisSecretStore("redis://localhost:6379/0")

    def _boom(key: str, value: str) -> None:
        raise RuntimeError("write failed")

    monkeypatch.setattr(store, "_client_lazy", lambda: SimpleNamespace(set=_boom))
    with pytest.raises(SecretWriteError, match="write failed"):
        store.set("conn-1", "p@ss")


def test_redis_store_set_in_one_instance_is_visible_to_another() -> None:
    """The cross-process property #86 needs: a write through one store instance
    (≈ the API) is readable through a separate instance (≈ the Celery worker).
    Uses a real Redis; skipped when unreachable (CI provides one)."""
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    writer = RedisSecretStore(url, key_prefix="dataq:test-secret:")
    reader = RedisSecretStore(url, key_prefix="dataq:test-secret:")
    try:
        writer._client_lazy().ping()
    except Exception:  # pragma: no cover - environment-dependent
        pytest.skip(f"Redis not reachable at {url}")

    name = f"conn-{os.getpid()}-xprocess"
    try:
        writer.set(name, "shared-secret")
        assert reader.get(name) == "shared-secret"  # separate instance sees it
    finally:
        writer._client_lazy().delete(f"dataq:test-secret:{name}")


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
