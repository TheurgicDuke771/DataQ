"""Workspace-admin read endpoints — the all-suites / all-users / access overview
the Admin page consumes.

Every route is gated by `require_workspace_admin` (config allowlist), declared
once at the router so a non-admin gets a real 403. These bypass the
owned-or-shared scoping `list_suites` applies — that's the point of the page.
Read-only and additive; no new authz on the per-suite ladder.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from backend.app.core.auth import require_workspace_admin
from backend.app.core.config import get_settings
from backend.app.core.secrets import SecretStore, get_secret_store
from backend.app.db.session import get_db
from backend.app.services import admin_service as svc

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_workspace_admin)],
)


class AdminSuiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    connection_name: str
    connection_type: str
    env: str
    owner_id: UUID
    owner_email: str
    owner_name: str | None
    check_count: int
    share_count: int
    created_at: datetime
    updated_at: datetime


class AdminUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    display_name: str | None
    last_seen_at: datetime | None
    created_at: datetime
    owned_suite_count: int
    shared_suite_count: int


class AdminAccessRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    suite_id: UUID
    suite_name: str
    user_id: UUID
    user_email: str
    user_name: str | None
    permission: str


@router.get("/suites", response_model=list[AdminSuiteRead], summary="All suites (admin)")
def all_suites(db: Annotated[Session, Depends(get_db)]) -> list[svc.AdminSuiteRow]:
    return svc.list_all_suites(db)


@router.get("/users", response_model=list[AdminUserRead], summary="All users (admin)")
def all_users(db: Annotated[Session, Depends(get_db)]) -> list[svc.AdminUserRow]:
    return svc.list_all_users(db)


@router.get("/access", response_model=list[AdminAccessRead], summary="Access overview (admin)")
def all_access(db: Annotated[Session, Depends(get_db)]) -> list[svc.AdminAccessRow]:
    return svc.list_all_access(db)


class AdminWebhookRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider: str
    auth: str
    inbound_url: str
    token_configured: bool
    signing_secret_name: str | None
    connection_names: list[str]


@router.get(
    "/orchestration/webhooks",
    response_model=list[AdminWebhookRead],
    summary="Inbound orchestration webhook config (admin)",
)
def orchestration_webhooks(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> list[svc.WebhookConfigRow]:
    # The ADF row embeds the shared secret in the URL — admin-gated (router dep)
    # and never logged. Base URL: the configured public host, else the request's.
    base_url = get_settings().public_base_url or str(request.base_url)
    return svc.webhook_configs(db, base_url=base_url, secret_store=secret_store)
