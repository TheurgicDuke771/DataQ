"""Trigger-binding CRUD — provider-agnostic management of the suite-run triggers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import (
    ENVS,
    SUITE_TRIGGER_PROVIDER,
    TRIGGER_PROVIDERS,
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


def _validate_provider_env(provider: str, env: str | None) -> None:
    if provider not in TRIGGER_PROVIDERS:
        raise TriggerBindingInvalidError(
            f"invalid provider {provider!r}",
            detail={"allowed": list(TRIGGER_PROVIDERS)},
        )
    if provider == SUITE_TRIGGER_PROVIDER:
        return  # env is derived from the upstream suite, whatever was sent
    if env not in ENVS:
        raise TriggerBindingInvalidError(f"invalid env {env!r}", detail={"allowed": list(ENVS)})


def _upstream_suite_id(pipeline_or_dag_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(pipeline_or_dag_id)
    except ValueError:
        raise TriggerBindingInvalidError(
            "for provider 'suite', pipeline_or_dag_id must be the upstream suite's id",
            detail={"pipeline_or_dag_id": pipeline_or_dag_id},
        ) from None


def suite_chain_path(
    session: Session, *, downstream_suite_id: uuid.UUID, upstream_suite_id: uuid.UUID
) -> list[uuid.UUID] | None:
    """The chain of suite bindings that already leads from ``downstream`` back to ``upstream``
    (so binding upstream → downstream would close a loop), or ``None``. Disabled bindings count:
    `enabled` is a toggle, and re-enabling must not be the moment a cycle appears (ADR 0051).
    """
    parents: dict[uuid.UUID, uuid.UUID] = {}
    frontier = [downstream_suite_id]
    seen = {downstream_suite_id}
    while frontier:
        rows = session.execute(
            select(TriggerBinding.pipeline_or_dag_id, TriggerBinding.suite_id).where(
                TriggerBinding.provider == SUITE_TRIGGER_PROVIDER,
                TriggerBinding.pipeline_or_dag_id.in_([str(s) for s in frontier]),
            )
        ).all()
        frontier = []
        for upstream_text, child in rows:
            parent = uuid.UUID(upstream_text)
            if child in seen:
                continue
            seen.add(child)
            parents[child] = parent
            if child == upstream_suite_id:
                path = [child]
                while path[-1] != downstream_suite_id:
                    path.append(parents[path[-1]])
                return list(reversed(path))
            frontier.append(child)
    return None


#: One transaction-scoped advisory lock serialises every suite-binding create, so two
#: concurrent creates (A→B and B→A) cannot both pass the cycle check and commit a loop.
SUITE_BINDING_LOCK_KEY = 0x0051_5515  # ADR 0051, "suite"


def _resolve_suite_binding(
    session: Session, *, pipeline_or_dag_id: str, suite_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[str, str]:
    """Validate a ``provider='suite'`` binding; return ``(canonical upstream id, env)``."""
    upstream_id = _upstream_suite_id(pipeline_or_dag_id)
    if upstream_id == suite_id:
        raise TriggerBindingInvalidError(
            "a suite cannot trigger itself", detail={"suite_id": str(suite_id)}
        )
    # `view` on the upstream: you cannot chain onto a suite you cannot see (404 when hidden).
    upstream = require_permission(session, upstream_id, user_id, minimum="view")
    # Held until this transaction commits (the insert below is in it).
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SUITE_BINDING_LOCK_KEY})
    path = suite_chain_path(session, downstream_suite_id=suite_id, upstream_suite_id=upstream_id)
    if path is not None:
        raise TriggerBindingInvalidError(
            "this binding would make the suites trigger each other in a loop",
            detail={"cycle": [str(s) for s in [*path, suite_id]]},
        )
    connection = session.get(Connection, upstream.connection_id)
    assert connection is not None
    # Stored canonical (`str(uuid)`): firing, the cycle walk and the UI all compare to that
    # spelling, so an upper-case or braced input must not create a row that never fires.
    return str(upstream_id), str(connection.env)


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
    env: str | None,
    suite_id: uuid.UUID,
    user_id: uuid.UUID,
    enabled: bool = True,
) -> BindingResult:
    """Create a binding. Requires `edit` on the target suite (404/403 otherwise). For
    ``provider='suite'`` the env is the upstream suite's connection env, whatever was sent.
    """
    _validate_provider_env(provider, env)
    # Proves the suite exists (404) and the caller may automate it (403).
    require_permission(session, suite_id, user_id, minimum="edit")
    if provider == SUITE_TRIGGER_PROVIDER:
        pipeline_or_dag_id, env = _resolve_suite_binding(
            session, pipeline_or_dag_id=pipeline_or_dag_id, suite_id=suite_id, user_id=user_id
        )
    assert env is not None

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
        _ambiguous_orchestration_warnings(session, provider=provider, env=env)
        if enabled and provider != SUITE_TRIGGER_PROVIDER
        else []
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
        if enabled and binding.provider != SUITE_TRIGGER_PROVIDER
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
