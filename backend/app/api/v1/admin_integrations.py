"""Admin Integrations endpoints (#1701) — webhook secret rotation, poll-now, and the
warehouse inventory-sync surface.

A separate module from `admin.py` so the orchestration/integration write axis has its
own file; it mounts under the same `/admin` prefix and the same workspace-admin gate.
Every provider is reached through the `OrchestrationProvider` registry — nothing here
branches on ADF, Airflow or dbt by name.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel, ApiRequestModel
from backend.app.core.auth import require_workspace_admin
from backend.app.core.config import get_settings
from backend.app.core.secrets import SecretStore, SecretWriteError, get_secret_store
from backend.app.db.models import ORCHESTRATION_PROVIDERS, Connection, User
from backend.app.db.session import get_db
from backend.app.orchestration.registry import get_orchestration_provider
from backend.app.services import (
    asset_view_service,
    audit_service,
    connection_service,
    inventory_service,
    orchestration_service,
    webhook_secret_service,
)
from backend.app.services.failure_classifier import classify_broker_reason

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_workspace_admin)],
)


# ──────────────────── webhook secret regeneration ────────────────────


class WebhookRegenerateResponse(ApiModel):
    """A regenerated inbound-webhook credential.

    `value` is returned HERE AND NOWHERE ELSE — DataQ stores it in the secret store
    and no read endpoint returns it again, so an operator who does not copy it now
    must regenerate again. `grace_until` is the moment the PREVIOUS value stops being
    accepted; it is `null` when nothing was carried over, which means the old value
    is already dead (there was none, or the grace window is configured to zero).
    """

    provider: str
    secret_name: str
    #: `url_token` — the value goes in the webhook URL; `hmac` — it signs the body.
    auth_mode: Literal["url_token", "hmac"]
    value: str
    grace_until: datetime | None
    #: Ready to paste, for providers whose credential rides in the URL. `null` for HMAC.
    inbound_url: str | None


@router.post(
    "/orchestration/webhooks/{provider}/regenerate",
    response_model=WebhookRegenerateResponse,
    summary="Regenerate a provider's inbound-webhook secret/key (admin)",
)
def regenerate_webhook_secret(
    provider: str,
    request: Request,
    current_user: Annotated[User, Depends(require_workspace_admin)],
    db: Annotated[Session, Depends(get_db)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> WebhookRegenerateResponse:
    """Mint a new secret for one orchestration provider's inbound webhook.

    The previous value keeps working until `grace_until` (`WEBHOOK_SECRET_GRACE_MINUTES`,
    15 by default), so updating the provider side is not a race — but DataQ cannot tell
    whether that update happened, so nothing here confirms the provider is still able to
    deliver. Once the window closes, callbacks signed with the old value are rejected.

    The audit row records the provider and the grace window, never the value.
    """
    if provider not in ORCHESTRATION_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown orchestration provider {provider!r}",
        )
    descriptor = get_orchestration_provider(provider).webhook_auth
    settings = get_settings()
    secret_name = descriptor.secret_name(settings)
    now = datetime.now(UTC)
    try:
        result = webhook_secret_service.regenerate(secret_store, secret_name, now=now)
    except SecretWriteError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="secret store rejected the write; the webhook secret is unchanged",
        ) from exc

    inbound_url: str | None = None
    if descriptor.mode == "url_token":
        from urllib.parse import quote

        base = (settings.public_base_url or str(request.base_url)).rstrip("/")
        inbound_url = (
            f"{base}/api/v1/orchestration/events/{provider}?token={quote(result.value, safe='')}"
        )

    audit_service.record(
        db,
        action="orchestration_webhook.regenerate",
        entity_type="orchestration_webhook",
        entity_id=None,
        actor=current_user,
        after={
            "provider": provider,
            "secret_name": secret_name,
            "grace_until": result.grace_until.isoformat() if result.grace_until else None,
        },
    )
    db.commit()
    return WebhookRegenerateResponse(
        provider=provider,
        secret_name=secret_name,
        auth_mode=descriptor.mode,
        value=result.value,
        grace_until=result.grace_until,
        inbound_url=inbound_url,
    )


# ──────────────────── poll now ────────────────────


class PollDispatchRead(ApiModel):
    """One enqueued poll. `scope="provider"` means the sweep covers EVERY connection
    of that provider, not only `connection_id` — which is what happens when the named
    connection has no resource identifier configured to narrow on.
    """

    provider: str
    connection_id: UUID | None
    scope: Literal["provider", "connection"]
    task_id: str


class PollNowResponse(ApiModel):
    """Poll-now dispatch (#1701). `dispatched` lists what actually reached the broker;
    an empty list with no error means there were no orchestration connections to poll.

    Enqueued is not polled: the worker runs these afterwards, so `GET /admin/health`
    still shows the previous poll timestamps until it does.
    """

    dispatched: list[PollDispatchRead]
    requested_at: datetime


@router.post(
    "/orchestration/poll-now",
    response_model=PollNowResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run the orchestration poll now, for one connection or all (admin)",
)
def poll_now(
    current_user: Annotated[User, Depends(require_workspace_admin)],
    db: Annotated[Session, Depends(get_db)],
    connection_id: Annotated[
        UUID | None, Query(description="Narrow to one orchestration connection")
    ] = None,
) -> PollNowResponse:
    """Enqueue the same 10-minute fallback poll the beat schedule runs (#492's
    immediate-poll path), for every orchestration connection or just one.

    A poll only ingests runs the provider reports; it does not re-run anything, and a
    dispatch failure at the broker is a 503 with nothing enqueued and no audit row.
    """
    now = datetime.now(UTC)
    targets: list[tuple[str, UUID | None, str | None]] = []
    if connection_id is not None:
        connection = db.get(Connection, connection_id)
        if connection is None or connection.type not in ORCHESTRATION_PROVIDERS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="orchestration connection not found",
            )
        key = get_orchestration_provider(connection.type).resource_config_key
        resource_name = (connection.config or {}).get(key)
        targets.append((connection.type, connection.id, resource_name))
    else:
        providers = db.scalars(
            select(Connection.type).where(Connection.type.in_(ORCHESTRATION_PROVIDERS)).distinct()
        ).all()
        targets = [(provider, None, None) for provider in sorted(providers)]

    dispatched: list[PollDispatchRead] = []
    for provider, target_id, resource_name in targets:
        try:
            task_id = orchestration_service.dispatch_immediate_poll(provider, resource_name)
        except Exception as exc:  # pragma: no cover - dispatch already catches its own
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=classify_broker_reason(exc),
            ) from exc
        if task_id is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="could not enqueue the poll; the task broker is unreachable",
            )
        dispatched.append(
            PollDispatchRead(
                provider=provider,
                connection_id=target_id,
                # No resource identifier to narrow on means the whole provider is swept,
                # and saying "connection" here would be a scope the poll does not honour.
                scope="connection" if resource_name else "provider",
                task_id=task_id,
            )
        )

    audit_service.record(
        db,
        action="orchestration_poll.request",
        entity_type="orchestration_poll",
        entity_id=connection_id,
        actor=current_user,
        after={"dispatched": [d.task_id for d in dispatched]},
    )
    db.commit()
    return PollNowResponse(dispatched=dispatched, requested_at=now)


# ──────────────────── inventory sync (ADR 0040) ────────────────────


class InventorySyncRead(ApiModel):
    """One warehouse connection's inventory-sync state.

    `tables_discovered` and `unmonitored` are `null` — never `0` — until the connection
    has been synced at least once: DataQ has not enumerated it, so "no tables" is not
    something it can claim. `unmonitored` counts assets from this connection that no
    suite targets, over the whole workspace (ADR 0037), not the caller's grants.

    `last_error` is the classified, secret-free reason for the last attempt and is
    present only while the connection is failing; `last_attempted_at` is an ATTEMPT, so
    a timestamp with an error beside it is a failure, not a successful sync.
    """

    connection_id: UUID
    name: str
    type: str
    env: str
    enabled: bool
    last_attempted_at: datetime | None
    failing_since: datetime | None
    last_error: str | None
    tables_discovered: int | None
    unmonitored: int | None
    status: Literal["never_synced", "synced", "failing"]


@router.get(
    "/inventory-sync",
    response_model=list[InventorySyncRead],
    summary="Warehouse inventory-sync state per connection (admin)",
)
def list_inventory_sync(db: Annotated[Session, Depends(get_db)]) -> list[InventorySyncRead]:
    """Every connection the inventory sync can enumerate (ADR 0040 — the warehouse
    types; flat-file and Iceberg connections are a recorded non-goal and are absent
    from this list rather than shown as disabled).
    """
    connections = list(
        db.scalars(
            select(Connection)
            .where(Connection.type.in_(inventory_service.INVENTORY_TYPES))
            .order_by(Connection.name)
        ).unique()
    )
    coverage = asset_view_service.coverage_by_connection(db, [c.id for c in connections])
    rows: list[InventorySyncRead] = []
    for conn in connections:
        synced = conn.inventory_sync_last_attempted_at is not None
        failing = conn.inventory_sync_last_error is not None
        counts = coverage.get(conn.id)
        rows.append(
            InventorySyncRead(
                connection_id=conn.id,
                name=conn.name,
                type=conn.type,
                env=conn.env,
                enabled=inventory_service.inventory_opted_in(conn),
                last_attempted_at=conn.inventory_sync_last_attempted_at,
                failing_since=conn.inventory_sync_failing_since,
                last_error=conn.inventory_sync_last_error,
                tables_discovered=conn.inventory_sync_last_table_count if synced else None,
                unmonitored=(counts.unmonitored if counts else 0) if synced else None,
                status="failing" if failing else ("synced" if synced else "never_synced"),
            )
        )
    return rows


class InventorySyncUpdate(ApiRequestModel):
    enabled: bool


@router.patch(
    "/inventory-sync/{connection_id}",
    response_model=InventorySyncRead,
    summary="Turn a connection's inventory sync on or off (admin)",
)
def set_inventory_sync(
    connection_id: UUID,
    payload: InventorySyncUpdate,
    current_user: Annotated[User, Depends(require_workspace_admin)],
    db: Annotated[Session, Depends(get_db)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> InventorySyncRead:
    """Flip the ADR 0040 opt-in on the connection's config, through the ordinary
    connection-update path — so the change is audited and snapshotted into
    `connection_versions` exactly like an edit made in the connection editor.

    Turning it ON schedules nothing: the sweep is a daily beat task, so use "Run now"
    to see tables before then. Turning it OFF clears the sync bookkeeping and leaves
    already-discovered assets in place.
    """
    connection = db.get(Connection, connection_id)
    if connection is None or connection.type not in inventory_service.INVENTORY_TYPES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="connection not found, or its type has no table enumerator",
        )
    config = dict(connection.config or {})
    config["inventory_sync"] = payload.enabled
    connection_service.update_connection(
        db,
        connection_id,
        config=config,
        secret_store=secret_store,
        actor_id=current_user.id,
    )
    return next(row for row in list_inventory_sync(db) if row.connection_id == connection_id)


class InventorySyncRunResponse(ApiModel):
    """The enqueued run. `status="queued"` means the worker has been asked, not that
    tables have been read — poll `GET /admin/inventory-sync` for the outcome.
    """

    status: str = "queued"
    task_id: str


@router.post(
    "/inventory-sync/{connection_id}/run",
    response_model=InventorySyncRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run one connection's inventory sync now (admin)",
)
def run_inventory_sync(
    connection_id: UUID,
    current_user: Annotated[User, Depends(require_workspace_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> InventorySyncRunResponse:
    """Enqueue the existing sync for this connection. It runs whether or not the
    connection is opted in — the opt-in gates the unattended nightly sweep, and an
    admin asking for this connection has asked for it explicitly. 503 with a classified
    broker reason if nothing could be enqueued; no audit row in that case.
    """
    connection = db.get(Connection, connection_id)
    if connection is None or connection.type not in inventory_service.INVENTORY_TYPES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="connection not found, or its type has no table enumerator",
        )
    from backend.app.worker.celery_app import celery_app

    try:
        result = celery_app.send_task(
            "sync_connection_asset_inventory", kwargs={"connection_id": str(connection_id)}
        )
        task_id = str(getattr(result, "id", "")) or ""
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=classify_broker_reason(exc),
        ) from exc
    audit_service.record(
        db,
        action="inventory_sync.run",
        entity_type="inventory_sync",
        entity_id=connection_id,
        actor=current_user,
        after={"task_id": task_id},
    )
    db.commit()
    return InventorySyncRunResponse(task_id=task_id)
