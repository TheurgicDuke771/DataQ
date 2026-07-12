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

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.datasources.monitors import MONITOR_KINDS
from backend.app.db.models import COMPARISON_KIND, ORCHESTRATION_PROVIDERS, Check, Connection, Suite
from backend.app.services.check_service import (
    record_check_version,
    validate_comparison_check,
    validate_expectation_check,
    validate_kind,
    validate_monitor_check,
)
from backend.app.services.custom_sql import is_custom_sql, validate_custom_sql_check

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


def export_suite(session: Session, suite: Suite) -> dict[str, Any]:
    """Build a portable document from an already-loaded, authorised suite.

    The API resolves and authorises the suite via `require_permission`, then
    passes it here. Checks are emitted in stable creation order so two exports of
    an unchanged suite are byte-identical (diffable in version control).

    A comparison check's source ref (ADR 0015) is serialized portably as the
    connection's `(name, env)` — a raw UUID would never survive a workspace
    move; import resolves it back (or 422s). The key is emitted only for
    comparison checks, so pre-0015 documents and consumers are unaffected.
    """
    checks = sorted(suite.checks, key=lambda c: c.created_at)
    docs: list[dict[str, Any]] = []
    for c in checks:
        doc: dict[str, Any] = {
            "name": c.name,
            "kind": c.kind,
            "expectation_type": c.expectation_type,
            "config": c.config,
            "warn_threshold": c.warn_threshold,
            "fail_threshold": c.fail_threshold,
            "critical_threshold": c.critical_threshold,
        }
        if c.source_connection_id is not None:
            # RESTRICT FK: a referenced source connection cannot have been
            # deleted, so the row always resolves.
            source = session.get(Connection, c.source_connection_id)
            assert source is not None
            doc["source_connection"] = {"name": source.name, "env": source.env}
        docs.append(doc)
    return {
        "version": EXPORT_VERSION,
        "name": suite.name,
        "description": suite.description,
        "checks": docs,
    }


def _resolve_source_connection(session: Session, check_doc: dict[str, Any]) -> uuid.UUID:
    """Resolve a comparison check's portable source ref to a connection id.

    The document carries `source_connection: {"name", "env"}` (ADR 0015);
    `(name, env)` is unique (`uq_connections_name_env`), so at most one row
    matches. Missing key or no match → 422 naming the check, so a document
    exported elsewhere fails imports with an actionable error, not a stray FK.
    """
    ref = check_doc.get("source_connection")
    if not isinstance(ref, dict) or not ref.get("name") or not ref.get("env"):
        raise SuiteImportInvalidError(
            "a comparison check needs source_connection {name, env} in the document",
            detail={"check": check_doc.get("name")},
        )
    source_id = session.scalar(
        select(Connection.id).where(Connection.name == ref["name"], Connection.env == ref["env"])
    )
    if source_id is None:
        raise SuiteImportInvalidError(
            "comparison source connection not found on this workspace — create it "
            "(same name and env) before importing",
            detail={"check": check_doc.get("name"), "source_connection": ref},
        )
    return source_id


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
    # Validate every check (kind + custom-SQL / monitor / comparison guardrails)
    # up front so a bad document writes nothing. connection.type is known here,
    # so the datasource-gating + config validation that CRUD applies also applies
    # at import (custom-SQL: ADR 0019; freshness/volume monitors: ADR 0012;
    # comparison source refs: ADR 0015). Comparison source refs travel as
    # `(name, env)` and resolve to a connection id per check (index-aligned with
    # `checks` for the construction below).
    source_ids: list[uuid.UUID | None] = []
    for c in checks:
        validate_kind(c["kind"])
        source_ids.append(
            _resolve_source_connection(session, c) if c["kind"] == COMPARISON_KIND else None
        )
        if c["kind"] in MONITOR_KINDS:
            validate_monitor_check(
                c["kind"],
                c["config"],
                expectation_type=c["expectation_type"],
                connection_type=connection.type,
                fail_threshold=c["fail_threshold"],
                critical_threshold=c["critical_threshold"],
            )
        elif c["kind"] == COMPARISON_KIND:
            validate_comparison_check(
                session,
                config=c["config"],
                expectation_type=c["expectation_type"],
                source_connection_id=source_ids[-1],
                suite_connection_type=connection.type,
            )
        elif is_custom_sql(c["expectation_type"]):
            validate_custom_sql_check(
                expectation_type=c["expectation_type"],
                config=c["config"],
                connection_type=connection.type,
            )
        else:
            # Same author-time GX validation as check CRUD (#651) — an imported
            # document must not smuggle in checks a direct POST would 422.
            validate_expectation_check(c["expectation_type"], c["config"])

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
            source_connection_id=source_id,
            config=c["config"],
            warn_threshold=c["warn_threshold"],
            fail_threshold=c["fail_threshold"],
            critical_threshold=c["critical_threshold"],
        )
        for c, source_id in zip(checks, source_ids, strict=True)
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
