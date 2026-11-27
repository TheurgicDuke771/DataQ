"""Datasource credential health, recorded at the one credential-use seam (#1697)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ResourceNotFoundError,
)

from backend.app.db.models import Connection, User
from backend.app.services import credential_health
from backend.app.services.failure_classifier import is_auth_failure
from backend.tests.support.credential_configs import CREDENTIAL_CONFIGS, NO_CREDENTIAL_CONFIGS


def _connection(
    db_session: Any,
    *,
    conn_type: str = "snowflake",
    secret_ref: str | None = "c-x",
    config: dict[str, Any] | None = None,
) -> Connection:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@ex")
    db_session.add(user)
    db_session.flush()
    conn = Connection(
        name=f"conn-{uuid.uuid4().hex[:8]}",
        type=conn_type,
        env="dev",
        config=config if config is not None else {},
        secret_ref=secret_ref,
        created_by=user.id,
    )
    db_session.add(conn)
    db_session.commit()
    return conn


# Real driver vocabulary. Every string below is the shape the named driver actually
# emits — invented strings would make this suite prove only that the markers match
# themselves.
AUTH_ERRORS = [
    # Snowflake connector.
    (
        "snowflake_250001",
        RuntimeError(
            "250001 (08001): Failed to connect to DB: acct.snowflakecomputing.com:443. Incorrect "
            "username or password was specified."
        ),
    ),
    (
        "snowflake_390100",
        RuntimeError("390100 (08004): Incorrect username or password was specified."),
    ),
    (
        # #2314: the message the AWS deployment's expired reader PAT produced, 2026-09-30.
        "snowflake_394401_pat_expired",
        RuntimeError(
            "394401 (08001): None: Failed to connect to DB: acct.snowflakecomputing.com:443. "
            "Programmatic access token is expired. [4aaa1095-075e-4448-815b-e7692952af59]"
        ),
    ),
    (
        "snowflake_390114",
        RuntimeError(
            "390114 (08001): Authentication token has expired. The user must authenticate again."
        ),
    ),
    # Databricks / Unity Catalog PAT.
    (
        "databricks_401",
        RuntimeError(
            "Error during request to server: HTTP 401 Unauthorized: Invalid access token."
        ),
    ),
    # Azure ADLS Gen2 / Blob.
    (
        "adls_authenticationfailed",
        PermissionError(
            "AuthenticationFailed: Server failed to authenticate the request. Make sure the value "
            "of Authorization header is formed correctly."
        ),
    ),
    (
        "adls_sas_signature",
        PermissionError(
            "AuthenticationFailed: Signature did not match. String to sign used was ..."
        ),
    ),
    # AWS S3 and S3-compatible stores.
    (
        "s3_invalid_access_key",
        RuntimeError(
            "An error occurred (InvalidAccessKeyId) when calling the ListObjectsV2 operation: The "
            "AWS Access Key Id you provided does not exist in our records."
        ),
    ),
    (
        "s3_signature",
        RuntimeError(
            "An error occurred (SignatureDoesNotMatch) when calling the GetObject operation: The "
            "request signature we calculated does not match the signature you provided."
        ),
    ),
    (
        "s3_expired_token",
        RuntimeError(
            "An error occurred (ExpiredToken) when calling the ListObjectsV2 operation: The "
            "provided token has expired."
        ),
    ),
    # AWS SigV4 service APIs — Athena, as pyathena raised it live for a wrong secret key (#2131).
    (
        "athena_invalid_signature",
        RuntimeError(
            "(pyathena.error.DatabaseError) An error occurred (InvalidSignatureException) when "
            "calling the StartQueryExecution operation: The request signature we calculated does "
            "not match the signature you provided."
        ),
    ),
    # Iceberg REST catalog.
    ("iceberg_rest_401", RuntimeError("Server error: 401 Unauthorized: invalid_client")),
    # Entra service principal (ADLS `service_principal`, #1680) — text as azure-identity raised
    # it live, trace/correlation ids dropped.
    (
        "entra_sp_invalid_secret",
        ClientAuthenticationError(
            "Authentication failed: AADSTS7000215: Invalid client secret provided. Ensure the "
            "secret being sent in the request is the client secret value, not the client secret "
            "ID, for a secret added to app '00000000-0000-0000-0000-000000000001'."
        ),
    ),
    (
        "entra_sp_expired_secret",
        ClientAuthenticationError(
            "Authentication failed: AADSTS7000222: The provided client secret keys for app "
            "'00000000-0000-0000-0000-000000000001' are expired."
        ),
    ),
]

# Failures that must NOT move the signal. Each says something real is wrong, and none
# of them says the CREDENTIAL is dead — recording them would make the signal lie.
NON_AUTH_ERRORS = [
    (
        "missing_grant",
        RuntimeError(
            "SQL access control error: Insufficient privileges to operate on table 'ORDERS'"
        ),
    ),
    ("permission_denied", PermissionError("permission denied for schema analytics")),
    (
        "bad_table",
        RuntimeError(
            "002003 (42S02): SQL compilation error: Object 'NOPE' does not exist or not authorized."
        ),
    ),
    ("unreachable", ConnectionError("Could not connect to host: getaddrinfo failed")),
    (
        "snowflake_bad_account",
        RuntimeError(
            "250001 (08001): Failed to connect to DB: acct.snowflakecomputing.com:443. "
            "404 Not Found"
        ),
    ),
    ("timeout", TimeoutError("Request timed out after 30s")),
    (
        "no_warehouse",
        RuntimeError("000606 (57P03): No active warehouse selected in the current session."),
    ),
    # Entra / OneLake (#1680): a wrong tenant or client id is configuration, and a missing data
    # role or workspace membership is a grant — none of them says the client secret is dead.
    (
        "entra_unknown_tenant",
        ClientAuthenticationError(
            "Authentication failed: Unable to get authority configuration for "
            "https://login.microsoftonline.com/00000000-0000-0000-0000-000000000009. Also please "
            "double check your tenant name or GUID is correct."
        ),
    ),
    (
        "entra_unknown_client",
        ClientAuthenticationError(
            "Authentication failed: AADSTS700016: Application with identifier "
            "'00000000-0000-0000-0000-000000000002' was not found in the directory "
            "'00000000-0000-0000-0000-000000000003'. You may have sent your authentication "
            "request to the wrong tenant."
        ),
    ),
    (
        "storage_no_data_role",
        HttpResponseError(
            "This request is not authorized to perform this operation using this permission.\n"
            "ErrorCode:AuthorizationPermissionMismatch"
        ),
    ),
    (
        "onelake_workspace_not_visible",
        ResourceNotFoundError(
            "The specified workspace was not found.\nErrorCode:WorkspaceNotFound"
        ),
    ),
]


class TestAuthClassification:
    @pytest.mark.parametrize("label,exc", AUTH_ERRORS, ids=[c[0] for c in AUTH_ERRORS])
    def test_real_driver_auth_errors_are_recognised(self, label: str, exc: Exception) -> None:
        assert is_auth_failure(exc) is True

    @pytest.mark.parametrize("label,exc", NON_AUTH_ERRORS, ids=[c[0] for c in NON_AUTH_ERRORS])
    def test_non_credential_failures_are_not_auth(self, label: str, exc: Exception) -> None:
        # The narrowing that separates #1697 from `FailureCategory.PERMISSION`: a missing
        # grant is an authorization fact, not a credential-health one.
        assert is_auth_failure(exc) is False

    def test_a_wrapped_driver_error_is_still_recognised(self) -> None:
        # `InventorySyncEnumerationError` and the GX/SQLAlchemy wrappers all re-raise
        # `from` the driver error, so matching only the outermost exception would see
        # none of the vocabulary the datasource actually emitted.
        try:
            try:
                raise RuntimeError("390100 (08004): Incorrect username or password was specified.")
            except RuntimeError as inner:
                raise ValueError("inventory sync failed") from inner
        except ValueError as outer:
            assert is_auth_failure(outer) is True

    def test_a_self_referential_chain_terminates(self) -> None:
        exc = RuntimeError("boom")
        exc.__cause__ = exc
        assert is_auth_failure(exc) is False


class TestStatusDerivation:
    def test_a_never_used_credential_is_unknown_not_healthy(self, db_session: Any) -> None:
        # The whole point of the signal (#828/#954): silence must not read as a clean
        # bill of health. A connection nothing has run against has been OBSERVED not at
        # all, and "unknown" is the only honest answer.
        conn = _connection(db_session)
        assert conn.last_auth_success_at is None
        assert conn.last_auth_failure_at is None
        assert conn.consecutive_auth_failures == 0

        assert credential_health.credential_status(conn) == "unknown"

    def test_a_successful_use_makes_it_healthy(self, db_session: Any) -> None:
        conn = _connection(db_session)
        credential_health.record_credential_success(db_session, connection_id=conn.id)
        db_session.refresh(conn)
        assert credential_health.credential_status(conn) == "healthy"

    def test_a_rejection_makes_it_failing(self, db_session: Any) -> None:
        conn = _connection(db_session)
        credential_health.record_credential_failure(
            db_session,
            connection_id=conn.id,
            exc=RuntimeError("390100 Incorrect username or password"),
        )
        db_session.refresh(conn)
        assert credential_health.credential_status(conn) == "failing"

    def test_a_failing_credential_stays_failing_even_with_an_old_success(
        self, db_session: Any
    ) -> None:
        # A credential that worked last week and is rejected today is failing NOW; the
        # older success must not out-vote the current streak.
        conn = _connection(db_session)
        credential_health.record_credential_success(db_session, connection_id=conn.id)
        credential_health.record_credential_failure(
            db_session, connection_id=conn.id, exc=RuntimeError("AuthenticationFailed")
        )
        db_session.refresh(conn)
        assert conn.last_auth_success_at is not None
        assert credential_health.credential_status(conn) == "failing"


class TestNotApplicable:
    """A connection that authenticates with no credential has none to observe (never stored)."""

    @pytest.mark.parametrize(("conn_type", "config"), NO_CREDENTIAL_CONFIGS)
    def test_a_credential_less_auth_mode_is_not_applicable(
        self, db_session: Any, conn_type: str, config: dict[str, Any]
    ) -> None:
        from backend.app.datasources.registry import get_connection_adapter

        # The adapter accepts the config, so this is a real mode and not a stand-in.
        get_connection_adapter(conn_type).validate_config(dict(config))
        conn = _connection(db_session, conn_type=conn_type, secret_ref=None, config=config)

        assert credential_health.credential_status(conn) == "not_applicable"

    @pytest.mark.parametrize(("conn_type", "config"), NO_CREDENTIAL_CONFIGS)
    def test_it_is_derived_and_never_stored(
        self, db_session: Any, conn_type: str, config: dict[str, Any]
    ) -> None:
        conn = _connection(db_session, conn_type=conn_type, secret_ref=None, config=config)
        with credential_health.credential_use(db_session, conn):
            pass
        db_session.refresh(conn)

        # The seam still records nothing for a connection with no credential: the status is
        # read off the auth mode, so the stored health columns stay untouched.
        assert conn.last_auth_success_at is None
        assert conn.last_auth_failure_at is None
        assert conn.consecutive_auth_failures == 0
        assert credential_health.credential_status(conn) == "not_applicable"

    @pytest.mark.parametrize(("conn_type", "config"), CREDENTIAL_CONFIGS)
    def test_a_mode_that_needs_a_credential_still_reads_unknown_until_used(
        self, db_session: Any, conn_type: str, config: dict[str, Any]
    ) -> None:
        from backend.app.datasources.registry import get_connection_adapter

        get_connection_adapter(conn_type).validate_config(dict(config))
        conn = _connection(db_session, conn_type=conn_type, secret_ref="c-x", config=config)
        assert credential_health.credential_status(conn) == "unknown"

        credential_health.record_credential_success(db_session, connection_id=conn.id)
        db_session.refresh(conn)
        assert credential_health.credential_status(conn) == "healthy"

        credential_health.record_credential_failure(
            db_session, connection_id=conn.id, exc=RuntimeError("401 Unauthorized")
        )
        db_session.refresh(conn)
        assert credential_health.credential_status(conn) == "failing"

    def test_a_mode_that_needs_a_credential_but_has_none_stored_is_not_excused(
        self, db_session: Any
    ) -> None:
        # Missing, not unnecessary: the auth mode asks for a secret, so not "not applicable".
        conn = _connection(
            db_session,
            conn_type="trino",
            secret_ref=None,
            config={"host": "trino.example.com", "catalog": "hive", "user": "dq"},
        )
        assert credential_health.credential_status(conn) == "unknown"

    def test_a_type_that_never_declares_an_optional_secret_is_not_excused(
        self, db_session: Any
    ) -> None:
        conn = _connection(db_session, conn_type="snowflake", secret_ref=None, config={})
        assert credential_health.credential_status(conn) == "unknown"

    def test_a_config_that_no_longer_validates_is_unknown_not_excused(
        self, db_session: Any
    ) -> None:
        conn = _connection(
            db_session, conn_type="gcs", secret_ref=None, config={"auth_type": "anonymous"}
        )
        assert credential_health.credential_status(conn) == "unknown"

    @pytest.mark.parametrize(
        "extra",
        [{"catalog_secret_name": "catalog-pw"}, {"secret_property": "s3.secret-access-key"}],
    )
    def test_an_iceberg_catalog_with_a_credential_held_elsewhere_is_not_excused(
        self, db_session: Any, extra: dict[str, Any]
    ) -> None:
        """No `secret_ref`, but the config names a catalog password or a storage credential:
        one is expected, so "no credential needed" would be wrong."""
        config = {"catalog_type": "rest", "catalog_uri": "https://catalog.example.com", **extra}
        from backend.app.datasources.registry import get_connection_adapter

        get_connection_adapter("iceberg").validate_config(dict(config))
        conn = _connection(db_session, conn_type="iceberg", secret_ref=None, config=config)
        assert credential_health.credential_status(conn) == "unknown"

    def test_a_connection_switched_to_a_credential_less_mode_keeps_its_old_secret_unused(
        self, db_session: Any
    ) -> None:
        """The edit flow leaves the stored secret in place when the new config sends none.
        The mode decides: an unused secret is not a credential in use."""
        conn = _connection(
            db_session,
            conn_type="trino",
            secret_ref="c-old-password",
            config={
                "host": "trino.example.com",
                "catalog": "hive",
                "user": "dq",
                "auth_type": "none",
            },
        )
        assert credential_health.credential_status(conn) == "not_applicable"

    def test_failure_counters_from_a_credential_that_is_gone_are_not_reported(
        self, db_session: Any
    ) -> None:
        """A key was rejected three times, then the connection was switched to anonymous: the
        status is not_applicable, and so are the numbers beside it."""
        conn = _connection(
            db_session,
            conn_type="gcs",
            secret_ref=None,
            config={"bucket": "public-bucket", "auth_type": "anonymous"},
        )
        conn.consecutive_auth_failures = 3
        conn.last_auth_error = "credential rejected"

        assert credential_health.credential_status(conn) == "not_applicable"
        assert credential_health.observed_failures(conn) == (0, None)

    def test_failure_counters_are_reported_for_a_connection_that_has_a_credential(
        self, db_session: Any
    ) -> None:
        conn = _connection(db_session, conn_type="snowflake", secret_ref="c-x", config={})
        conn.consecutive_auth_failures = 3
        conn.last_auth_error = "credential rejected"
        assert credential_health.observed_failures(conn) == (3, "credential rejected")

    def test_an_orchestration_connection_is_never_not_applicable(self, db_session: Any) -> None:
        # dbt declares an optional secret too, but orchestration health is the poll signal.
        conn = _connection(
            db_session,
            conn_type="dbt",
            secret_ref=None,
            config={"artifacts_uri": "file:///tmp/target"},
        )
        assert credential_health.credential_status(conn) == "unknown"


class TestRecording:
    def test_failure_stores_a_classified_reason_never_the_driver_text(
        self, db_session: Any
    ) -> None:
        secret = "sig=LEAKMEPLEASE"
        credential = _connection(db_session)
        credential_health.record_credential_failure(
            db_session,
            connection_id=credential.id,
            exc=PermissionError(f"AuthenticationFailed: Signature did not match. {secret}"),
        )
        db_session.refresh(credential)
        assert credential.last_auth_error
        assert secret not in credential.last_auth_error
        assert "rotate it" in credential.last_auth_error.lower()

    def test_consecutive_failures_accumulate(self, db_session: Any) -> None:
        conn = _connection(db_session)
        for _ in range(3):
            credential_health.record_credential_failure(
                db_session, connection_id=conn.id, exc=RuntimeError("http 401")
            )
        db_session.refresh(conn)
        assert conn.consecutive_auth_failures == 3

    def test_success_resets_the_streak_and_clears_the_error(self, db_session: Any) -> None:
        # The admin-facing half of the contract: a re-auth must clear the signal on the
        # same request, not leave a stale red badge over a credential that now works.
        conn = _connection(db_session)
        for _ in range(2):
            credential_health.record_credential_failure(
                db_session, connection_id=conn.id, exc=RuntimeError("http 401")
            )
        db_session.refresh(conn)
        assert conn.consecutive_auth_failures == 2

        credential_health.record_credential_success(db_session, connection_id=conn.id)

        db_session.refresh(conn)
        assert conn.consecutive_auth_failures == 0
        assert conn.last_auth_error is None
        assert conn.last_auth_success_at is not None
        assert credential_health.credential_status(conn) == "healthy"


class TestSeam:
    def test_a_clean_block_records_success(self, db_session: Any) -> None:
        conn = _connection(db_session)
        with credential_health.credential_use(db_session, conn):
            pass
        db_session.refresh(conn)
        assert credential_health.credential_status(conn) == "healthy"

    def test_an_auth_exception_records_a_failure_and_still_propagates(
        self, db_session: Any
    ) -> None:
        conn = _connection(db_session)
        with pytest.raises(RuntimeError):
            with credential_health.credential_use(db_session, conn):
                raise RuntimeError("390100 (08004): Incorrect username or password was specified.")
        db_session.refresh(conn)
        assert conn.consecutive_auth_failures == 1
        assert credential_health.credential_status(conn) == "failing"

    def test_a_non_auth_exception_leaves_the_signal_untouched(self, db_session: Any) -> None:
        conn = _connection(db_session)
        with pytest.raises(RuntimeError):
            with credential_health.credential_use(db_session, conn):
                raise RuntimeError("Object 'ORDERS' does not exist or not authorized.")
        db_session.refresh(conn)
        assert conn.consecutive_auth_failures == 0
        assert conn.last_auth_success_at is None
        # Not "healthy" either: a config error proves nothing about the credential.
        assert credential_health.credential_status(conn) == "unknown"

    def test_a_swallowed_failure_reported_via_the_handle_is_recorded(self, db_session: Any) -> None:
        # The run path turns a failure into a `failed` run rather than re-raising, so a
        # clean exit would otherwise be recorded as a working credential.
        conn = _connection(db_session)
        with credential_health.credential_use(db_session, conn) as credential:
            try:
                raise RuntimeError("http 401 unauthorized")
            except RuntimeError as exc:
                credential.failed(exc)
        db_session.refresh(conn)
        assert credential_health.credential_status(conn) == "failing"
        assert conn.last_auth_success_at is None

    def test_a_reported_non_auth_failure_does_not_become_a_success(self, db_session: Any) -> None:
        conn = _connection(db_session)
        with credential_health.credential_use(db_session, conn) as credential:
            try:
                raise RuntimeError("table not found")
            except RuntimeError as exc:
                credential.failed(exc)
        db_session.refresh(conn)
        assert credential_health.credential_status(conn) == "unknown"

    def test_an_orchestration_connection_is_left_alone(self, db_session: Any) -> None:
        # Orchestration providers have #828's poll signal; recording a second, different
        # health axis on them would make two surfaces disagree about one connection.
        conn = _connection(db_session, conn_type="airflow")
        with credential_health.credential_use(db_session, conn):
            pass
        db_session.refresh(conn)
        assert conn.last_auth_success_at is None
        assert credential_health.is_datasource(conn.type) is False

    def test_a_credential_less_connection_is_left_alone(self, db_session: Any) -> None:
        # Managed identity / IAM role (ADR 0010/0011): there is no stored credential to
        # have a health, so the signal must stay silent rather than claim one.
        conn = _connection(db_session, secret_ref=None)
        with credential_health.credential_use(db_session, conn):
            pass
        db_session.refresh(conn)
        assert conn.last_auth_success_at is None
        assert credential_health.credential_status(conn) == "unknown"

    def test_a_missing_connection_is_a_no_op(self, db_session: Any) -> None:
        with credential_health.credential_use(db_session, None):
            pass  # nothing to record, and nothing raised
