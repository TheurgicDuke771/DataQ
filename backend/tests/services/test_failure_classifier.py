"""Unit tests for the redaction-safe failure classifier (#605).

The contract that matters most: the returned reason is ALWAYS one of the fixed
category messages — never the raw exception text — so a credential/DSN/PII
fragment in the exception can't ride out onto a persisted/surfaced reason.
"""

import pytest

from backend.app.services.failure_classifier import (
    _MESSAGES,
    FailureCategory,
    classify_failure_category,
    classify_failure_reason,
    classify_inventory_sync_error,
)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (RuntimeError("Incorrect username or password was specified"), FailureCategory.PERMISSION),
        (RuntimeError("Insufficient privileges to operate on schema"), FailureCategory.PERMISSION),
        (PermissionError("access denied"), FailureCategory.PERMISSION),
        (RuntimeError("HTTP 403 Forbidden"), FailureCategory.PERMISSION),
        (TimeoutError("connection timed out after 30s"), FailureCategory.CONNECTIVITY),
        (OSError("Connection refused"), FailureCategory.CONNECTIVITY),
        (RuntimeError("Temporary failure in name resolution"), FailureCategory.CONNECTIVITY),
        (RuntimeError("Table 'RAW.ORDERS' does not exist"), FailureCategory.CONFIG),
        (
            RuntimeError("No active warehouse selected in the current session"),
            FailureCategory.CONFIG,
        ),
        (KeyError("account"), FailureCategory.CONFIG),
        (ValueError("something entirely unexpected"), FailureCategory.UNKNOWN),
    ],
)
def test_classifies_into_the_expected_category(exc: Exception, expected: FailureCategory) -> None:
    assert classify_failure_category(exc) == expected
    assert classify_failure_reason(exc) == _MESSAGES[expected]


def test_permission_wins_over_config_for_invalid_credentials() -> None:
    # "invalid credentials" contains no config marker, but the ordering also
    # guarantees an auth error never falls through to config even if it mentions
    # a missing object.
    exc = RuntimeError("authentication failed: role DATAQ not found")
    assert classify_failure_category(exc) == FailureCategory.PERMISSION


def test_reason_never_echoes_the_raw_exception_text() -> None:
    """The whole point (#605): a secret/DSN/PII fragment in the exception must not
    appear in the returned reason."""
    secret = "snowflake://user:SUPERSECRET@acct.region/db"
    reason = classify_failure_reason(RuntimeError(f"could not connect to {secret}"))
    assert secret not in reason
    assert "SUPERSECRET" not in reason
    assert reason in _MESSAGES.values()


class TestInventorySyncClassification:
    """#1104 — a grant failure on the inventory-sync enumeration query must get a
    SPECIFIC, connection-type-aware reason (naming the known system schema), not
    the generic PERMISSION message every other failure gets."""

    def test_uc_grant_error_names_the_schema(self) -> None:
        exc = RuntimeError(
            "Insufficient privileges to SELECT on Schema 'system.information_schema'"
        )
        reason = classify_inventory_sync_error(exc, "unity_catalog")
        assert "system.information_schema" in reason
        # Specific, not the generic bucket message every other permission failure gets.
        assert reason != _MESSAGES[FailureCategory.PERMISSION]

    def test_snowflake_grant_error_names_the_schema(self) -> None:
        exc = RuntimeError(
            "SQL access control error: Insufficient privileges to operate"
            " on schema 'INFORMATION_SCHEMA'"
        )
        reason = classify_inventory_sync_error(exc, "snowflake")
        assert "INFORMATION_SCHEMA" in reason
        assert reason != _MESSAGES[FailureCategory.PERMISSION]

    def test_uc_and_snowflake_grant_messages_differ(self) -> None:
        exc = PermissionError("access denied")
        assert classify_inventory_sync_error(exc, "unity_catalog") != classify_inventory_sync_error(
            exc, "snowflake"
        )

    def test_non_permission_failure_falls_back_to_the_generic_reason(self) -> None:
        exc = TimeoutError("connection timed out after 30s")
        assert classify_inventory_sync_error(exc, "unity_catalog") == classify_failure_reason(exc)
        assert (
            classify_inventory_sync_error(exc, "unity_catalog")
            == _MESSAGES[FailureCategory.CONNECTIVITY]
        )

    def test_unknown_connection_type_falls_back_to_the_generic_permission_message(self) -> None:
        # No known schema mapping for this type — must not raise, must not fabricate
        # a schema name; falls back to the generic classified reason.
        exc = PermissionError("access denied")
        assert (
            classify_inventory_sync_error(exc, "some_future_type")
            == _MESSAGES[FailureCategory.PERMISSION]
        )

    def test_never_echoes_the_raw_exception_text(self) -> None:
        secret = "token dq_pat_SUPERSECRET"
        exc = RuntimeError(f"access denied: {secret}")
        reason = classify_inventory_sync_error(exc, "unity_catalog")
        assert secret not in reason
        assert "SUPERSECRET" not in reason
