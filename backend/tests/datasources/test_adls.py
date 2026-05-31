"""ADLS Gen2 connection adapter tests — config validation + the container probe.

No live Azure: ``azure.storage.blob.BlobServiceClient`` is monkeypatched so the
container-properties probe runs against a fake. The adapter is DB-free, so these
are pure unit tests (no db_session).
"""

from typing import Any

import azure.storage.blob as azblob
import pytest
from pydantic import ValidationError

from backend.app.datasources.adls import AdlsConfig, AdlsConnectionAdapter

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
