"""Idempotent seed for the Week 1 exit-gate probe.

The probe endpoint runs a canned suite against a single seeded dev Snowflake
connection. Connection CRUD and suite/check authoring arrive in Weeks 2-3; until
then this get-or-creates the fixtures so the endpoint is safe to hit repeatedly.

The canned check is column-agnostic (a row-count bound) so it works against any
configured table without assuming a schema.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.db.models import Check, Connection, Suite, User
from backend.app.services import audit_service

PROBE_CONNECTION_NAME = "probe-snowflake-dev"
PROBE_SUITE_NAME = "probe-snowflake-suite"
PROBE_ENV = "dev"

# (name, expectation_type, config) — kept column-agnostic for now.
_PROBE_CHECKS: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("row_count_positive", "expect_table_row_count_to_be_between", {"min_value": 1}),
)


def _connection_config(settings: Settings) -> dict[str, Any]:
    return {
        "account": settings.probe_snowflake_account,
        "user": settings.probe_snowflake_user,
        "database": settings.probe_snowflake_database,
        "schema": settings.probe_snowflake_schema,
        "warehouse": settings.probe_snowflake_warehouse,
        "role": settings.probe_snowflake_role,
    }


def ensure_probe_fixtures(
    session: Session, *, user: User, settings: Settings
) -> tuple[Connection, Suite, list[Check]]:
    """Get-or-create the probe Connection, Suite, and Checks. Idempotent."""
    connection = session.scalars(
        select(Connection).where(
            Connection.name == PROBE_CONNECTION_NAME, Connection.env == PROBE_ENV
        )
    ).first()
    if connection is None:
        connection = Connection(
            name=PROBE_CONNECTION_NAME,
            type="snowflake",
            env=PROBE_ENV,
            config=_connection_config(settings),
            secret_ref=settings.probe_snowflake_secret_ref,
            created_by=user.id,
        )
        session.add(connection)
        session.flush()  # populate connection.id for the suite FK

    # The run target (#215) — the probe table the suite's checks run against,
    # from settings. NULL when no probe table is configured (the run then fails
    # cleanly with `suite_target_invalid` rather than guessing a table).
    target = {"table": settings.probe_snowflake_table} if settings.probe_snowflake_table else None

    suite = session.scalars(
        select(Suite).where(Suite.name == PROBE_SUITE_NAME, Suite.connection_id == connection.id)
    ).first()
    if suite is None:
        suite = Suite(
            name=PROBE_SUITE_NAME,
            description="Week 1 exit-gate probe suite",
            connection_id=connection.id,
            created_by=user.id,
            target=target,
        )
        session.add(suite)
        session.flush()  # populate suite.id for the check FK
    elif target is not None:
        # Backfill a suite seeded before the target column existed, but never
        # auto-clear an already-configured target just because the env setting
        # is currently unset.
        suite.target = target

    checks = list(session.scalars(select(Check).where(Check.suite_id == suite.id)))
    existing_names = {c.name for c in checks}
    for name, expectation_type, config in _PROBE_CHECKS:
        if name not in existing_names:
            check = Check(
                suite_id=suite.id,
                name=name,
                expectation_type=expectation_type,
                config=dict(config),
            )
            session.add(check)
            checks.append(check)

    # ONE event for the whole provisioning act, and it exists because of how this
    # endpoint was found: the ADR-0033 RBAC review (#1396) caught it as a THIRD
    # DOOR — it creates a Connection AND a Suite under a name that mentions
    # neither, so reasoning that put controls on those resources' own routes was
    # structurally blind to it. Auditing the resources rather than the routes
    # would have had the identical blind spot, so the act is recorded here by
    # name. `entity_type` is the suite because that is what the act produces that
    # a reader would go looking for; the connection id rides in the payload.
    audit_service.record(
        session,
        action="probe.provision",
        entity_type="suite",
        entity_id=suite.id,
        actor=user,
        after={
            "suite_id": str(suite.id),
            "suite_name": suite.name,
            "connection_id": str(connection.id),
            "connection_name": connection.name,
            "check_count": len(checks),
        },
    )
    session.commit()
    return connection, suite, checks
