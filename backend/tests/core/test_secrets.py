from types import SimpleNamespace

import pytest

from backend.app.core import secrets
from backend.app.core.secrets import (
    AzureKeyVaultStore,
    EnvSecretStore,
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
    monkeypatch.setattr(secrets.os, "environ", dict(secrets.os.environ))
    store = EnvSecretStore()
    store.set("conn-snowflake-dev-finance", "p@ss")
    assert store.get("conn-snowflake-dev-finance") == "p@ss"


def test_env_store_set_writes_normalised_key(
    clean_kv_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(secrets.os, "environ", dict(secrets.os.environ))
    EnvSecretStore().set("conn-snowflake-dev-finance", "p@ss")
    assert secrets.os.environ["KV_SECRET_CONN_SNOWFLAKE_DEV_FINANCE"] == "p@ss"


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


# ───────────────────────── Factory + cache ─────────────────────────


def _settings(**overrides: object) -> object:
    base = {
        "secret_store": "env",
        "azure_key_vault_url": None,
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
