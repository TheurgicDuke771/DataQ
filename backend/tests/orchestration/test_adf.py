"""ADF connection adapter tests — config validation + the HTTP test() probe.

No live Azure: ``httpx.post`` / ``httpx.get`` are monkeypatched so the
token-acquisition + factory-GET flow is exercised against canned responses. The
adapter is GX-free and DB-free, so these are pure unit tests (no db_session).
"""

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from backend.app.orchestration import adf
from backend.app.orchestration.adf import ADFConfig, ADFConnectionAdapter

_ADF_CONFIG = {
    "subscription_id": "00000000-0000-0000-0000-000000000001",
    "resource_group": "rg-data",
    "factory_name": "lll-adf-nonprod",
    "tenant_id": "00000000-0000-0000-0000-0000000000aa",
    "client_id": "00000000-0000-0000-0000-0000000000bb",
}


class _FakeResponse:
    def __init__(
        self, *, json_body: dict[str, Any] | None = None, raise_exc: Exception | None = None
    ) -> None:
        self._json = json_body or {}
        self._raise = raise_exc

    def raise_for_status(self) -> None:
        if self._raise is not None:
            raise self._raise

    def json(self) -> dict[str, Any]:
        return self._json


# ───────────────────────── validate_config ─────────────────────────


def test_validate_config_accepts_full_config() -> None:
    cfg = ADFConnectionAdapter().validate_config(dict(_ADF_CONFIG))
    assert isinstance(cfg, ADFConfig)
    assert cfg.factory_name == "lll-adf-nonprod"


def test_validate_config_rejects_missing_field() -> None:
    bad = {k: v for k, v in _ADF_CONFIG.items() if k != "factory_name"}
    with pytest.raises(ValidationError):
        ADFConnectionAdapter().validate_config(bad)


def test_validate_config_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        ADFConnectionAdapter().validate_config({**_ADF_CONFIG, "region": "westeurope"})


# ───────────────────────── test() connectivity ─────────────────────


def test_test_succeeds_when_token_and_factory_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        calls["token_url"] = url
        return _FakeResponse(json_body={"access_token": "tok-123"})

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        calls["factory_url"] = url
        calls["auth"] = kwargs["headers"]["Authorization"]
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)

    ADFConnectionAdapter().test(dict(_ADF_CONFIG), "sp-secret")  # no raise

    assert _ADF_CONFIG["tenant_id"] in calls["token_url"]
    assert _ADF_CONFIG["factory_name"] in calls["factory_url"]
    assert calls["auth"] == "Bearer tok-123"


def test_test_raises_when_token_response_has_no_access_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _FakeResponse(json_body={}))
    with pytest.raises(ValueError, match="no access_token"):
        ADFConnectionAdapter().test(dict(_ADF_CONFIG), "sp-secret")


def test_test_raises_when_factory_get_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx, "post", lambda url, **kw: _FakeResponse(json_body={"access_token": "tok"})
    )
    http_error = httpx.HTTPStatusError("404", request=None, response=None)  # type: ignore[arg-type]
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _FakeResponse(raise_exc=http_error))
    with pytest.raises(httpx.HTTPStatusError):
        ADFConnectionAdapter().test(dict(_ADF_CONFIG), "sp-secret")


def test_acquire_token_propagates_auth_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bad SP secret surfaces as the token endpoint's raise_for_status error."""
    http_error = httpx.HTTPStatusError("401", request=None, response=None)  # type: ignore[arg-type]
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _FakeResponse(raise_exc=http_error))
    with pytest.raises(httpx.HTTPStatusError):
        adf._acquire_token(ADFConfig.model_validate(_ADF_CONFIG), "wrong-secret")
