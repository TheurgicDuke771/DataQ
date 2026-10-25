"""Consumer-side data contracts (ADR 0052): ODCS v3 import, reads, ODCS export, unlink-delete."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

import yaml
from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import Field, model_validator
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel, ApiRequestModel
from backend.app.contracts.export import export_contract
from backend.app.core.auth import MemberUser, get_current_user
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.services import contract_service as svc

router = APIRouter(tags=["contracts"])

#: The largest ODCS document accepted as YAML text — the suite-document cap's order of magnitude.
MAX_CONTRACT_YAML_CHARS = 1_000_000


class ContractImportRequest(ApiRequestModel):
    """An ODCS v3 document as JSON (`document`) or YAML text (`document_yaml`) — exactly one."""

    connection_id: uuid.UUID = Field(
        description="The connection the contract's suites run on (one suite per schema object)."
    )
    document: dict[str, Any] | None = None
    document_yaml: str | None = Field(default=None, max_length=MAX_CONTRACT_YAML_CHARS)
    suite_ids: list[uuid.UUID] | None = Field(
        default=None,
        max_length=200,
        description=(
            "For a NEW version: the suites linked to an earlier version of this contract that "
            "this version takes over and reconciles. Omit to create fresh suites."
        ),
    )
    replace: bool = Field(
        default=False,
        description="Replace an already-imported version (and reconcile its linked suites).",
    )

    @model_validator(mode="after")
    def _exactly_one_document(self) -> ContractImportRequest:
        if (self.document is None) == (self.document_yaml is None):
            raise ValueError("send exactly one of document or document_yaml")
        return self


class ContractSuiteRead(ApiModel):
    id: uuid.UUID
    name: str
    #: The ODCS schema object this suite enforces.
    contract_object: str | None
    #: False when the object's `physicalName` did not resolve to a run target: the suite holds
    #: its checks but cannot run until a target is set.
    runnable: bool


class UnmappedClauseRead(ApiModel):
    """A clause no DataQ check enforces, and why — shown as *not enforced*, never dropped."""

    object: str | None
    property: str | None
    rule_id: str | None
    field: str
    reason: str
    clause: dict[str, Any]


class ContractRead(ApiModel):
    id: uuid.UUID
    #: The document's own `id` — the identity across versions and renames.
    odcs_id: str
    name: str
    version: str
    status: str | None
    domain: str | None
    tenant: str | None
    #: The `apiVersion` the document declared.
    api_version: str
    created_at: datetime
    #: Linked suites the caller can view.
    suites: list[ContractSuiteRead]
    #: Linked suites the caller cannot view — counted, never named (ADR 0037).
    restricted_suite_count: int
    unmapped: list[UnmappedClauseRead]

    @classmethod
    def of(cls, visible: svc.VisibleContract) -> ContractRead:
        return cls(**cls.fields_of(visible))

    @staticmethod
    def fields_of(visible: svc.VisibleContract) -> dict[str, Any]:
        c = visible.contract
        return {
            "id": c.id,
            "odcs_id": c.odcs_id,
            "name": c.name,
            "version": c.version,
            "status": c.status,
            "domain": c.domain,
            "tenant": c.tenant,
            "api_version": c.api_version,
            "created_at": c.created_at,
            "suites": [
                ContractSuiteRead(
                    id=s.id,
                    name=s.name,
                    contract_object=s.contract_object,
                    runnable=s.target is not None,
                )
                for s in visible.suites
            ],
            "restricted_suite_count": visible.restricted_suite_count,
            "unmapped": [
                UnmappedClauseRead(
                    object=u.get("object"),
                    property=u.get("property"),
                    rule_id=u.get("rule_id"),
                    field=str(u.get("field") or ""),
                    reason=str(u.get("reason") or ""),
                    clause=u.get("clause") or {},
                )
                for u in c.unmapped or []
            ],
        }


class ClauseStateRead(ApiModel):
    suite_id: uuid.UUID
    check_id: uuid.UUID
    check_name: str
    #: `{object, property, rule_id, field}` — the clause the check enforces.
    clause: dict[str, Any]
    #: `pass` / `warn` / `fail` / `critical` from the suite's latest run; `unverified` when the
    #: check errored or was skipped; `not_run` when that run has no result for it (added since,
    #: or the run is still going or stopped early); `disabled` when the check is switched off.
    state: str
    #: The check was edited since its import, so it may no longer say what the clause says.
    drifted: bool


class SuiteComplianceRead(ApiModel):
    suite_id: uuid.UUID
    last_run_id: uuid.UUID | None
    last_run_status: str | None
    last_run_at: datetime | None
    #: True only when the latest run completed (`succeeded`); while it is queued or running, or
    #: when it failed or was cancelled, its results are partial and the states are no verdict.
    results_final: bool


class ComplianceRead(ApiModel):
    """Per clause, what its check found in its suite's latest run — over the suites the caller
    can see; `restricted_suite_count` on the contract counts the rest, which are not read."""

    clauses: list[ClauseStateRead]
    suites: list[SuiteComplianceRead]
    #: Clauses per state, over `clauses`.
    counts: dict[str, int]
    #: How many clauses DataQ does not enforce at all (the contract's `unmapped` list).
    not_enforced: int

    @classmethod
    def of(cls, c: svc.Compliance) -> ComplianceRead:
        return cls(
            clauses=[
                ClauseStateRead(
                    suite_id=x.suite_id,
                    check_id=x.check_id,
                    check_name=x.check_name,
                    clause=x.clause,
                    state=x.state,
                    drifted=x.drifted,
                )
                for x in c.clauses
            ],
            suites=[
                SuiteComplianceRead(
                    suite_id=x.suite_id,
                    last_run_id=x.last_run_id,
                    last_run_status=x.last_run_status,
                    last_run_at=x.last_run_at,
                    results_final=x.results_final,
                )
                for x in c.suites
            ],
            counts=c.counts,
            not_enforced=c.not_enforced,
        )


class ContractDetailRead(ContractRead):
    compliance: ComplianceRead


class ContractImportRead(ApiModel):
    contract: ContractRead
    created_suite_ids: list[uuid.UUID]
    reconciled_suite_ids: list[uuid.UUID]
    #: Suites named in `suite_ids` whose schema object this version dropped: unlinked, with
    #: their contract checks switched off (never deleted).
    retired_suite_ids: list[uuid.UUID]
    #: Schema objects whose `physicalName` / `name` did not resolve on this connection.
    untargeted_objects: list[str]
    checks_added: int
    checks_updated: int
    checks_disabled: int
    #: E.g. a newer ODCS v3 minor than DataQ vendors, validated against the newest it knows.
    warnings: list[str]


@router.post(
    "/contracts/import",
    response_model=ContractImportRead,
    status_code=status.HTTP_201_CREATED,
    summary="Import an ODCS v3 data contract",
    description=(
        "Validates the document against the Open Data Contract Standard (v3.0 to v3.2 vendored; a "
        "newer v3 minor is accepted with a warning; v2 and v4 are refused), stores it verbatim, "
        "and creates one suite per schema object on `connection_id` with the checks the "
        "translation table derives (ADR 0052). Every clause DataQ cannot enforce is returned in "
        "`contract.unmapped` with the reason. Member or above; `edit` on every suite it "
        "reconciles."
    ),
)
def import_contract(
    body: ContractImportRequest,
    current_user: MemberUser,
    db: Annotated[Session, Depends(get_db)],
) -> ContractImportRead:
    outcome = svc.import_contract(
        db,
        document=body.document,
        document_yaml=body.document_yaml,
        connection_id=body.connection_id,
        actor_id=current_user.id,
        suite_ids=body.suite_ids,
        replace=body.replace,
    )
    visible = svc.get_contract(db, outcome.contract.id, current_user.id)
    return ContractImportRead(
        contract=ContractRead.of(visible),
        created_suite_ids=outcome.created_suite_ids,
        reconciled_suite_ids=outcome.reconciled_suite_ids,
        retired_suite_ids=outcome.retired_suite_ids,
        untargeted_objects=outcome.untargeted_objects,
        checks_added=outcome.checks_added,
        checks_updated=outcome.checks_updated,
        checks_disabled=outcome.checks_disabled,
        warnings=outcome.warnings,
    )


@router.get(
    "/contracts",
    response_model=list[ContractRead],
    summary="List data contracts",
    description=(
        "Contracts with at least one linked suite the caller can view (a workspace admin also "
        "sees contracts with none). Each version of a contract is its own row."
    ),
)
def list_contracts(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[ContractRead]:
    return [ContractRead.of(v) for v in svc.list_contracts(db, current_user.id)]


@router.get(
    "/contracts/{contract_id}",
    response_model=ContractDetailRead,
    summary="Get a data contract with its compliance rollup",
)
def get_contract(
    contract_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ContractDetailRead:
    visible = svc.get_contract(db, contract_id, current_user.id)
    return ContractDetailRead(
        **ContractRead.fields_of(visible),
        compliance=ComplianceRead.of(svc.compliance(db, visible)),
    )


@router.get(
    "/contracts/{contract_id}/export",
    response_model=dict[str, Any],
    summary="Export a data contract as an ODCS v3 document",
    responses={
        200: {
            "content": {"application/yaml": {"schema": {"type": "string"}}},
            "description": (
                "The stored document plus `dataq.*` custom properties for the checks the "
                "caller's visible suites hold, in the newest vendored ODCS v3 vocabulary — JSON, "
                "or YAML text with `format=yaml`."
            ),
        }
    },
)
def export_contract_document(
    contract_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    export_format: Annotated[
        Literal["json", "yaml"],
        Query(alias="format", description="`yaml` returns the same document as YAML text."),
    ] = "json",
) -> dict[str, Any] | Response:
    visible = svc.get_contract(db, contract_id, current_user.id)
    document = export_contract(db, visible.contract, visible.suites)
    if export_format == "yaml":
        return Response(
            content=yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
            media_type="application/yaml",
        )
    return document


@router.delete(
    "/contracts/{contract_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a data contract (unlinks its suites; deletes no suite, check or result)",
)
def delete_contract(
    contract_id: uuid.UUID,
    current_user: MemberUser,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    svc.delete_contract(db, contract_id, current_user.id)
