"""Trigger-binding CRUD — provider-agnostic management of the suite-run triggers.

A `trigger_binding` maps a successful orchestrator run to a suite execution:
composite key (`provider`, `pipeline_or_dag_id`, `env`) → `suite_id` (ADR 0004).
The webhook + polling paths (`orchestration_service`) *consume* enabled bindings;
this module lets users *manage* them.

Provider-agnostic by design (CLAUDE.md §10): `provider` is validated against the
shared `ORCHESTRATION_PROVIDERS` set — there is no ADF-specific table or branch.
Because a binding automates a suite, management is gated on the caller's suite
permission (`suite_authz.require_permission`): `edit` to create / change / delete,
`view` to read — so you can't wire a pipeline to a suite you can't access.

FastAPI-free (like the other services): takes a `Session`, returns ORM models,
raises typed `DataQError`s the envelope maps to status codes.
"""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import ENVS, ORCHESTRATION_PROVIDERS, Share, Suite, TriggerBinding
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


def create_binding(
    session: Session,
    *,
    provider: str,
    pipeline_or_dag_id: str,
    env: str,
    suite_id: uuid.UUID,
    user_id: uuid.UUID,
    enabled: bool = True,
) -> TriggerBinding:
    """Create a binding. Requires `edit` on the target suite (404/403 otherwise).

    The composite key (`provider`, `pipeline_or_dag_id`, `env`, `suite_id`) is
    unique — a duplicate is a 409.
    """
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
    return binding


def list_bindings(
    session: Session,
    *,
    user_id: uuid.UUID,
    provider: str | None = None,
    env: str | None = None,
    suite_id: uuid.UUID | None = None,
) -> list[TriggerBinding]:
    """Bindings on suites the user can access (owned or shared), newest first."""
    accessible = select(Suite.id).where(
        or_(
            Suite.created_by == user_id,
            Suite.id.in_(select(Share.suite_id).where(Share.user_id == user_id)),
        )
    )
    stmt = (
        select(TriggerBinding)
        .where(TriggerBinding.suite_id.in_(accessible))
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
) -> TriggerBinding:
    """Toggle a binding's `enabled` flag. Identity fields are immutable — to
    re-target a binding, delete it and create a new one. Requires `edit`."""
    binding = _get_owned(session, binding_id, user_id, minimum="edit")
    binding.enabled = enabled
    session.commit()
    session.refresh(binding)
    log.info("trigger_binding_updated", binding_id=str(binding.id), enabled=enabled)
    return binding


def delete_binding(session: Session, binding_id: uuid.UUID, *, user_id: uuid.UUID) -> None:
    """Delete a binding. Requires `edit` on its suite."""
    binding = _get_owned(session, binding_id, user_id, minimum="edit")
    session.delete(binding)
    session.commit()
    log.info("trigger_binding_deleted", binding_id=str(binding_id))
