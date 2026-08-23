"""Trigger-binding CRUD — provider-agnostic management of the suite-run triggers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import (
    ENVS,
    ORCHESTRATION_PROVIDERS,
    Connection,
    TriggerBinding,
)
from backend.app.orchestration.registry import get_orchestration_provider
from backend.app.services import audit_service, suite_service
from backend.app.services.suite_authz import require_permission

log = get_logger(__name__)


class TriggerBindingNotFoundError(DataQError):
    status_code = 404
    code = "trigger_binding_not_found"


class TriggerBindingInvalidError(DataQError):
    status_code = 422
    code = "trigger_binding_invalid"


class TriggerBindingConflictError(DataQError):
    status_code = 409
    code = "trigger_binding_conflict"


def _validate_provider_env(provider: str, env: str) -> None:
    if provider not in ORCHESTRATION_PROVIDERS:
        raise TriggerBindingInvalidError(
            f"invalid provider {provider!r}",
            detail={"allowed": list(ORCHESTRATION_PROVIDERS)},
        )
    if env not in ENVS:
        raise TriggerBindingInvalidError(f"invalid env {env!r}", detail={"allowed": list(ENVS)})


@dataclass(frozen=True)
class TriggerBindingWarning:
    """A non-blocking, advisory signal returned alongside a create/update response (#1186 design
    option 1 — option 3, deterministic per-connection attribution, was rejected as too invasive
    for v1.1).
    """

    code: str
    message: str
    other_envs: list[str]


@dataclass(frozen=True)
class BindingResult:
    """A binding plus the advisory warnings computed for this create/update."""

    binding: TriggerBinding
    warnings: list[TriggerBindingWarning] = field(default_factory=list)


def _ambiguous_orchestration_warnings(
    session: Session, *, provider: str, env: str
) -> list[TriggerBindingWarning]:
    """Advisory warnings for a binding that will fire against `(provider, env)`."""
    connection = session.scalar(
        select(Connection).where(Connection.type == provider, Connection.env == env)
    )
    if connection is None:
        return []
    resource_key = get_orchestration_provider(provider).resource_config_key
    resource_value = connection.config.get(resource_key)
    if not resource_value:
        return []
    other_envs = sorted(
        set(
            session.scalars(
                select(Connection.env).where(
                    Connection.type == provider,
                    Connection.env != env,
                    Connection.config[resource_key].astext == str(resource_value),
                )
            )
        )
    )
    if not other_envs:
        return []
    return [
        TriggerBindingWarning(
            code="ambiguous_orchestration_url",
            message=(
                f"This {provider} connection's resource ({resource_key}={resource_value!r}) is "
                f"also configured on the {', '.join(other_envs)} connection(s). A pipeline/DAG "
                "run reported against one of those will not match this binding's env — verify "
                "this is the env you intend, or the trigger may silently never fire."
            ),
            other_envs=other_envs,
        )
    ]


def create_binding(
    session: Session,
    *,
    provider: str,
    pipeline_or_dag_id: str,
    env: str,
    suite_id: uuid.UUID,
    user_id: uuid.UUID,
    enabled: bool = True,
) -> BindingResult:
    """Create a binding. Requires `edit` on the target suite (404/403 otherwise)."""
    _validate_provider_env(provider, env)
    # Proves the suite exists (404) and the caller may automate it (403).
    require_permission(session, suite_id, user_id, minimum="edit")

    binding = TriggerBinding(
        provider=provider,
        pipeline_or_dag_id=pipeline_or_dag_id,
        env=env,
        suite_id=suite_id,
        enabled=enabled,
    )
    session.add(binding)
    try:
        # Inside the try: `record_entity_change` flushes to obtain the server-assigned id, which is
        # where the duplicate-binding unique constraint now fires.
        audit_service.record_entity_change(
            session,
            action="trigger_binding.create",
            entity_type="trigger_binding",
            entity=binding,
            actor=user_id,
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise TriggerBindingConflictError(
            "a binding for this (provider, pipeline, env, suite) already exists",
            detail={"provider": provider, "pipeline_or_dag_id": pipeline_or_dag_id, "env": env},
        ) from exc
    session.refresh(binding)
    log.info(
        "trigger_binding_created",
        binding_id=str(binding.id),
        provider=provider,
        pipeline_or_dag_id=pipeline_or_dag_id,
        env=env,
        suite_id=str(suite_id),
    )
    warnings = (
        _ambiguous_orchestration_warnings(session, provider=provider, env=env) if enabled else []
    )
    return BindingResult(binding=binding, warnings=warnings)


def list_bindings(
    session: Session,
    *,
    user_id: uuid.UUID,
    provider: str | None = None,
    env: str | None = None,
    suite_id: uuid.UUID | None = None,
    include_all: bool = False,
) -> list[TriggerBinding]:
    """Bindings on suites the user can access (owned or shared), newest first — or
    on *every* suite when ``include_all`` (the workspace-admin view, ADR 0027).
    """
    stmt = (
        select(TriggerBinding)
        .where(
            TriggerBinding.suite_id.in_(
                suite_service.accessible_suite_ids(user_id, include_all=include_all)
            )
        )
        .order_by(TriggerBinding.created_at.desc())
    )
    if provider is not None:
        stmt = stmt.where(TriggerBinding.provider == provider)
    if env is not None:
        stmt = stmt.where(TriggerBinding.env == env)
    if suite_id is not None:
        stmt = stmt.where(TriggerBinding.suite_id == suite_id)
    return list(session.scalars(stmt))


def _get_owned(
    session: Session, binding_id: uuid.UUID, user_id: uuid.UUID, *, minimum: str
) -> TriggerBinding:
    """Load a binding and assert the caller's permission on its suite."""
    binding = session.get(TriggerBinding, binding_id)
    if binding is None:
        raise TriggerBindingNotFoundError(
            "trigger binding not found", detail={"binding_id": str(binding_id)}
        )
    require_permission(session, binding.suite_id, user_id, minimum=minimum)
    return binding


def get_binding(session: Session, binding_id: uuid.UUID, *, user_id: uuid.UUID) -> TriggerBinding:
    return _get_owned(session, binding_id, user_id, minimum="view")


def update_binding(
    session: Session, binding_id: uuid.UUID, *, user_id: uuid.UUID, enabled: bool
) -> BindingResult:
    """Toggle a binding's `enabled` flag. Identity fields are immutable — to
    re-target a binding, delete it and create a new one. Requires `edit`.
    """
    binding = _get_owned(session, binding_id, user_id, minimum="edit")
    audit_before = audit_service.snapshot("trigger_binding", binding)
    binding.enabled = enabled
    audit_service.record_entity_change(
        session,
        action="trigger_binding.update",
        entity_type="trigger_binding",
        entity=binding,
        actor=user_id,
        before=audit_before,
        if_changed=True,
    )
    session.commit()
    session.refresh(binding)
    log.info("trigger_binding_updated", binding_id=str(binding.id), enabled=enabled)
    warnings = (
        _ambiguous_orchestration_warnings(session, provider=binding.provider, env=binding.env)
        if enabled
        else []
    )
    return BindingResult(binding=binding, warnings=warnings)


def delete_binding(session: Session, binding_id: uuid.UUID, *, user_id: uuid.UUID) -> None:
    """Delete a binding. Requires `edit` on its suite."""
    binding = _get_owned(session, binding_id, user_id, minimum="edit")
    audit_before = audit_service.snapshot("trigger_binding", binding)
    session.delete(binding)
    audit_service.record_entity_change(
        session,
        action="trigger_binding.delete",
        entity_type="trigger_binding",
        entity=None,
        actor=user_id,
        before=audit_before,
    )
    session.commit()
    log.info("trigger_binding_deleted", binding_id=str(binding_id))
