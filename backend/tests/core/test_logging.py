"""Tests for the PII redactor (post-2026-05-28 security audit additions)."""

from backend.app.core.logging import _redact_pii


def _redact(payload: dict[str, object]) -> dict[str, object]:
    """Apply the redactor as a free function (mirrors structlog processor invocation)."""
    return dict(_redact_pii(None, "", dict(payload)))


def test_redacts_credentials_and_personal_contact() -> None:
    out = _redact(
        {
            "event": "auth_attempt",
            "password": "hunter2",
            "token": "abc.def",
            "api_key": "sk-1234",
            "authorization": "Bearer xyz",
            "email": "user@example.com",
            "phone": "+15551234567",
        }
    )
    assert out["password"] == "<redacted>"
    assert out["token"] == "<redacted>"
    assert out["api_key"] == "<redacted>"
    assert out["authorization"] == "<redacted>"
    assert out["email"] == "<redacted>"
    assert out["phone"] == "<redacted>"
    assert out["event"] == "auth_attempt"  # safe key


def test_redacts_azure_ad_claim_fields() -> None:
    """Per security audit 2026-05-28: AAD identifiers are GDPR personal data."""
    out = _redact(
        {
            "event": "auth_user_resolved",
            "oid": "00000000-0000-0000-0000-000000000001",
            "aad_oid": "00000000-0000-0000-0000-000000000001",
            "aad_object_id": "00000000-0000-0000-0000-000000000001",
            "upn": "user@tenant.onmicrosoft.com",
            "preferred_username": "user@example.com",
            "user_id": "u-12345",
            "name": "Jane Doe",
            "display_name": "Jane Doe",
        }
    )
    assert out["oid"] == "<redacted>"
    assert out["aad_oid"] == "<redacted>"
    assert out["aad_object_id"] == "<redacted>"
    assert out["upn"] == "<redacted>"
    assert out["preferred_username"] == "<redacted>"
    assert out["user_id"] == "<redacted>"
    assert out["name"] == "<redacted>"
    assert out["display_name"] == "<redacted>"


def test_redacts_nested_pii_keys() -> None:
    out = _redact(
        {
            "event": "request",
            "headers": {
                "authorization": "Bearer xyz",
                "x-request-id": "safe-value",
            },
        }
    )
    assert out["headers"] == {  # type: ignore[comparison-overlap]
        "authorization": "<redacted>",
        "x-request-id": "safe-value",
    }


def test_redacts_lists_of_dicts() -> None:
    out = _redact(
        {
            "event": "failure_sample",
            "rows": [
                {"email": "a@b.com", "order_id": "ORD-1"},
                {"email": "c@d.com", "order_id": "ORD-2"},
            ],
        }
    )
    assert out["rows"] == [
        {"email": "<redacted>", "order_id": "ORD-1"},
        {"email": "<redacted>", "order_id": "ORD-2"},
    ]


def test_safe_keys_pass_through() -> None:
    """Status, level, duration etc. must not be touched."""
    out = _redact(
        {
            "event": "request",
            "method": "GET",
            "path": "/healthz",
            "status": 200,
            "duration_ms": 12.34,
            "level": "info",
        }
    )
    assert out["method"] == "GET"
    assert out["path"] == "/healthz"
    assert out["status"] == 200
    assert out["duration_ms"] == 12.34
    assert out["level"] == "info"
