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
