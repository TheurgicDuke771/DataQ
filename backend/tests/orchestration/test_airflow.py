"""Airflow connection adapter tests — config validation + the REST test() probe.

No live Airflow: ``httpx.get`` is monkeypatched so the DAGs-probe flow is
exercised against canned responses. The adapter is DB-free, so these are pure
unit tests (no db_session).
"""

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from backend.app.orchestration.airflow import AirflowConfig, AirflowConnectionAdapter

_TOKEN_CONFIG = {"base_url": "https://airflow.example.com", "auth_type": "token"}
_BASIC_CONFIG = {
    "base_url": "https://airflow.example.com",
    "auth_type": "basic",
    "username": "dataq",
}


class _FakeResponse:
    def __init__(self, *, raise_exc: Exception | None = None) -> None:
        self._raise = raise_exc

    def raise_for_status(self) -> None:
        if self._raise is not None:
            raise self._raise


# ───────────────────────── validate_config ─────────────────────────


def test_validate_config_accepts_token_config() -> None:
    cfg = AirflowConnectionAdapter().validate_config(dict(_TOKEN_CONFIG))
    assert isinstance(cfg, AirflowConfig)
    assert cfg.auth_type == "token"


def test_validate_config_defaults_auth_type_to_token() -> None:
    cfg = AirflowConnectionAdapter().validate_config({"base_url": "https://a.example.com"})
    assert cfg.auth_type == "token"


def test_validate_config_accepts_basic_with_username() -> None:
    cfg = AirflowConnectionAdapter().validate_config(dict(_BASIC_CONFIG))
    assert cfg.username == "dataq"


def test_validate_config_rejects_basic_without_username() -> None:
    with pytest.raises(ValidationError, match="username is required"):
        AirflowConnectionAdapter().validate_config(
            {"base_url": "https://a.example.com", "auth_type": "basic"}
        )


def test_validate_config_rejects_non_http_base_url() -> None:
    with pytest.raises(ValidationError, match="http"):
        AirflowConnectionAdapter().validate_config({"base_url": "airflow.example.com"})


def test_validate_config_strips_trailing_slash() -> None:
    cfg = AirflowConnectionAdapter().validate_config({"base_url": "https://a.example.com/"})
    assert cfg.base_url == "https://a.example.com"


def test_validate_config_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        AirflowConnectionAdapter().validate_config({**_TOKEN_CONFIG, "region": "eu"})


# ───────────────────────── test() connectivity ─────────────────────


def test_test_token_auth_sends_bearer_header(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        calls["url"] = url
        calls["headers"] = kwargs["headers"]
        calls["auth"] = kwargs["auth"]
        return _FakeResponse()

    monkeypatch.setattr(httpx, "get", fake_get)
    AirflowConnectionAdapter().test(dict(_TOKEN_CONFIG), "tok-123")  # no raise

    assert calls["url"] == "https://airflow.example.com/api/v1/dags"
    assert calls["headers"]["Authorization"] == "Bearer tok-123"
    assert calls["auth"] is None


def test_test_basic_auth_uses_basic_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        calls["auth"] = kwargs["auth"]
        calls["headers"] = kwargs["headers"]
        return _FakeResponse()

    monkeypatch.setattr(httpx, "get", fake_get)
    AirflowConnectionAdapter().test(dict(_BASIC_CONFIG), "p@ss")  # no raise

    assert isinstance(calls["auth"], httpx.BasicAuth)
    assert "Authorization" not in calls["headers"]


def test_test_raises_when_probe_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    http_error = httpx.HTTPStatusError("401", request=None, response=None)  # type: ignore[arg-type]
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _FakeResponse(raise_exc=http_error))
    with pytest.raises(httpx.HTTPStatusError):
        AirflowConnectionAdapter().test(dict(_TOKEN_CONFIG), "bad-token")
