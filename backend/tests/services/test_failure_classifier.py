"""Unit tests for the redaction-safe failure classifier (#605).

The contract that matters most: the returned reason is ALWAYS one of the fixed
category messages — never the raw exception text — so a credential/DSN/PII
fragment in the exception can't ride out onto a persisted/surfaced reason.
"""

import pytest

from backend.app.services.failure_classifier import (
    _MESSAGES,
    _ORCHESTRATION_MESSAGES,
    FailureCategory,
    classify_failure_category,
    classify_failure_reason,
    classify_inventory_sync_error,
    classify_orchestration_poll_reason,
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
    the generic PERMISSION message every other failure gets.

    The specific claim is gated on the PHASE (`during_enumeration`), because the
    category markers are broad substrings: a secret-store 403 or an IdP handshake
    rejection matches PERMISSION exactly as well as a missing grant does, and
    naming a warehouse grant for one of those sends an admin to fix something
    that was never broken (the #1227 review finding)."""

    def test_uc_grant_error_names_the_schema(self) -> None:
        exc = RuntimeError(
            "Insufficient privileges to SELECT on Schema 'system.information_schema'"
        )
        reason = classify_inventory_sync_error(exc, "unity_catalog", during_enumeration=True)
        assert "system.information_schema" in reason
        # Specific, not the generic bucket message every other permission failure gets.
        assert reason != _MESSAGES[FailureCategory.PERMISSION]

    def test_snowflake_grant_error_names_the_schema(self) -> None:
        exc = RuntimeError(
            "SQL access control error: Insufficient privileges to operate"
            " on schema 'INFORMATION_SCHEMA'"
        )
        reason = classify_inventory_sync_error(exc, "snowflake", during_enumeration=True)
        assert "INFORMATION_SCHEMA" in reason
        assert reason != _MESSAGES[FailureCategory.PERMISSION]

    def test_uc_and_snowflake_grant_messages_differ(self) -> None:
        exc = PermissionError("access denied")
        assert classify_inventory_sync_error(
            exc, "unity_catalog", during_enumeration=True
        ) != classify_inventory_sync_error(exc, "snowflake", during_enumeration=True)

    def test_non_permission_failure_falls_back_to_the_generic_reason(self) -> None:
        exc = TimeoutError("connection timed out after 30s")
        reason = classify_inventory_sync_error(exc, "unity_catalog", during_enumeration=True)
        assert reason == classify_failure_reason(exc)
        assert reason == _MESSAGES[FailureCategory.CONNECTIVITY]

    def test_unknown_connection_type_falls_back_to_the_generic_permission_message(self) -> None:
        # No known schema mapping for this type — must not raise, must not fabricate
        # a schema name; falls back to the generic classified reason.
        exc = PermissionError("access denied")
        assert (
            classify_inventory_sync_error(exc, "some_future_type", during_enumeration=True)
            == _MESSAGES[FailureCategory.PERMISSION]
        )

    @pytest.mark.parametrize(
        "exc",
        [
            # A sealed/again-403 secret store, reading the credential BEFORE the
            # warehouse is ever contacted — the driver never ran a query at all.
            RuntimeError("HTTP 403 from the secret store while reading conn-uc-prod"),
            # The driver handshake itself: the warehouse is reachable, the
            # credential is not accepted. Still not a missing SELECT grant.
            RuntimeError("failed to authenticate: token expired"),
        ],
    )
    def test_a_permission_failure_before_enumeration_never_claims_a_missing_grant(
        self, exc: Exception
    ) -> None:
        """The misdiagnosis this gate exists to prevent: an admin granting SELECT on
        a system schema that was never the problem, while the real fault (a sealed
        vault, an expired token) stays undiagnosed."""
        reason = classify_inventory_sync_error(exc, "unity_catalog", during_enumeration=False)
        assert reason == _MESSAGES[FailureCategory.PERMISSION]
        assert "information_schema" not in reason.lower()
        assert "grant select" not in reason.lower()

    def test_the_enumeration_message_hedges_rather_than_asserting_the_cause(self) -> None:
        """Even in the right phase the driver's own text is only a substring match, so
        the reason must read as the most likely cause plus a next step — never as a
        certainty that leaves the reader with nowhere to go if the grant is present."""
        exc = PermissionError("access denied")
        reason = classify_inventory_sync_error(exc, "snowflake", during_enumeration=True)
        assert "most likely" in reason.lower()
        assert "credential" in reason.lower()

    @pytest.mark.parametrize(
        "exc",
        [
            RuntimeError("Object 'DATAQ_DB' does not exist or not authorized"),
            ValueError("something entirely unexpected"),
        ],
    )
    def test_the_reason_never_describes_the_failure_as_a_run(self, exc: Exception) -> None:
        """The generic messages are written for a RUN — `CONFIG` sends the reader to
        "the suite's run target" (an inventory sync has none) and `UNKNOWN` says "the
        run failed to execute. See the server logs", which is the answer #1104 was
        filed to replace. Rendering either in the connection's tooltip would describe
        a run that does not exist."""
        reason = classify_inventory_sync_error(exc, "snowflake", during_enumeration=True)
        assert "run target" not in reason
        assert "The run failed to execute" not in reason
        assert "inventory" in reason.lower()

    def test_connectivity_keeps_the_shared_datasource_wording(self) -> None:
        """Not every category needs its own copy — `CONNECTIVITY` already describes the
        datasource rather than the run, so it stays shared (one message to keep true)."""
        exc = TimeoutError("connection timed out after 30s")
        assert (
            classify_inventory_sync_error(exc, "snowflake", during_enumeration=True)
            == _MESSAGES[FailureCategory.CONNECTIVITY]
        )

    def test_the_grant_message_also_points_at_role_and_warehouse_privileges(self) -> None:
        """Phase narrows the claim to "the warehouse rejected OUR query" — no further.
        A Snowflake role that lost USAGE on its warehouse is rejected at exactly the
        same point and reads identically, so the message must not assert the SELECT
        grant as the cause and leave that reader with nowhere to go."""
        reason = classify_inventory_sync_error(
            PermissionError("access denied"), "snowflake", during_enumeration=True
        )
        assert "role" in reason.lower()
        assert "warehouse" in reason.lower()

    def test_never_echoes_the_raw_exception_text(self) -> None:
        secret = "token dq_pat_SUPERSECRET"  # a marker string, not a real credential
        exc = RuntimeError(f"access denied: {secret}")
        for phase in (True, False):
            reason = classify_inventory_sync_error(exc, "unity_catalog", during_enumeration=phase)
            assert secret not in reason
            assert "SUPERSECRET" not in reason


class TestOrchestrationPollReason:
    """#1285 — an orchestration connection is NOT a datasource (CLAUDE.md §4), so its
    poll failures must not be described in warehouse/role/table/run-target nouns.

    This shipped to prod: both Airflow connections reported "missing warehouse or role
    … check the suite's run target" while the real cause was the Airflow host being
    stopped. That is the #828 confident-wrong-answer shape — worse than a silent gap,
    because it sends an operator to fix something that doesn't exist on the object.
    """

    # The exact exception an Airflow poll raises against a stopped Container App:
    # httpx `raise_for_status()` on the ingress's 404.
    STOPPED_HOST = RuntimeError("Client error '404 Not Found' for url 'https://airflow.example'")

    @pytest.mark.parametrize(
        "exc",
        [
            STOPPED_HOST,
            RuntimeError("connection refused"),
            PermissionError("access denied"),
            RuntimeError("something entirely unrecognised"),
        ],
    )
    def test_no_reason_uses_datasource_or_run_vocabulary(self, exc: Exception) -> None:
        """The property that actually failed in prod, asserted over every category —
        not just the one that happened to break."""
        reason = classify_orchestration_poll_reason(exc).lower()
        # "the run failed to execute" is the generic UNKNOWN text — it describes a
        # RUN, which a poll is not, so it belongs on this list too. Without it the
        # UNKNOWN case passes against the unfixed code and proves nothing.
        for noun in ("warehouse", "role", "table/path", "run target", "datasource", "the run"):
            assert noun not in reason, f"{noun!r} leaked into an orchestration reason: {reason}"

    def test_every_category_has_an_orchestration_message(self) -> None:
        """A category added later without a message here would KeyError in the poll
        path — the one place that must never raise (it runs inside the failure
        handler itself)."""
        for category in FailureCategory:
            assert category in _ORCHESTRATION_MESSAGES

    def test_a_stopped_host_names_both_plausible_causes(self) -> None:
        """A 404 cannot distinguish "DAG deleted" from "host stopped" — Container Apps
        answers for a stopped app with the same shape. So the message must name both
        rather than assert one, per the #1104 hedging precedent."""
        reason = classify_orchestration_poll_reason(self.STOPPED_HOST).lower()
        assert "pipeline/dag" in reason
        assert "url" in reason
        assert "stopped" in reason

    @pytest.mark.parametrize(
        "message",
        [
            "Server error '502 Bad Gateway' for url 'https://airflow.example'",
            "Server error '503 Service Unavailable' for url 'https://airflow.example'",
            "Server error '504 Gateway Timeout' for url 'https://airflow.example'",
            "upstream connect error: no healthy upstream",
        ],
    )
    def test_upstream_down_statuses_classify_as_connectivity_not_config(self, message: str) -> None:
        """Unlike a 404, a gateway saying its backend is missing or overloaded is an
        unambiguous connectivity fact. Before #1285 these fell through to CONFIG and
        told the reader their configuration was wrong."""
        assert classify_failure_category(RuntimeError(message)) is FailureCategory.CONNECTIVITY

    def test_never_echoes_the_raw_exception_text(self) -> None:
        secret = "bearer dq_live_SUPERSECRET"  # a marker string, not a real credential
        reason = classify_orchestration_poll_reason(RuntimeError(f"401 unauthorized: {secret}"))
        assert secret not in reason
        assert "SUPERSECRET" not in reason
