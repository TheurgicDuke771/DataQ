"""`GET /admin/workspace-config` — the read-only retention + sign-in allowlist readout."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import get_settings
from backend.app.db.session import get_db
from backend.app.main import app

URL = "/api/v1/admin/workspace-config"


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _fresh_settings() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _set(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def _by_variable(body: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    return {row["variable"]: row for row in body[key]}


def test_reports_the_configured_retention_clocks(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set(
        monkeypatch,
        SAMPLE_FAILURES_RETENTION_DAYS="7",
        AUDIT_RETENTION_DAYS="400",
        OTP_CODES_RETENTION_HOURS="12",
    )
    body = client.get(URL).json()
    retention = _by_variable(body, "retention")
    samples = retention["SAMPLE_FAILURES_RETENTION_DAYS"]
    assert (samples["value"], samples["unit"]) == (7, "days")
    assert retention["AUDIT_RETENTION_DAYS"]["value"] == 400
    codes = retention["OTP_CODES_RETENTION_HOURS"]
    assert (codes["value"], codes["unit"]) == (12, "hours")
    assert all(row["detail"] for row in body["retention"])


def test_allowlist_for_an_unused_mode_is_shown_but_inert(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # OIDC values set with no OIDC issuer configured: no door reads them (ADR 0043), so the
    # readout must not present them as the thing keeping anyone out — or in.
    _set(
        monkeypatch,
        OIDC_ALLOWED_EMAILS="Ada@Example.com, grace@example.com",
        OIDC_ALLOWED_DOMAINS="example.org",
        WORKSPACE_ADMIN_EMAILS="root@example.com",
    )
    body = client.get(URL).json()
    lists = _by_variable(body, "signup_allowlists")
    oidc = lists["OIDC_ALLOWED_EMAILS"]
    assert oidc["values"] == ["ada@example.com", "grace@example.com"]
    assert oidc["active"] is False
    assert lists["OIDC_ALLOWED_DOMAINS"]["values"] == ["example.org"]
    admin = lists["WORKSPACE_ADMIN_EMAILS"]
    # It admits the address AND grants Admin — the guide must never call it role-only.
    assert (admin["values"], admin["active"], admin["grants"]) == (
        ["root@example.com"],
        True,
        "sign_in_and_admin_role",
    )
    # The test stack signs in through the developer bypass, which an OIDC issuer would displace.
    assert (body["sign_in_mode"], body["dev_bypass_active"]) == ("dev_bypass", True)
    assert body["idp_decides_signup"] is False
    assert set(lists) == {
        "OIDC_ALLOWED_EMAILS",
        "OIDC_ALLOWED_DOMAINS",
        "AUTH_OTP_ALLOWED_EMAILS",
        "AUTH_OTP_ALLOWED_DOMAINS",
        "WORKSPACE_ADMIN_EMAILS",
    }
    sign_in = {v: row for v, row in lists.items() if v != "WORKSPACE_ADMIN_EMAILS"}
    assert all(row["grants"] == "sign_in" for row in sign_in.values())


def test_allowlist_is_active_when_its_mode_gates_a_door(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set(
        monkeypatch,
        OIDC_ISSUER="https://issuer.example.com/",
        OIDC_AUDIENCE="dataq",
        OIDC_ALLOWED_DOMAINS="example.com",
        WORKSPACE_ADMIN_EMAILS="dev@dataq.local",
    )
    body = client.get(URL).json()
    lists = _by_variable(body, "signup_allowlists")
    assert lists["OIDC_ALLOWED_DOMAINS"]["active"] is True
    assert lists["OIDC_ALLOWED_EMAILS"]["active"] is True  # same door, empty list
    assert lists["AUTH_OTP_ALLOWED_EMAILS"]["active"] is False
    assert body["dev_bypass_active"] is False
    assert (body["sign_in_mode"], body["idp_decides_signup"]) == ("oidc", False)
    assert (body["oidc_default_role"], body["otp_default_role"]) == ("member", "member")


def test_oidc_without_an_allowlist_is_reported_as_the_idp_deciding(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The open-pool posture: OIDC in use, no app-side allowlist. The mode IS configured, so
    # its rows stay active (an inactive row would read as "mode not configured" — the
    # opposite of the truth) and the top-level flag names what an empty active list means.
    _set(monkeypatch, OIDC_ISSUER="https://issuer.example.com/", OIDC_AUDIENCE="dataq")
    body = client.get(URL).json()
    assert (body["sign_in_mode"], body["idp_decides_signup"]) == ("oidc", True)
    lists = _by_variable(body, "signup_allowlists")
    assert (lists["OIDC_ALLOWED_EMAILS"]["active"], lists["OIDC_ALLOWED_EMAILS"]["values"]) == (
        True,
        [],
    )
    assert lists["OIDC_ALLOWED_DOMAINS"]["active"] is True
    assert lists["AUTH_OTP_ALLOWED_EMAILS"]["active"] is False


def test_azure_ad_mode_reports_the_tenant_as_the_gate(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set(
        monkeypatch,
        AZURE_TENANT_ID="11111111-1111-1111-1111-111111111111",
        AZURE_API_CLIENT_ID="22222222-2222-2222-2222-222222222222",
        OIDC_ALLOWED_DOMAINS="example.com",
    )
    body = client.get(URL).json()
    assert (body["sign_in_mode"], body["idp_decides_signup"]) == ("azure_ad", True)
    lists = _by_variable(body, "signup_allowlists")
    # No app-side allowlist is read in this mode: the OIDC values are shown but inert.
    assert all(
        lists[v]["active"] is False
        for v in (
            "OIDC_ALLOWED_EMAILS",
            "OIDC_ALLOWED_DOMAINS",
            "AUTH_OTP_ALLOWED_EMAILS",
            "AUTH_OTP_ALLOWED_DOMAINS",
        )
    )


def test_the_retention_readout_names_every_sweep_that_deletes(client: TestClient) -> None:
    body = client.get(URL).json()
    assert set(_by_variable(body, "retention")) == {
        "SAMPLE_FAILURES_RETENTION_DAYS",
        "AUDIT_RETENTION_DAYS",
        "OTP_CODES_RETENTION_HOURS",
        "ASSET_ORPHAN_RETENTION_DAYS",
        "NOTIFICATIONS_RETENTION_DAYS",
    }


def test_the_readout_carries_no_secret_bearing_setting(client: TestClient) -> None:
    body = client.get(URL).json()
    text = str(body).lower()
    for forbidden in ("secret", "password", "token", "client_secret"):
        assert forbidden not in text, forbidden


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_non_admins_are_refused(
    client: TestClient, as_role: Callable[..., tuple[Any, dict[str, str]]], role: str
) -> None:
    _, headers = as_role(role)
    response = client.get(URL, headers=headers)
    assert response.status_code == 403
