"""Suite export / import — portable, connection-agnostic suite documents."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.datasources.databricks_dqx import DQX_ENGINE
from backend.app.datasources.monitors import MONITOR_KINDS
from backend.app.datasources.snowflake_dmf import DMF_ENGINE
from backend.app.db.models import (
    COMPARISON_KIND,
    GX_ENGINE,
    ORCHESTRATION_PROVIDERS,
    Check,
    Connection,
    Suite,
)
from backend.app.services import audit_service
from backend.app.services.check_dimension import derive_dimension
from backend.app.services.check_service import (
    record_check_version,
    reject_dataframe_only_expectation,
    reject_thresholds_on_unbanded,
    validate_comparison_check,
    validate_dimension,
    validate_engine,
    validate_engine_compatibility,
    validate_expectation_check,
    validate_kind,
    validate_lengths,
    validate_monitor_check,
    validate_threshold_ordering,
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
    """Build a portable document from an already-loaded, authorised suite."""
    checks = sorted(suite.checks, key=lambda c: c.created_at)
    docs: list[dict[str, Any]] = []
    for c in checks:
        doc: dict[str, Any] = {
            "name": c.name,
            "kind": c.kind,
            "expectation_type": c.expectation_type,
            "dimension": c.dimension,
            "config": c.config,
            "warn_threshold": c.warn_threshold,
            "fail_threshold": c.fail_threshold,
            "critical_threshold": c.critical_threshold,
        }
        # Emitted only when non-default (ADR 0036), like `source_connection` below: pre-engine
        # documents and consumers stay byte-identical.
        if c.engine != GX_ENGINE:
            doc["engine"] = c.engine
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
    """Resolve a comparison check's portable source ref to a connection id."""
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


def _load_target_connection(session: Session, version: int, connection_id: uuid.UUID) -> Connection:
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
    return connection


def _validate_check(
    session: Session, connection: Connection, c: dict[str, Any]
) -> uuid.UUID | None:
    """One document check through the save-time gates check CRUD applies (kind +
    custom-SQL / monitor / comparison guardrails). Returns a comparison check's
    resolved source connection id.
    """
    validate_kind(c["kind"])
    # ADR 0036 §5: a document carrying a native-engine check imports only where the target
    # connection offers that engine — the same save-time validation as CRUD.
    validate_engine(c.get("engine", GX_ENGINE), connection_type=connection.type)
    validate_engine_compatibility(
        c.get("engine", GX_ENGINE),
        kind=c["kind"],
        expectation_type=c["expectation_type"],
        config=c["config"],
        warn_threshold=c["warn_threshold"],
        fail_threshold=c["fail_threshold"],
        critical_threshold=c["critical_threshold"],
    )
    # Direct `Check(...)` construction below has no Pydantic layer of its own — today the REST
    # import route's `CheckDocumentIn` model already enforces the same 256/128 bounds.
    validate_lengths(name=c["name"], expectation_type=c["expectation_type"])
    # #568: an imported document must not smuggle in what a direct POST
    # would 422 — same shared validator create_check/update_check use.
    validate_threshold_ordering(
        warn_threshold=c["warn_threshold"],
        fail_threshold=c["fail_threshold"],
        critical_threshold=c["critical_threshold"],
    )
    source_id = _resolve_source_connection(session, c) if c["kind"] == COMPARISON_KIND else None
    if c["kind"] in MONITOR_KINDS:
        validate_monitor_check(
            c["kind"],
            c["config"],
            expectation_type=c["expectation_type"],
            connection_type=connection.type,
            warn_threshold=c["warn_threshold"],
            fail_threshold=c["fail_threshold"],
            critical_threshold=c["critical_threshold"],
        )
    elif c["kind"] == COMPARISON_KIND:
        validate_comparison_check(
            session,
            config=c["config"],
            expectation_type=c["expectation_type"],
            source_connection_id=source_id,
            suite_connection_type=connection.type,
        )
    elif is_custom_sql(c["expectation_type"]):
        validate_custom_sql_check(
            expectation_type=c["expectation_type"],
            config=c["config"],
            connection_type=connection.type,
        )
    elif c.get("engine", GX_ENGINE) in (DMF_ENGINE, DQX_ENGINE):
        # A dmf:* / dqx:* check — fully validated by validate_engine_compatibility above;
        # not a GX expectation.
        pass
    else:
        # Same author-time GX validation as check CRUD (#651) — an imported
        # document must not smuggle in checks a direct POST would 422.
        validate_expectation_check(c["expectation_type"], c["config"])
        reject_dataframe_only_expectation(
            c["expectation_type"],
            connection_type=connection.type,
            connection_config=dict(connection.config or {}),
        )
    reject_thresholds_on_unbanded(
        c["expectation_type"],
        warn_threshold=c["warn_threshold"],
        fail_threshold=c["fail_threshold"],
        critical_threshold=c["critical_threshold"],
    )
    if "dimension" in c:
        validate_dimension(c["dimension"])
    return source_id


@dataclass(frozen=True)
class DocumentProblem:
    """One reason a document would be refused. `check_index` is None for a problem with
    the document as a whole (its version, its target connection).
    """

    code: str
    message: str
    check_index: int | None = None
    check_name: str | None = None


def validate_document(
    session: Session, *, version: int, checks: list[dict[str, Any]], connection_id: uuid.UUID
) -> list[DocumentProblem]:
    """Everything `import_suite` would refuse this document for, without stopping at the
    first and without writing anything (#1688). Empty means the import would be accepted.
    """
    try:
        connection = _load_target_connection(session, version, connection_id)
    except DataQError as exc:
        # Every per-check gate depends on the connection's type, so there is nothing
        # further to say until this is fixed.
        return [DocumentProblem(code=exc.code, message=exc.message)]
    problems = []
    for index, c in enumerate(checks):
        try:
            _validate_check(session, connection, c)
        except DataQError as exc:
            problems.append(
                DocumentProblem(
                    code=exc.code, message=exc.message, check_index=index, check_name=c["name"]
                )
            )
    return problems


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
    """Create a new suite + checks from a document, bound to `connection_id`."""
    connection = _load_target_connection(session, version, connection_id)
    # Validate every check up front so a bad document writes nothing.
    source_ids = [_validate_check(session, connection, c) for c in checks]

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
            engine=c.get("engine", GX_ENGINE),
            expectation_type=c["expectation_type"],
            # Key ABSENT (an older document) → derive, so an import behaves like fresh authoring.
            dimension=(
                validate_dimension(c["dimension"])
                if "dimension" in c
                else derive_dimension(expectation_type=c["expectation_type"], kind=c["kind"])
            ),
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
    # ONE event for the import, not one per check.
    audit_service.record_entity_change(
        session,
        action="suite.import",
        entity_type="suite",
        entity=suite,
        actor=created_by,
    )
    session.commit()
    session.refresh(suite)
    log.info(
        "suite_imported",
        suite_id=str(suite.id),
        connection_id=str(connection_id),
        check_count=len(checks),
    )
    return suite
