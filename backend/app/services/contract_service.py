"""Consumer-side data contracts (ADR 0052): import an ODCS v3 document, reconcile its suites.

A contract row is one VERSION of the document, stored verbatim. Importing it creates (or, for a
new version of a contract already linked to suites, reconciles) one suite per schema object on
the caller's connection, each carrying the checks the translation table produced. Every clause
ends in a check or in the contract's `unmapped` list — including a check the save-time
validators refuse, which is recorded with the validator's own message rather than failing the
whole import.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.app.contracts import odcs
from backend.app.contracts import translate as tr
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.datasources.monitors import SCHEMA_DRIFT
from backend.app.db.models import (
    CONTRACT_CHECK_ORIGIN,
    Check,
    Contract,
    Schedule,
    Suite,
)
from backend.app.services import audit_service, cron, suite_authz
from backend.app.services.check_dimension import derive_dimension
from backend.app.services.check_service import record_check_version
from backend.app.services.monitor_baseline import insert_baseline_if_absent
from backend.app.services.run_target import SuiteTargetInvalidError, validate_target
from backend.app.services.suite_io_service import (
    EXPORT_VERSION,
    _load_target_connection,
    _validate_check,
)

log = get_logger(__name__)


class ContractNotFoundError(DataQError):
    status_code = 404
    code = "contract_not_found"


class ContractVersionExistsError(DataQError):
    status_code = 409
    code = "contract_version_exists"


class ContractSuitesInvalidError(DataQError):
    status_code = 422
    code = "contract_suites_invalid"


@dataclass
class ImportOutcome:
    """What an import did — the REST/MCP response, and the audit payload's counts."""

    contract: Contract
    created_suite_ids: list[uuid.UUID] = field(default_factory=list)
    reconciled_suite_ids: list[uuid.UUID] = field(default_factory=list)
    #: Objects whose `physicalName` / `name` did not resolve to a run target on this connection:
    #: their suites exist (with checks) but cannot run until a target is set.
    untargeted_objects: list[str] = field(default_factory=list)
    checks_added: int = 0
    checks_updated: int = 0
    checks_disabled: int = 0
    warnings: list[str] = field(default_factory=list)


# ───────────────────────────── targets ──────────────────────────────


def _target_for(connection_type: str, physical: str) -> dict[str, Any] | None:
    """Map an ODCS `physicalName` (`table`, `schema.table`, `catalog.schema.table`, or a file
    path) onto this datasource's run-target shape, or `None` when it does not resolve."""
    parts = [p for p in physical.split(".") if p] if "/" not in physical else []
    target: dict[str, Any]
    if connection_type in {"adls_gen2", "s3", "gcs"}:
        target = {"path": physical}
    elif connection_type == "unity_catalog":
        if len(parts) != 3:
            return None
        target = {"catalog": parts[0], "schema": parts[1], "table": parts[2]}
    elif connection_type == "iceberg":
        if not parts:
            return None
        target = {"table": parts[-1]}
        if len(parts) > 1:
            target["namespace"] = ".".join(parts[:-1])
    else:
        if not parts or len(parts) > 2:
            return None
        target = {"table": parts[-1]}
        if len(parts) == 2:
            target["schema"] = parts[0]
    try:
        validate_target(connection_type, target)
    except SuiteTargetInvalidError:
        return None
    return target


# ───────────────────────────── checks ───────────────────────────────


def _document(draft: tr.CheckDraft) -> dict[str, Any]:
    """The draft in the suite-document vocabulary `_validate_check` reads."""
    return {
        "name": draft.name,
        "kind": draft.kind,
        "engine": draft.engine,
        "expectation_type": draft.expectation_type,
        "config": draft.config,
        "warn_threshold": draft.warn_threshold,
        "fail_threshold": draft.fail_threshold,
        "critical_threshold": draft.critical_threshold,
        "enabled": draft.enabled,
        "dimension": _dimension(draft),
    }


def _dimension(draft: tr.CheckDraft) -> str | None:
    if draft.unclassified:
        return None
    if draft.dimension is not None:
        return draft.dimension
    return derive_dimension(expectation_type=draft.expectation_type, kind=draft.kind)


def _clause_key(ref: dict[str, Any] | None) -> tuple[Any, ...] | None:
    if not ref:
        return None
    return (ref.get("object"), ref.get("property"), ref.get("rule_id"), ref.get("field"))


def _current_fingerprint(check: Check) -> str:
    return tr.fingerprint(
        check.expectation_type,
        check.config,
        check.warn_threshold,
        check.fail_threshold,
        check.critical_threshold,
    )


def _apply(check: Check, draft: tr.CheckDraft) -> None:
    check.name = draft.name
    check.expectation_type = draft.expectation_type
    check.engine = draft.engine
    check.config = draft.config
    check.warn_threshold = draft.warn_threshold
    check.fail_threshold = draft.fail_threshold
    check.critical_threshold = draft.critical_threshold
    check.dimension = _dimension(draft)
    check.enabled = draft.enabled
    check.contract_ref = draft.ref_dict()


def _new_check(draft: tr.CheckDraft, ordinal: int) -> Check:
    check = Check(kind=draft.kind, origin=CONTRACT_CHECK_ORIGIN, ordinal=ordinal)
    _apply(check, draft)
    return check


def _seed_declared_baseline(session: Session, check: Check, draft: tr.CheckDraft) -> None:
    if draft.kind == SCHEMA_DRIFT and draft.baseline is not None:
        insert_baseline_if_absent(
            session,
            check_id=check.id,
            kind=SCHEMA_DRIFT,
            baseline={"columns": draft.baseline, "declared": True},
        )


def _accepted_drafts(
    session: Session, connection: Any, obj: tr.TranslatedObject
) -> tuple[list[tr.CheckDraft], list[tr.Unmapped]]:
    """The drafts the save-time validators accept; a refused one becomes an unmapped clause
    carrying the validator's message (ADR 0052 §3: the validator has the last word)."""
    accepted: list[tr.CheckDraft] = []
    refused: list[tr.Unmapped] = []
    for draft in obj.checks:
        try:
            _validate_check(session, connection, _document(draft))
        except DataQError as exc:
            refused.append(
                tr.Unmapped(
                    object=draft.ref.object,
                    property=draft.ref.property,
                    rule_id=draft.ref.rule_id,
                    field=draft.ref.field,
                    reason=f"DataQ refused the check this clause maps to: {exc}",
                    clause={"expectation_type": draft.expectation_type},
                )
            )
            continue
        accepted.append(draft)
    return accepted, refused


def _merge_pii(suite: Suite, columns: list[str]) -> None:
    if not columns:
        return
    policy = dict(suite.column_policy or {})
    existing = [str(c) for c in policy.get("pii_columns") or []]
    merged = existing + [c for c in columns if c.lower() not in {e.lower() for e in existing}]
    policy["pii_columns"] = merged
    suite.column_policy = policy


@dataclass
class _ReconcileCounts:
    added: int = 0
    updated: int = 0
    disabled: int = 0


def _reconcile(
    session: Session,
    suite: Suite,
    drafts: list[tr.CheckDraft],
    *,
    actor_id: uuid.UUID,
) -> tuple[_ReconcileCounts, list[tuple[Check, tr.CheckDraft]]]:
    """ADR 0052 §6 / ADR 0047's rules: add checks for new clauses, update a clause-derived check
    only if nobody edited it since its import, disable (never delete) the checks whose clause
    disappeared, and leave every hand-authored check alone."""
    counts = _ReconcileCounts()
    created: list[tuple[Check, tr.CheckDraft]] = []
    by_clause = {
        _clause_key(c.contract_ref): c
        for c in suite.checks
        if c.origin == CONTRACT_CHECK_ORIGIN and c.contract_ref
    }
    seen: set[tuple[Any, ...] | None] = set()
    next_ordinal = max((c.ordinal or 0 for c in suite.checks), default=0) + 1
    for draft in drafts:
        key = _clause_key(draft.ref.as_dict())
        seen.add(key)
        existing = by_clause.get(key)
        if existing is None:
            check = _new_check(draft, next_ordinal)
            next_ordinal += 1
            suite.checks.append(check)
            created.append((check, draft))
            counts.added += 1
            continue
        stored = (existing.contract_ref or {}).get("fingerprint")
        unedited = stored is not None and stored == _current_fingerprint(existing)
        if unedited and stored != draft.fingerprint():
            _apply(existing, draft)
            record_check_version(session, existing, actor_id=actor_id)
            counts.updated += 1
    for key, check in by_clause.items():
        if key not in seen and check.enabled:
            check.enabled = False
            record_check_version(session, check, actor_id=actor_id)
            counts.disabled += 1
    return counts, created


# ───────────────────────────── import ───────────────────────────────


def import_contract(
    session: Session,
    *,
    document: dict[str, Any] | None,
    document_yaml: str | None,
    connection_id: uuid.UUID,
    actor_id: uuid.UUID,
    suite_ids: list[uuid.UUID] | None = None,
    replace: bool = False,
) -> ImportOutcome:
    """Validate, translate and store an ODCS document, then create or reconcile its suites.

    - A version already stored is a 409 unless ``replace`` (then its stored spec is replaced
      and its linked suites reconciled).
    - A new version with ``suite_ids`` re-points those suites (each must be linked to an earlier
      version of the same contract) and reconciles them; without, it creates fresh suites.
    """
    raw = odcs.load_document(document=document, document_yaml=document_yaml)
    validated = odcs.validate_document(raw)
    connection = _load_target_connection(session, EXPORT_VERSION, connection_id)
    translated = tr.translate(validated.document, connection_type=connection.type)

    spec = validated.document
    odcs_id = str(spec["id"])
    version = str(spec["version"])
    existing = session.scalar(
        select(Contract).where(Contract.odcs_id == odcs_id, Contract.version == version)
    )
    if existing is not None and not replace:
        raise ContractVersionExistsError(
            "this contract version is already imported; import a new version, or send "
            "replace=true to replace it",
            detail={"contract_id": str(existing.id), "odcs_id": odcs_id, "version": version},
        )

    if existing is not None:
        contract = existing
        targets = list(contract.suites)
    else:
        contract = Contract(odcs_id=odcs_id, version=version, created_by=actor_id)
        session.add(contract)
        targets = _suites_to_repoint(session, odcs_id, suite_ids or [], actor_id=actor_id)

    contract.name = str(spec.get("name") or odcs_id)[:256]
    contract.status = str(spec["status"])[:32] if spec.get("status") is not None else None
    contract.domain = str(spec["domain"])[:256] if spec.get("domain") is not None else None
    contract.tenant = str(spec["tenant"])[:256] if spec.get("tenant") is not None else None
    contract.api_version = str(spec["apiVersion"])
    contract.spec = spec
    session.flush()

    outcome = ImportOutcome(contract=contract, warnings=list(validated.warnings))
    unmapped: list[tr.Unmapped] = list(translated.unmapped)
    by_object = {s.contract_object: s for s in targets}
    new_checks: list[tuple[Check, tr.CheckDraft]] = []

    for obj in translated.objects:
        drafts, refused = _accepted_drafts(session, connection, obj)
        unmapped.extend(refused)
        suite = by_object.get(obj.name)
        if suite is None:
            target = _target_for(connection.type, obj.physical_name)
            if target is None:
                outcome.untargeted_objects.append(obj.name)
            suite = Suite(
                name=f"{contract.name} · {obj.name}"[:128],
                description=f"Enforces data contract {contract.name} {version}"[:1024],
                connection_id=connection.id,
                target=target,
                created_by=actor_id,
                contract_id=contract.id,
                contract_object=obj.name,
            )
            suite.checks = []
            session.add(suite)
            for ordinal, draft in enumerate(drafts, start=1):
                check = _new_check(draft, ordinal)
                suite.checks.append(check)
                new_checks.append((check, draft))
            outcome.checks_added += len(drafts)
            _merge_pii(suite, obj.pii_columns)
            session.flush()
            outcome.created_suite_ids.append(suite.id)
        else:
            suite.contract_id = contract.id
            suite.contract_object = obj.name
            counts, created = _reconcile(session, suite, drafts, actor_id=actor_id)
            new_checks.extend(created)
            outcome.checks_added += counts.added
            outcome.checks_updated += counts.updated
            outcome.checks_disabled += counts.disabled
            _merge_pii(suite, obj.pii_columns)
            session.flush()
            outcome.reconciled_suite_ids.append(suite.id)
        if obj.schedule_cron:
            _ensure_schedule(session, suite, obj, actor_id=actor_id, unmapped=unmapped)

    session.flush()
    for check, draft in new_checks:
        record_check_version(session, check, actor_id=actor_id)
        _seed_declared_baseline(session, check, draft)
    contract.unmapped = [u.as_dict() for u in unmapped]

    audit_service.record(
        session,
        action="contract.import",
        entity_type="contract",
        entity_id=contract.id,
        actor=actor_id,
        after={
            "id": str(contract.id),
            "odcs_id": odcs_id,
            "version": version,
            "replaced": existing is not None,
            "suites_created": len(outcome.created_suite_ids),
            "suites_reconciled": len(outcome.reconciled_suite_ids),
            "checks_added": outcome.checks_added,
            "checks_updated": outcome.checks_updated,
            "checks_disabled": outcome.checks_disabled,
            "unmapped": len(unmapped),
        },
    )
    session.commit()
    session.refresh(contract)
    log.info(
        "contract_imported",
        contract_id=str(contract.id),
        suites_created=len(outcome.created_suite_ids),
        suites_reconciled=len(outcome.reconciled_suite_ids),
        unmapped=len(unmapped),
    )
    return outcome


def _suites_to_repoint(
    session: Session, odcs_id: str, suite_ids: list[uuid.UUID], *, actor_id: uuid.UUID
) -> list[Suite]:
    """The suites a new version takes over: each `edit`-able and linked to an earlier version
    of the SAME contract (422 otherwise — re-pointing a stranger's suite is not an import)."""
    suites: list[Suite] = []
    for suite_id in dict.fromkeys(suite_ids):
        suite = suite_authz.require_permission(session, suite_id, actor_id, minimum="edit")
        linked = session.get(Contract, suite.contract_id) if suite.contract_id else None
        if linked is None or linked.odcs_id != odcs_id:
            raise ContractSuitesInvalidError(
                "a suite named in suite_ids is not linked to an earlier version of this contract",
                detail={"suite_id": str(suite_id)},
            )
        suites.append(suite)
    return suites


def _ensure_schedule(
    session: Session,
    suite: Suite,
    obj: tr.TranslatedObject,
    *,
    actor_id: uuid.UUID,
    unmapped: list[tr.Unmapped],
) -> None:
    """The object's `dataq.schedule` cron — created once; an existing schedule on the suite is
    left alone (a person may have tuned it). A cron the scheduler refuses is unmapped."""
    if session.scalar(select(Schedule.id).where(Schedule.suite_id == suite.id).limit(1)):
        return
    try:
        next_run_at = cron.next_fire(obj.schedule_cron or "", obj.schedule_timezone)
    except DataQError as exc:
        unmapped.append(
            tr.Unmapped(
                object=obj.name,
                field="customProperties.dataq.schedule",
                reason=f"the scheduler refused this cron: {exc}",
                clause={"schedule": obj.schedule_cron},
            )
        )
        return
    schedule = Schedule(
        suite_id=suite.id,
        cron=obj.schedule_cron,
        timezone=obj.schedule_timezone,
        enabled=True,
        next_run_at=next_run_at,
    )
    schedule.created_by = actor_id
    session.add(schedule)
    audit_service.record_entity_change(
        session, action="schedule.create", entity_type="schedule", entity=schedule, actor=actor_id
    )


# ───────────────────────────── reads ────────────────────────────────


@dataclass
class VisibleContract:
    contract: Contract
    suites: list[Suite]
    restricted_suite_count: int


def _visible(session: Session, contract: Contract, user_id: uuid.UUID) -> VisibleContract | None:
    """A contract is visible when the caller can `view` at least one linked suite, or is a
    workspace admin (who also sees a contract with no linked suite)."""
    suites = list(contract.suites)
    perms = suite_authz.effective_permissions(session, suites, user_id)
    visible = [s for s in suites if perms.get(s.id) is not None]
    is_admin = suite_authz.is_workspace_admin(session, user_id)
    if not visible and not is_admin:
        return None
    return VisibleContract(contract, visible, len(suites) - len(visible))


def list_contracts(session: Session, user_id: uuid.UUID) -> list[VisibleContract]:
    rows = session.scalars(
        select(Contract)
        .options(selectinload(Contract.suites))
        .order_by(Contract.name, Contract.version)
    )
    return [v for c in rows if (v := _visible(session, c, user_id)) is not None]


def get_contract(session: Session, contract_id: uuid.UUID, user_id: uuid.UUID) -> VisibleContract:
    """404 — never 403 — for a contract the caller cannot see (ADR 0027's no-leak rule)."""
    contract = session.get(Contract, contract_id)
    visible = _visible(session, contract, user_id) if contract is not None else None
    if visible is None:
        raise ContractNotFoundError("contract not found", detail={"contract_id": str(contract_id)})
    return visible


def delete_contract(session: Session, contract_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Unlink the contract's suites (both columns, together) and delete the row; suites, checks
    and results stay. Requires `edit` on every linked suite — unlinking is a suite mutation."""
    visible = get_contract(session, contract_id, user_id)
    for suite in visible.contract.suites:
        suite_authz.require_permission(session, suite.id, user_id, minimum="edit")
    for suite in visible.contract.suites:
        suite.contract_id = None
        suite.contract_object = None
    audit_service.record(
        session,
        action="contract.delete",
        entity_type="contract",
        entity_id=visible.contract.id,
        actor=user_id,
        before={
            "id": str(visible.contract.id),
            "odcs_id": visible.contract.odcs_id,
            "version": visible.contract.version,
            "suites_unlinked": len(visible.contract.suites),
        },
    )
    session.delete(visible.contract)
    session.commit()
