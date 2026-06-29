"""Suite export / import — portable, connection-agnostic suite documents.

A suite export is a plain dict (the API serialises it to JSON): the suite's
`name` / `description` plus every check's authoring fields. It deliberately
**omits** all DB-internal identity — `id`, `connection_id`, `created_by`,
timestamps — so a document is a reusable template, not a row dump. Import binds
the document to a *freshly chosen* connection and creates a new owned suite.

Why connection-agnostic: a suite's checks describe table/column expectations,
not a specific datasource row. Re-binding on import is the whole point (copy a
QA suite onto the UAT connection). The connection is supplied at import time and
validated like `create_suite` (422 on a missing connection).

Import is **atomic**: every check kind is validated *before* any row is written,
then the suite and its checks persist in a single commit — a bad document never
leaves a half-imported suite behind.

FastAPI-free like the sibling services: takes a `Session`, returns ORM models /
dicts, raises `DataQError` subclasses.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.datasources.monitors import MONITOR_KINDS
from backend.app.db.models import ORCHESTRATION_PROVIDERS, Check, Connection, Suite
from backend.app.services.check_service import (
    record_check_version,
    validate_kind,
    validate_monitor_check,
)
from backend.app.services.custom_sql import validate_custom_sql_check

log = get_logger(__name__)

# Bump when the document shape changes incompatibly; import refuses unknown
# versions rather than silently misreading an older/newer layout.
EXPORT_VERSION = 1


class SuiteImportInvalidError(DataQError):
    status_code = 422
    code = "suite_import_invalid"


class SuiteImportConnectionInvalidError(DataQError):
    status_code = 422
    code = "suite_import_connection_invalid"


def export_suite(suite: Suite) -> dict[str, Any]:
    """Build a portable document from an already-loaded, authorised suite.

    The API resolves and authorises the suite via `require_permission`, then
    passes it here. Checks are emitted in stable creation order so two exports of
    an unchanged suite are byte-identical (diffable in version control).
    """
    checks = sorted(suite.checks, key=lambda c: c.created_at)
    return {
        "version": EXPORT_VERSION,
        "name": suite.name,
        "description": suite.description,
        "checks": [
            {
                "name": c.name,
                "kind": c.kind,
                "expectation_type": c.expectation_type,
                "config": c.config,
                "warn_threshold": c.warn_threshold,
                "fail_threshold": c.fail_threshold,
                "critical_threshold": c.critical_threshold,
            }
            for c in checks
        ],
    }


def import_suite(
    session: Session,
    *,
    version: int,
    name: str,
    description: str | None,
    checks: list[dict[str, Any]],
    connection_id: uuid.UUID,
    created_by: uuid.UUID,
) -> Suite:
    """Create a new suite + checks from a document, bound to `connection_id`.

    Raises `SuiteImportInvalidError` (422) for an unsupported document version or
    an unsupported check kind, and `SuiteImportConnectionInvalidError` (422) if
    the target connection does not exist. Atomic: validates everything before
    writing, then commits the suite and all checks together.
    """
    if version != EXPORT_VERSION:
        raise SuiteImportInvalidError(
            f"unsupported export version {version!r}; this server imports v{EXPORT_VERSION}",
            detail={"version": version, "supported": EXPORT_VERSION},
        )
    connection = session.get(Connection, connection_id)
    if connection is None:
        raise SuiteImportConnectionInvalidError(
            "connection not found", detail={"connection_id": str(connection_id)}
        )
    if connection.type in ORCHESTRATION_PROVIDERS:
        # Orchestration providers (ADF/Airflow) are never suite datasources
        # (CLAUDE.md §4) — same guard as create_suite, applied at import time.
        raise SuiteImportConnectionInvalidError(
            "orchestration providers cannot be a suite's datasource; "
            "they trigger suites via trigger bindings",
            detail={"connection_id": str(connection_id), "type": connection.type},
        )
    # Validate every check (kind + custom-SQL / monitor guardrails) up front so a
    # bad document writes nothing. connection.type is known here, so the
    # datasource-gating + config validation that CRUD applies also applies at
    # import (custom-SQL: ADR 0019; freshness/volume monitors: ADR 0012).
    for c in checks:
        validate_kind(c["kind"])
        if c["kind"] in MONITOR_KINDS:
            validate_monitor_check(
                c["kind"],
                c["config"],
                connection_type=connection.type,
                fail_threshold=c["fail_threshold"],
                critical_threshold=c["critical_threshold"],
            )
        else:
            validate_custom_sql_check(
                expectation_type=c["expectation_type"],
                config=c["config"],
                connection_type=connection.type,
            )

    suite = Suite(
        name=name,
        description=description,
        connection_id=connection_id,
        created_by=created_by,
    )
    suite.checks = [
        Check(
            name=c["name"],
            kind=c["kind"],
            expectation_type=c["expectation_type"],
            config=c["config"],
            warn_threshold=c["warn_threshold"],
            fail_threshold=c["fail_threshold"],
            critical_threshold=c["critical_threshold"],
        )
        for c in checks
    ]
    session.add(suite)
    session.flush()  # assign check ids so each can carry a v1 snapshot (#280)
    for check in suite.checks:
        record_check_version(session, check, actor_id=created_by)
    session.commit()
    session.refresh(suite)
    log.info(
        "suite_imported",
        suite_id=str(suite.id),
        connection_id=str(connection_id),
        check_count=len(checks),
    )
    return suite
