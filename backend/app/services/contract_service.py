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
    CHECK_ORDER,
    CONTRACT_CHECK_ORIGIN,
    RESULT_SEVERITY_TIERS,
    Check,
    Connection,
    Contract,
    Result,
    Schedule,
    Suite,
)
from backend.app.services import audit_service, cron, suite_authz
from backend.app.services.check_dimension import derive_dimension
from backend.app.services.check_service import record_check_version
from backend.app.services.monitor_baseline import get_baseline, insert_baseline_if_absent
from backend.app.services.rollup import AGGREGATABLE_RUN_STATUSES, latest_runs_per_suite_stmt
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


class ContractForbiddenError(DataQError):
    status_code = 403
    code = "contract_forbidden"


def _require_edit_on_all(session: Session, contract: Contract, user_id: uuid.UUID) -> None:
    """`edit` on every suite the contract governs. A suite the caller cannot even view is
    refused WITHOUT naming it (ADR 0037: restricted suites are counted, never identified)."""
    suites = list(contract.suites)
    perms = suite_authz.effective_permissions(session, suites, user_id)
    if any(perms.get(s.id) is None for s in suites):
        raise ContractForbiddenError(
            "this contract governs suites you cannot see; only someone who can edit all of them "
            "may change it",
            detail={"contract_id": str(contract.id)},
        )
    for suite in suites:
        suite_authz.require_permission(session, suite.id, user_id, minimum="edit")


@dataclass
class ImportOutcome:
    """What an import did — the REST/MCP response, and the audit payload's counts."""

    contract: Contract
    created_suite_ids: list[uuid.UUID] = field(default_factory=list)
    reconciled_suite_ids: list[uuid.UUID] = field(default_factory=list)
    #: Suites taken over for a schema object this version dropped: unlinked, contract checks off.
    retired_suite_ids: list[uuid.UUID] = field(default_factory=list)
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
        engine=check.engine,
    )


#: Set on `contract_ref` when a re-import switched the check off because its clause left the
#: contract — the only off-switch a later version may undo. A check a PERSON switched off has no
#: such mark, and an import never turns it back on.
DISABLED_BY_CONTRACT = "disabled_by_contract"


def _apply(check: Check, draft: tr.CheckDraft, *, odcs_id: str, set_enabled: bool = True) -> None:
    check.name = draft.name
    check.expectation_type = draft.expectation_type
    check.engine = draft.engine
    check.config = draft.config
    check.warn_threshold = draft.warn_threshold
    check.fail_threshold = draft.fail_threshold
    check.critical_threshold = draft.critical_threshold
    check.dimension = _dimension(draft)
    if set_enabled:
        check.enabled = draft.enabled
    # The contract's own id travels with the clause, so a suite later linked to a DIFFERENT
    # contract never has this clause reported under that contract's name.
    check.contract_ref = {**draft.ref_dict(), "odcs_id": odcs_id}


def _new_check(draft: tr.CheckDraft, ordinal: int, *, odcs_id: str) -> Check:
    check = Check(kind=draft.kind, origin=CONTRACT_CHECK_ORIGIN, ordinal=ordinal)
    _apply(check, draft, odcs_id=odcs_id)
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


def _refresh_declared_baseline(session: Session, check: Check, draft: tr.CheckDraft) -> bool:
    """A new version's declared columns replace a DECLARED baseline (never a learned one — that
    is what a run observed, and a person may have re-baselined it on purpose)."""
    if draft.kind != SCHEMA_DRIFT or draft.baseline is None:
        return False
    row = get_baseline(session, check.id, for_update=True)
    if row is None:
        _seed_declared_baseline(session, check, draft)
        return True
    if not row.baseline.get("declared") or row.baseline.get("columns") == draft.baseline:
        return False
    row.baseline = {"columns": draft.baseline, "declared": True}
    return True


def _reconcile(
    session: Session,
    suite: Suite,
    drafts: list[tr.CheckDraft],
    *,
    odcs_id: str,
    actor_id: uuid.UUID,
) -> tuple[_ReconcileCounts, list[tuple[Check, tr.CheckDraft]]]:
    """ADR 0052 §6 / ADR 0047's rules: add checks for new clauses, update a clause-derived check
    only if nobody edited it since its import, disable (never delete) the checks whose clause
    disappeared, and leave every hand-authored check alone.

    Switching on and off: a clause that returns re-enables only a check an earlier import switched
    off (`DISABLED_BY_CONTRACT`); a check a person switched off stays off, even when its clause
    changes."""
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
            check = _new_check(draft, next_ordinal, odcs_id=odcs_id)
            next_ordinal += 1
            suite.checks.append(check)
            created.append((check, draft))
            counts.added += 1
            continue
        ref = existing.contract_ref or {}
        reopened = bool(ref.get(DISABLED_BY_CONTRACT)) and not existing.enabled
        stored = ref.get("fingerprint")
        unedited = stored is not None and stored == _current_fingerprint(existing)
        changed = False
        if unedited and stored != draft.fingerprint():
            _apply(existing, draft, odcs_id=odcs_id, set_enabled=False)
            changed = True
        if reopened:
            existing.enabled = True
            existing.contract_ref = {
                k: v for k, v in (existing.contract_ref or {}).items() if k != DISABLED_BY_CONTRACT
            }
            changed = True
        if unedited and _refresh_declared_baseline(session, existing, draft):
            changed = True
        if changed:
            record_check_version(session, existing, actor_id=actor_id)
            counts.updated += 1
    for key, check in by_clause.items():
        if key not in seen and check.enabled:
            _retire(session, check, actor_id=actor_id)
            counts.disabled += 1
    return counts, created


def _retire(session: Session, check: Check, *, actor_id: uuid.UUID) -> None:
    check.enabled = False
    check.contract_ref = {**(check.contract_ref or {}), DISABLED_BY_CONTRACT: True}
    record_check_version(session, check, actor_id=actor_id)


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
    if existing is not None:
        # Replacing a stored version is a write to every suite it governs: the caller must see the
        # contract (404 otherwise — the same no-leak rule as a read) and edit each linked suite.
        if _visible(session, existing, actor_id) is None:
            raise ContractVersionExistsError(
                "this contract version is already imported",
                detail={"odcs_id": odcs_id, "version": version},
            )
        if not replace:
            raise ContractVersionExistsError(
                "this contract version is already imported; import a new version, or send "
                "replace=true to replace it",
                detail={"contract_id": str(existing.id), "odcs_id": odcs_id, "version": version},
            )
        _require_edit_on_all(session, existing, actor_id)
        contract = existing
        targets = list(contract.suites)
    else:
        targets = _suites_to_repoint(session, odcs_id, suite_ids or [], actor_id=actor_id)
        contract = Contract(odcs_id=odcs_id, version=version, created_by=actor_id)
    # A reconciled suite keeps its own connection; its checks were validated against the import's,
    # so the two must be the same connection (a different engine could refuse what this accepted).
    for linked in targets:
        if linked.connection_id != connection.id:
            raise ContractSuitesInvalidError(
                "a suite this import would reconcile runs on a different connection; import "
                "against that suite's connection",
                detail={"suite_id": str(linked.id), "connection_id": str(linked.connection_id)},
            )

    contract.name = str(spec.get("name") or odcs_id)[:256]
    contract.status = str(spec["status"])[:32] if spec.get("status") is not None else None
    contract.domain = str(spec["domain"])[:256] if spec.get("domain") is not None else None
    contract.tenant = str(spec["tenant"])[:256] if spec.get("tenant") is not None else None
    contract.api_version = str(spec["apiVersion"])
    contract.spec = spec
    session.add(contract)
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
                check = _new_check(draft, ordinal, odcs_id=contract.odcs_id)
                suite.checks.append(check)
                new_checks.append((check, draft))
            outcome.checks_added += len(drafts)
            _merge_pii(suite, obj.pii_columns)
            session.flush()
            outcome.created_suite_ids.append(suite.id)
        else:
            suite.contract_id = contract.id
            suite.contract_object = obj.name
            counts, created = _reconcile(
                session, suite, drafts, odcs_id=contract.odcs_id, actor_id=actor_id
            )
            new_checks.extend(created)
            outcome.checks_added += counts.added
            outcome.checks_updated += counts.updated
            outcome.checks_disabled += counts.disabled
            _merge_pii(suite, obj.pii_columns)
            session.flush()
            outcome.reconciled_suite_ids.append(suite.id)
        if obj.schedule_cron:
            _ensure_schedule(session, suite, obj, actor_id=actor_id, unmapped=unmapped)

    # A suite taken over for an object this version no longer has: its contract checks are switched
    # off (never deleted) and the suite is unlinked, so nothing keeps enforcing a dropped object.
    current = {obj.name for obj in translated.objects}
    for suite in targets:
        if suite.contract_object in current:
            continue
        for check in suite.checks:
            if check.origin == CONTRACT_CHECK_ORIGIN and check.enabled:
                _retire(session, check, actor_id=actor_id)
                outcome.checks_disabled += 1
        outcome.retired_suite_ids.append(suite.id)
        suite.contract_id = None
        suite.contract_object = None

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
            "suites_retired": len(outcome.retired_suite_ids),
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
        if any(s.contract_object == suite.contract_object for s in suites):
            raise ContractSuitesInvalidError(
                "two suites in suite_ids stand for the same schema object; name one",
                detail={"suite_id": str(suite_id), "contract_object": suite.contract_object},
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
    #: The newest imported version of this `odcs_id` (by import time) — what "the current
    #: contract" means when several versions are stored.
    latest_version: bool = True


def _visible_from(
    contract: Contract, perms: dict[uuid.UUID, str | None], *, is_admin: bool
) -> VisibleContract | None:
    suites = list(contract.suites)
    visible = [s for s in suites if perms.get(s.id) is not None]
    if not visible and not is_admin:
        return None
    return VisibleContract(contract, visible, len(suites) - len(visible))


def _visible(session: Session, contract: Contract, user_id: uuid.UUID) -> VisibleContract | None:
    """A contract is visible when the caller can `view` at least one linked suite, or is a
    workspace admin (who also sees a contract with no linked suite)."""
    perms = suite_authz.effective_permissions(session, list(contract.suites), user_id)
    return _visible_from(contract, perms, is_admin=suite_authz.is_workspace_admin(session, user_id))


def _latest_ids(session: Session, odcs_ids: set[str]) -> set[uuid.UUID]:
    """The newest-imported row per `odcs_id` (a version string does not sort: 1.10 < 1.9)."""
    if not odcs_ids:
        return set()
    rows = session.execute(
        select(Contract.id)
        .where(Contract.odcs_id.in_(odcs_ids))
        .order_by(Contract.odcs_id, Contract.created_at.desc(), Contract.id.desc())
        .distinct(Contract.odcs_id)
    ).scalars()
    return set(rows)


def list_contracts(
    session: Session, user_id: uuid.UUID, *, limit: int | None = None, offset: int = 0
) -> tuple[list[VisibleContract], int]:
    """The caller's visible contract versions — grouped by `odcs_id`, newest import first —
    and how many there are in all. One permissions query and one role lookup, whatever the
    count."""
    contracts = list(
        session.scalars(
            select(Contract)
            .options(selectinload(Contract.suites))
            .order_by(Contract.odcs_id, Contract.created_at.desc(), Contract.id.desc())
        )
    )
    suites = [s for c in contracts for s in c.suites]
    perms = suite_authz.effective_permissions(session, suites, user_id) if suites else {}
    is_admin = suite_authz.is_workspace_admin(session, user_id)
    latest = _latest_ids(session, {c.odcs_id for c in contracts})
    visible = []
    for c in contracts:
        v = _visible_from(c, perms, is_admin=is_admin)
        if v is not None:
            v.latest_version = c.id in latest
            visible.append(v)
    page = visible[offset:] if limit is None else visible[offset : offset + limit]
    return page, len(visible)


def get_contract(session: Session, contract_id: uuid.UUID, user_id: uuid.UUID) -> VisibleContract:
    """404 — never 403 — for a contract the caller cannot see (ADR 0027's no-leak rule)."""
    contract = session.get(Contract, contract_id)
    visible = _visible(session, contract, user_id) if contract is not None else None
    if visible is None:
        raise ContractNotFoundError("contract not found", detail={"contract_id": str(contract_id)})
    visible.latest_version = visible.contract.id in _latest_ids(session, {visible.contract.odcs_id})
    return visible


def delete_contract(session: Session, contract_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Unlink the contract's suites (both columns, together) and delete the row; suites, checks
    and results stay. Requires `edit` on every linked suite — unlinking is a suite mutation."""
    visible = get_contract(session, contract_id, user_id)
    _require_edit_on_all(session, visible.contract, user_id)
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


# ───────────────────────────── compliance ───────────────────────────


#: A clause's state in the rollup — the result its check produced in the suite's latest run, or why
#: there is none. `not_enforced` clauses are the contract's `unmapped` list, reported beside these.
CLAUSE_STATES = (
    "pass",
    "warn",
    "fail",
    "critical",
    "unverified",
    "not_run",
    "disabled",
    "missing",
)


@dataclass
class ClauseState:
    suite_id: uuid.UUID
    #: None for a `missing` clause: the contract derives a check for it and the suite has none
    #: (deleted since, or the suite was linked by hand and never imported).
    check_id: uuid.UUID | None
    check_name: str | None
    clause: dict[str, Any]
    state: str
    #: The check was edited since its import, so it may no longer say what the clause says;
    #: None when that cannot be told (no check, or no fingerprint was recorded).
    drifted: bool | None


@dataclass
class SuiteCompliance:
    suite_id: uuid.UUID
    last_run_id: uuid.UUID | None
    last_run_status: str | None
    last_run_at: Any
    #: True only for a run that completed (`succeeded`): a queued, running, failed or cancelled
    #: run's results are not a complete account, and the states below are not a verdict.
    results_final: bool


@dataclass
class Compliance:
    clauses: list[ClauseState]
    suites: list[SuiteCompliance]
    counts: dict[str, int]
    not_enforced: int


def _clause_state(check: Check, by_check: dict[uuid.UUID, str]) -> str:
    if not check.enabled:
        return "disabled"
    if check.id not in by_check:
        return "not_run"
    status = by_check[check.id]
    return status if status in RESULT_SEVERITY_TIERS else "unverified"


def _expected_clauses(session: Session, contract: Contract, suite: Suite) -> list[dict[str, Any]]:
    """The clauses the contract derives a check for on this suite's object and datasource — the
    translation re-run, so a clause whose check is gone still shows up (as `missing`)."""
    connection = session.get(Connection, suite.connection_id)
    if connection is None or suite.contract_object is None:
        return []
    translated = tr.translate(contract.spec, connection_type=connection.type)
    obj = next((o for o in translated.objects if o.name == suite.contract_object), None)
    if obj is None:
        return []
    # A clause the import recorded as not enforced (e.g. the validators refused its check) is
    # already counted there; it is not also `missing`.
    not_enforced = {_clause_key(u) for u in contract.unmapped or []}
    return [
        ref for draft in obj.checks if _clause_key(ref := draft.ref.as_dict()) not in not_enforced
    ]


def compliance(session: Session, visible: VisibleContract) -> Compliance:
    """Per clause, what the check that enforces it found in its suite's latest run — over the
    suites the caller can see only (the restricted ones are counted on the contract, never
    read). Three queries whatever the size: latest runs, their result statuses, the checks.
    A check counts only when its clause names THIS contract (a suite relinked to another
    contract keeps its old clauses, and they are not this contract's)."""
    contract = visible.contract
    suite_ids = [s.id for s in visible.suites]
    runs = (
        {r.suite_id: r for r in session.scalars(latest_runs_per_suite_stmt(suite_ids))}
        if suite_ids
        else {}
    )
    run_ids = [r.id for r in runs.values()]
    by_check: dict[uuid.UUID, str] = {}
    if run_ids:
        rows = session.execute(
            select(Result.check_id, Result.status).where(Result.run_id.in_(run_ids))
        ).tuples()
        by_check = dict(rows.all())
    checks_by_suite: dict[uuid.UUID, list[Check]] = {sid: [] for sid in suite_ids}
    if suite_ids:
        for check in session.scalars(
            select(Check)
            .where(Check.suite_id.in_(suite_ids), Check.origin == CONTRACT_CHECK_ORIGIN)
            .order_by(*CHECK_ORDER)
        ):
            if (check.contract_ref or {}).get("odcs_id") == contract.odcs_id:
                checks_by_suite[check.suite_id].append(check)
    clauses: list[ClauseState] = []
    suites: list[SuiteCompliance] = []
    for suite in visible.suites:
        run = runs.get(suite.id)
        suites.append(
            SuiteCompliance(
                suite_id=suite.id,
                last_run_id=run.id if run else None,
                last_run_status=run.status if run else None,
                last_run_at=(run.finished_at or run.created_at) if run else None,
                results_final=run is not None and run.status in AGGREGATABLE_RUN_STATUSES,
            )
        )
        present: set[tuple[Any, ...] | None] = set()
        for check in checks_by_suite[suite.id]:
            ref = check.contract_ref or {}
            present.add(_clause_key(ref))
            stored = ref.get("fingerprint")
            clauses.append(
                ClauseState(
                    suite_id=suite.id,
                    check_id=check.id,
                    check_name=check.name,
                    clause={k: ref.get(k) for k in ("object", "property", "rule_id", "field")},
                    state=_clause_state(check, by_check),
                    drifted=None if stored is None else stored != _current_fingerprint(check),
                )
            )
        for ref in _expected_clauses(session, contract, suite):
            if _clause_key(ref) not in present:
                clauses.append(
                    ClauseState(
                        suite_id=suite.id,
                        check_id=None,
                        check_name=None,
                        clause=ref,
                        state="missing",
                        drifted=None,
                    )
                )
    counts = dict.fromkeys(CLAUSE_STATES, 0)
    for clause in clauses:
        counts[clause.state] += 1
    return Compliance(
        clauses=clauses,
        suites=suites,
        counts=counts,
        not_enforced=len(contract.unmapped or []),
    )


# ───────────────────────────── linking ──────────────────────────────


def link_suite(
    session: Session,
    suite: Suite,
    *,
    contract_id: uuid.UUID | None,
    contract_object: str | None,
    actor_id: uuid.UUID,
) -> None:
    """Set or clear a suite's contract link — both fields together, never one (ADR 0052 §2).
    The caller already holds `edit` on the suite (the route checked). Linking adds no checks: an
    import is what derives them. Linking into a contract is a change to that contract's set of
    suites, so it needs what replacing or deleting it needs — `edit` on every suite already
    linked — and the suite must run on the same connection and stand for an object no other
    linked suite does (otherwise the next import could not reconcile it)."""
    if (contract_id is None) != (contract_object is None):
        raise ContractSuitesInvalidError(
            "send contract_id and contract_object together, or both null to unlink",
            detail={"suite_id": str(suite.id)},
        )
    if contract_id is None:
        suite.contract_id = None
        suite.contract_object = None
        return
    visible = get_contract(session, contract_id, actor_id)
    contract = visible.contract
    objects = {o.get("name") for o in contract.spec.get("schema") or () if isinstance(o, dict)}
    if contract_object not in objects:
        raise ContractSuitesInvalidError(
            "the contract has no schema object of that name",
            detail={"contract_id": str(contract_id), "contract_object": contract_object},
        )
    others = [s for s in contract.suites if s.id != suite.id]
    _require_edit_on_all(session, contract, actor_id)
    if any(s.contract_object == contract_object for s in others):
        raise ContractSuitesInvalidError(
            "another suite already stands for that schema object of this contract",
            detail={"contract_id": str(contract_id), "contract_object": contract_object},
        )
    if any(s.connection_id != suite.connection_id for s in others):
        raise ContractSuitesInvalidError(
            "this contract's suites run on a different connection",
            detail={"contract_id": str(contract_id)},
        )
    suite.contract_id = contract_id
    suite.contract_object = contract_object
