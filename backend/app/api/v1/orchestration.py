"""Orchestration event webhook receivers (ADF now; Airflow next).

`POST /api/v1/orchestration/events/adf` is the Azure Monitor → DataQ channel.
It is a machine-to-machine endpoint (no Azure AD user), authenticated by a
shared secret carried as the ``token`` query parameter and compared
constant-time against the Key Vault secret (ADR 0006). Per ADR 0006 the endpoint
returns **200 for every well-formed, authenticated event** — including ignored /
unattributable ones — so Azure Monitor does not enter a retry storm; only a bad
token (401) or a malformed body (422) is an error.

The receiver itself is provider-agnostic: it resolves the `OrchestrationProvider`
from the path, parses the payload to a `RunUpdate`, and persists via
`orchestration_service.record_pipeline_event`. Adding Airflow is a sibling route
plus its provider — no new persistence code.
"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.app.core.config import get_settings
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretNotFoundError, SecretStore, get_secret_store
from backend.app.db.session import get_db
from backend.app.orchestration.registry import get_orchestration_provider
from backend.app.services.orchestration_service import ingest_event

log = get_logger(__name__)

router = APIRouter(tags=["orchestration"])


class WebhookAuthError(DataQError):
    status_code = 401
    code = "webhook_unauthorized"


class WebhookNotConfiguredError(DataQError):
    status_code = 503
    code = "webhook_not_configured"


class EventAck(BaseModel):
    status: str  # "recorded" | "ignored"
    triggered: int = 0  # suite runs triggered (succeeded run matching a binding)


def _authenticate(token: str | None, secret_store: SecretStore) -> None:
    """Constant-time shared-secret check (ADR 0006). The token is never logged."""
    settings = get_settings()
    try:
        secret = secret_store.get(settings.adf_webhook_secret_name)
    except SecretNotFoundError as exc:
        # Receiver secret not provisioned — operator error, not a caller error.
        log.error("adf_webhook_secret_missing", secret_name=settings.adf_webhook_secret_name)
        raise WebhookNotConfiguredError("ADF webhook receiver is not configured") from exc

    # Compare on UTF-8 bytes: hmac.compare_digest rejects non-ASCII str inputs
    # with a TypeError, so a caller-supplied non-ASCII token must not reach it.
    if not token or not hmac.compare_digest(token.encode("utf-8"), secret.encode("utf-8")):
        log.warning("adf_webhook_auth_failed", token_present=bool(token))
        raise WebhookAuthError("invalid or missing webhook token")


@router.post(
    "/orchestration/events/adf",
    response_model=EventAck,
    status_code=status.HTTP_200_OK,
    summary="Receive an Azure Data Factory pipeline-run event",
)
async def receive_adf_event(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
    token: Annotated[str | None, Query(description="Shared secret (ADR 0006)")] = None,
) -> EventAck:
    _authenticate(token, secret_store)

    provider = get_orchestration_provider("adf")
    body = await request.body()
    update = provider.parse_event(body, request.headers)  # raises MalformedEventError → 422

    result = await run_in_threadpool(
        ingest_event, db, provider_impl=provider, update=update, secret_store=secret_store
    )
    return EventAck(
        status="recorded" if result.pipeline_run is not None else "ignored",
        triggered=len(result.triggered_runs),
    )
