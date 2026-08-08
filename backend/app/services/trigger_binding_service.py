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

FastAPI-free (like the other services): takes a `Session`, raises typed
`DataQError`s the envelope maps to status codes. `create_binding`/`update_binding`
return a `BindingResult` (the `TriggerBinding` ORM row plus advisory #1186
warnings, e.g. an ambiguous cross-env orchestration URL); every other read
returns the plain ORM model.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import (
    ENVS,
    ORCHESTRATION_PROVIDERS,
    Connection,
    Share,
    Suite,
    TriggerBinding,
)
from backend.app.orchestration.registry import get_orchestration_provider
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
    """A non-blocking, advisory signal returned alongside a create/update response
    (#1186 design option 1 — option 3, deterministic per-connection attribution,
    was rejected as too invasive for v1.1). Never raised — the ambiguity a warning
    names may be entirely intentional (two genuinely distinct pipelines that
    happen to share a URL), so it informs rather than blocks.
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
    """Advisory warnings for a binding that will fire against `(provider, env)`.

    The one check today (#1186 — confirmed live, silent trigger loss): does the
    orchestrator connection this binding resolves to at ingest time
    (`orchestration_service._resolve_connection`, keyed on `(type, env)`, which is
    unique per the `uq_connections_orchestrator_type_env` partial index) share its
    resource identity — `OrchestrationProvider.resource_config_key`, e.g. Airflow
    `base_url`, ADF `factory_name`, dbt `project_name` — with a connection in a
    DIFFERENT env? If so, a pipeline/DAG run reported against the OTHER
    connection's env will never match a binding scoped to THIS env — the exact
    live incident: two Airflow connections ("dev" / "qa") shared one `base_url`,
    and bindings created against "dev" silently never fired while runs kept
    attributing to "qa".

    Provider-agnostic via the `resource_config_key` seam (CLAUDE.md §4/§11) — no
    provider-specific branching. Returns `[]` when the binding's own connection
    doesn't exist yet or carries no resource value — nothing to compare.
    """
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
    """Create a binding. Requires `edit` on the target suite (404/403 otherwise).

    The composite key (`provider`, `pipeline_or_dag_id`, `env`, `suite_id`) is
    unique — a duplicate is a 409. Returns the binding plus any advisory
    ambiguous-URL warnings (#1186), computed only when the binding is enabled —
    a disabled binding won't fire regardless, so the ambiguity isn't yet actionable.
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
) -> BindingResult:
    """Toggle a binding's `enabled` flag. Identity fields are immutable — to
    re-target a binding, delete it and create a new one. Requires `edit`.

    Returns the same advisory warnings as `create_binding` (#1186), recomputed
    here because re-enabling a previously-disabled binding is exactly the moment
    the ambiguity becomes actionable again.
    """
    binding = _get_owned(session, binding_id, user_id, minimum="edit")
    binding.enabled = enabled
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
    session.delete(binding)
    session.commit()
    log.info("trigger_binding_deleted", binding_id=str(binding_id))
