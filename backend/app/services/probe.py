"""Idempotent seed for the Week 1 exit-gate probe.

The probe endpoint runs a canned suite against a single seeded dev Snowflake
connection. Connection CRUD and suite/check authoring arrive in Weeks 2–3; until
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

    suite = session.scalars(
        select(Suite).where(Suite.name == PROBE_SUITE_NAME, Suite.connection_id == connection.id)
    ).first()
    if suite is None:
        suite = Suite(
            name=PROBE_SUITE_NAME,
            description="Week 1 exit-gate probe suite",
            connection_id=connection.id,
            created_by=user.id,
        )
        session.add(suite)
        session.flush()  # populate suite.id for the check FK

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

    session.commit()
    return connection, suite, checks
