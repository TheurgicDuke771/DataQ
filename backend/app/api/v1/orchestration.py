"""Orchestration event webhook receivers (ADF + Airflow).

Two machine-to-machine channels (no Azure AD user), each authenticated per its
provider's constraints, then funnelled through the same provider-agnostic
ingestion (`ingest_event`): resolve provider → parse to `RunUpdate` → persist.

- `POST /orchestration/events/adf` — Azure Monitor. Auth = shared secret in the
  ``token`` query parameter, constant-time vs the Key Vault secret (ADR 0006:
  Azure Monitor webhooks can't set custom headers).
- `POST /orchestration/events/airflow` — our DAG callback snippet. Auth =
  HMAC-SHA256 over the **raw body** in the ``X-DataQ-Signature`` header,
  constant-time vs the Key Vault signing key (ADR 0007: we author the snippet,
  so it can sign a header).

Per ADR 0006/0007 each returns **200 for every well-formed, authenticated
event** — including ignored / unattributable ones — so the sender does not
retry-storm; only bad auth (401) or a malformed body (422) is an error. Adding a
provider is a sibling route + its provider class — no new persistence code.
"""

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
from backend.app.orchestration.base import AlertPing, OrchestrationProvider, RunUpdate
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
    """Provider-agnostic tail of both webhook routes: persist or poll-now.

    An `AlertPing` (#492 — a run-anonymous alert, e.g. Azure Monitor's Common
    Alert Schema) has no runId to upsert, so a *fired* ping becomes an
    immediate **targeted** poll (the poll ingests the real run identities
    within seconds); a *resolved* one is noise. Everything else is the normal
    `ingest_event` upsert + trigger path. The ack is honest: ``reconciling``
    only when the poll actually enqueued (a broker hiccup degrades to
    ``ignored`` — the 10-min beat recovers).
    """
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
    return await _ack_event(db, provider_impl=provider, update=update, secret_store=secret_store)


_SIGNATURE_HEADER = "X-DataQ-Signature"


def _authenticate_airflow(body: bytes, signature: str | None, secret_store: SecretStore) -> None:
    """Verify the HMAC-SHA256 over the raw body against the header (ADR 0007).

    The signing key resolves from the SecretStore; the expected digest is hex.
    The signature is never logged.
    """
    settings = get_settings()
    try:
        key = secret_store.get(settings.airflow_webhook_secret_name)
    except SecretNotFoundError as exc:
        log.error(
            "airflow_webhook_secret_missing", secret_name=settings.airflow_webhook_secret_name
        )
        raise WebhookNotConfiguredError("Airflow webhook receiver is not configured") from exc

    expected = hmac.new(key.encode("utf-8"), body, hashlib.sha256).hexdigest()
    # Compare on UTF-8 bytes: hmac.compare_digest raises TypeError on non-ASCII
    # str, so a caller-supplied non-ASCII signature must not reach it as str
    # (else 500 instead of 401).
    if not signature or not hmac.compare_digest(
        signature.encode("utf-8"), expected.encode("utf-8")
    ):
        log.warning("airflow_webhook_auth_failed", signature_present=bool(signature))
        raise WebhookAuthError("invalid or missing webhook signature")


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
    """Verify the HMAC-SHA256 over the raw body against the header (ADR 0029).

    Identical scheme to the Airflow callback (`_authenticate_airflow`) but keyed on
    the dbt signing secret; the signature is never logged.
    """
    settings = get_settings()
    try:
        key = secret_store.get(settings.dbt_webhook_secret_name)
    except SecretNotFoundError as exc:
        log.error("dbt_webhook_secret_missing", secret_name=settings.dbt_webhook_secret_name)
        raise WebhookNotConfiguredError("dbt webhook receiver is not configured") from exc

    expected = hmac.new(key.encode("utf-8"), body, hashlib.sha256).hexdigest()
    # Compare on UTF-8 bytes (see _authenticate_airflow): a non-ASCII signature must
    # not reach compare_digest as str, else TypeError → 500 instead of 401.
    if not signature or not hmac.compare_digest(
        signature.encode("utf-8"), expected.encode("utf-8")
    ):
        log.warning("dbt_webhook_auth_failed", signature_present=bool(signature))
        raise WebhookAuthError("invalid or missing webhook signature")


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
