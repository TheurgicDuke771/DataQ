"""LLM invocation read surface (ADR 0042) — the polling target for worker-side
LLM calls. Creation happens on the feature endpoints (SQL-gen, suggestions),
not here. Rate-limited under the `llm` class by path prefix.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import ConfigDict, Field, field_validator
from sqlalchemy import update
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel, ApiRequestModel
from backend.app.core.auth import get_current_user
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.roles import is_workspace_admin
from backend.app.db.models import Connection, LlmInvocation, User
from backend.app.db.session import get_db
from backend.app.services import (
    incident_service,
    llm_checksuggest,
    llm_prompt_context,
    llm_rca,
    llm_service,
    llm_sqlgen,
)
from backend.app.services.llm_kinds import REGISTERED_KINDS
from backend.app.services.run_dispatch import dispatch_llm_invocation
from backend.app.services.suite_authz import require_permission

log = get_logger(__name__)

router = APIRouter(prefix="/llm", tags=["llm"])


class LlmInvocationRead(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    status: str
    suite_id: UUID | None
    response: dict[str, Any] | None
    error: str | None
    input_tokens: int | None
    output_tokens: int | None
    duration_ms: int | None
    created_at: datetime
    finished_at: datetime | None


class LlmInvocationNotFoundError(DataQError):
    status_code = 404
    code = "llm_invocation_not_found"


class LlmDispatchFailedError(DataQError):
    status_code = 503
    code = "llm_dispatch_failed"


class AdditionalTableRef(ApiRequestModel):
    """A join-relevant related table on the SUITE'S OWN connection (#1649).
    There is deliberately no connection reference here: cross-connection
    joins are out of scope (a federated engine is a different ADR) and the
    `comparison` kind (ADR 0015) is the reconciliation shape for that —
    refusing it is structural, not an error path to hit.
    """

    model_config = ConfigDict(populate_by_name=True)

    table: str = Field(min_length=1, max_length=255)
    schema_: str | None = Field(default=None, alias="schema", max_length=255)
    catalog: str | None = Field(default=None, max_length=255)

    @field_validator("table")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("table must not be blank")
        return stripped


class SqlGenerationRequest(ApiRequestModel):
    suite_id: UUID
    #: Refused over-length, never silently truncated — a clipped rule generates
    #: SQL that quietly omits conditions the user wrote.
    description: str = Field(min_length=1, max_length=llm_sqlgen.MAX_DESCRIPTION_CHARS)
    include_profile: bool = False
    #: Extra tables (SAME connection) for a cross-table rule, e.g. "sales" +
    #: "traffic" — bounded, see `llm_sqlgen.MAX_ADDITIONAL_TABLES`.
    additional_tables: list[AdditionalTableRef] = Field(
        default_factory=list, max_length=llm_sqlgen.MAX_ADDITIONAL_TABLES
    )

    @field_validator("description")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("description must not be blank")
        return value


class LlmInvocationQueued(ApiModel):
    invocation_id: UUID
    status: str = "pending"


class CheckSuggestionRequest(ApiRequestModel):
    suite_id: UUID


class RcaNarrativeRequest(ApiRequestModel):
    incident_id: UUID


def _queue_invocation(db: Session, invocation: LlmInvocation) -> LlmInvocationQueued:
    db.commit()
    try:
        dispatch_llm_invocation(invocation.id)
    except Exception as exc:
        log.exception("llm_dispatch_failed", invocation_id=str(invocation.id))
        # Conditional, like the worker's claim: send_task can raise after the
        # message was effectively published, and a claimed row must not be
        # clobbered back to failed.
        db.rollback()
        db.execute(
            update(LlmInvocation)
            .where(LlmInvocation.id == invocation.id, LlmInvocation.status == "pending")
            .values(
                status="failed",
                error="the task broker was unreachable — the generation was not started",
                finished_at=datetime.now(UTC),
            )
        )
        db.commit()
        raise LlmDispatchFailedError("could not dispatch the generation to the worker") from exc
    return LlmInvocationQueued(invocation_id=invocation.id)


@router.post(
    "/sql_generation",
    response_model=LlmInvocationQueued,
    status_code=202,
    summary="Generate a custom-SQL check from a natural-language rule (suite edit)",
)
def generate_sql(
    payload: SqlGenerationRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> LlmInvocationQueued:
    """Queues a worker-side generation; poll `GET /llm/invocations/{id}`. The
    model's SQL passes the same ADR 0019 validator a human's would before it is
    ever stored; add the result to a check like any custom SQL (dry-run in the
    editor before save applies there unchanged). `additional_tables` (#1649)
    joins in up to `llm_sqlgen.MAX_ADDITIONAL_TABLES` more tables on this SAME
    connection for a cross-table rule — cross-connection is refused
    structurally (there is no connection reference to give one).
    """
    if llm_sqlgen.SQLGEN_KIND not in REGISTERED_KINDS:  # pragma: no cover - wiring guard
        raise LlmDispatchFailedError("sql_generation is not registered in this process")
    suite = require_permission(db, payload.suite_id, current_user.id, minimum="edit")
    connection = db.get(Connection, suite.connection_id)
    llm_sqlgen.check_generation_preconditions(suite, connection)
    primary = llm_prompt_context.target_identity(suite)
    additional_tables = llm_sqlgen.validate_additional_tables(
        [
            {"table": t.table, "schema": t.schema_, "catalog": t.catalog}
            for t in payload.additional_tables
        ],
        primary_table=primary.get("table"),
        primary_schema=primary.get("schema"),
        primary_catalog=primary.get("catalog"),
    )
    invocation = llm_service.create_invocation(
        db,
        kind=llm_sqlgen.SQLGEN_KIND,
        requested_by=current_user,
        suite_id=suite.id,
        request={
            "description": payload.description,
            "include_profile": payload.include_profile,
            "additional_tables": additional_tables,
        },
    )
    return _queue_invocation(db, invocation)


@router.post(
    "/check_suggestions",
    response_model=LlmInvocationQueued,
    status_code=202,
    summary="Suggest checks for a suite from its column profile (suite edit)",
)
def suggest_checks(
    payload: CheckSuggestionRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> LlmInvocationQueued:
    """Queues a worker-side generation; poll `GET /llm/invocations/{id}`. Every
    suggested check passes the same validator a human's `create_check` call
    would before it is ever stored — one that fails is dropped, not surfaced;
    see the invocation's `rejected` field for what didn't make it and why.

    If EVERY suggestion is rejected, the invocation fails instead — there is no
    empty-but-successful outcome for "nothing runnable came back" — and
    `rejected` never gets written (`response` stays null on a failed
    invocation). The rejection reasons are folded into `error` instead, so
    they're still readable there rather than lost.
    """
    if (
        llm_checksuggest.CHECKSUGGEST_KIND not in REGISTERED_KINDS
    ):  # pragma: no cover - wiring guard
        raise LlmDispatchFailedError("check_suggestion is not registered in this process")
    suite = require_permission(db, payload.suite_id, current_user.id, minimum="edit")
    connection = db.get(Connection, suite.connection_id)
    llm_checksuggest.check_generation_preconditions(suite, connection)
    invocation = llm_service.create_invocation(
        db,
        kind=llm_checksuggest.CHECKSUGGEST_KIND,
        requested_by=current_user,
        suite_id=suite.id,
        request={},
    )
    return _queue_invocation(db, invocation)


@router.post(
    "/rca_narrative",
    response_model=LlmInvocationQueued,
    status_code=202,
    summary="Explain a failed check — LLM root-cause narrative on an incident's evidence card",
)
def generate_rca_narrative(
    payload: RcaNarrativeRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> LlmInvocationQueued:
    """Queues a worker-side narrative over an incident's already-captured
    evidence card + a longer per-check history; poll `GET /llm/invocations/{id}`.
    Read-only — nothing is saved to the suite — so it's gated the same as
    reading the incident itself (`view`), not `edit`.
    """
    if llm_rca.RCA_KIND not in REGISTERED_KINDS:  # pragma: no cover - wiring guard
        raise LlmDispatchFailedError("rca_narrative is not registered in this process")
    incident = incident_service.load_visible_incident(
        db, payload.incident_id, user_id=current_user.id, for_action=False
    )
    llm_rca.check_generation_preconditions(incident)
    invocation = llm_service.create_invocation(
        db,
        kind=llm_rca.RCA_KIND,
        requested_by=current_user,
        suite_id=incident.suite_id,
        request={"incident_id": str(incident.id)},
    )
    return _queue_invocation(db, invocation)


@router.get(
    "/invocations/{invocation_id}",
    response_model=LlmInvocationRead,
    summary="One LLM invocation — requester or workspace admin only",
)
def get_invocation(
    invocation_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> LlmInvocationRead:
    invocation = llm_service.get_visible_invocation(
        db, invocation_id, user=current_user, is_admin=is_workspace_admin(current_user)
    )
    if invocation is None:  # 404-no-leak: another user's invocation reads as absent
        raise LlmInvocationNotFoundError("LLM invocation not found")
    return LlmInvocationRead.model_validate(invocation)
