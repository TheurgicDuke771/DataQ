"""Orchestration event webhook receivers (ADF + Airflow + dbt)."""

from __future__ import annotations

import hashlib
import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.app.api.v1._base import ApiModel
from backend.app.core.config import get_settings
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretNotFoundError, SecretStore, get_secret_store
from backend.app.db.session import get_db
from backend.app.orchestration.base import (
    AlertPing,
    OrchestrationProvider,
    RunUpdate,
    WebhookAuthDescriptor,
)
from backend.app.orchestration.registry import get_orchestration_provider
from backend.app.services.orchestration_service import ingest_event, request_immediate_poll

log = get_logger(__name__)

router = APIRouter(tags=["orchestration"])


class WebhookAuthError(DataQError):
    status_code = 401
    code = "webhook_unauthorized"


class WebhookNotConfiguredError(DataQError):
    status_code = 503
    code = "webhook_not_configured"


class EventAck(ApiModel):
    status: str  # "recorded" | "ignored" | "reconciling" (run-anonymous alert → poll-now)
    triggered: int = 0  # suite runs triggered (succeeded run matching a binding)


async def _ack_event(
    db: Session,
    *,
    provider_impl: OrchestrationProvider,
    update: RunUpdate | AlertPing,
    secret_store: SecretStore,
) -> EventAck:
    """Provider-agnostic tail of both webhook routes: persist or poll-now."""
    if isinstance(update, AlertPing):
        log.info(
            "orchestration_alert_ping",
            provider=provider_impl.provider,
            monitor_condition=update.monitor_condition,
            resource_name=update.resource_name,
            pipeline=update.pipeline_or_dag_id,
        )
        if update.monitor_condition == "fired":
            enqueued = await run_in_threadpool(
                request_immediate_poll, provider_impl.provider, update.resource_name
            )
            return EventAck(status="reconciling" if enqueued else "ignored")
        return EventAck(status="ignored")

    result = await run_in_threadpool(
        ingest_event, db, provider_impl=provider_impl, update=update, secret_store=secret_store
    )
    return EventAck(
        status="recorded" if result.pipeline_run is not None else "ignored",
        triggered=len(result.triggered_runs),
    )


def _resolve_webhook_secret(descriptor: WebhookAuthDescriptor, secret_store: SecretStore) -> str:
    secret_name = descriptor.secret_name(get_settings())
    try:
        return secret_store.get(secret_name)
    except SecretNotFoundError as exc:
        # Receiver secret not provisioned — operator error, not a caller error.
        log.error("orchestration_webhook_secret_missing", secret_name=secret_name)
        raise WebhookNotConfiguredError("webhook receiver is not configured") from exc


def _authenticate_url_token(
    token: str | None, descriptor: WebhookAuthDescriptor, secret_store: SecretStore
) -> None:
    """Constant-time shared-secret check (ADR 0006). The token is never logged."""
    secret = _resolve_webhook_secret(descriptor, secret_store)
    # Compare on UTF-8 bytes: hmac.compare_digest rejects non-ASCII str inputs
    # with a TypeError, so a caller-supplied non-ASCII token must not reach it.
    if not token or not hmac.compare_digest(token.encode("utf-8"), secret.encode("utf-8")):
        log.warning("orchestration_webhook_auth_failed", token_present=bool(token))
        raise WebhookAuthError("invalid or missing webhook token")


def _authenticate(token: str | None, secret_store: SecretStore) -> None:
    _authenticate_url_token(token, get_orchestration_provider("adf").webhook_auth, secret_store)


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
    return await _ack_event(db, provider_impl=provider, update=update, secret_store=secret_store)


_SIGNATURE_HEADER = "X-DataQ-Signature"


def _authenticate_hmac(
    body: bytes,
    signature: str | None,
    descriptor: WebhookAuthDescriptor,
    secret_store: SecretStore,
) -> None:
    """Verify the HMAC-SHA256 over the raw body against the signature header."""
    key = _resolve_webhook_secret(descriptor, secret_store)
    expected = hmac.new(key.encode("utf-8"), body, hashlib.sha256).hexdigest()
    # Compare on UTF-8 bytes: hmac.compare_digest raises TypeError on non-ASCII str, so a caller-
    # supplied non-ASCII signature must not reach it as str (else 500 instead of 401).
    if not signature or not hmac.compare_digest(
        signature.encode("utf-8"), expected.encode("utf-8")
    ):
        log.warning("orchestration_webhook_auth_failed", signature_present=bool(signature))
        raise WebhookAuthError("invalid or missing webhook signature")


def _authenticate_airflow(body: bytes, signature: str | None, secret_store: SecretStore) -> None:
    _authenticate_hmac(
        body, signature, get_orchestration_provider("airflow").webhook_auth, secret_store
    )


@router.post(
    "/orchestration/events/airflow",
    response_model=EventAck,
    status_code=status.HTTP_200_OK,
    summary="Receive an Apache Airflow DAG-run callback event",
)
async def receive_airflow_event(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> EventAck:
    body = await request.body()
    _authenticate_airflow(body, request.headers.get(_SIGNATURE_HEADER), secret_store)

    provider = get_orchestration_provider("airflow")
    update = provider.parse_event(body, request.headers)  # raises MalformedEventError → 422
    return await _ack_event(db, provider_impl=provider, update=update, secret_store=secret_store)


def _authenticate_dbt(body: bytes, signature: str | None, secret_store: SecretStore) -> None:
    _authenticate_hmac(
        body, signature, get_orchestration_provider("dbt").webhook_auth, secret_store
    )


@router.post(
    "/orchestration/events/dbt",
    response_model=EventAck,
    status_code=status.HTTP_200_OK,
    summary="Receive a dbt build callback event",
)
async def receive_dbt_event(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> EventAck:
    body = await request.body()
    _authenticate_dbt(body, request.headers.get(_SIGNATURE_HEADER), secret_store)

    provider = get_orchestration_provider("dbt")
    update = provider.parse_event(body, request.headers)  # raises MalformedEventError → 422
    return await _ack_event(db, provider_impl=provider, update=update, secret_store=secret_store)
