"""Resolve the configured ``ResultPublisher`` (cached after first build)."""

from __future__ import annotations

import threading

from backend.app.alerting.base import AlertPublisher, HealthPublisher, ResultPublisher
from backend.app.alerting.composite import CompositePublisher
from backend.app.alerting.email import EmailPublisher
from backend.app.alerting.slack import SlackPublisher
from backend.app.alerting.teams import TeamsPublisher
from backend.app.alerting.webhook import WebhookPublisher
from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.core.secrets import get_secret_store

log = get_logger(__name__)

_publisher_singleton: AlertPublisher | None = None
_publisher_lock = threading.Lock()


def _split_csv(raw: str) -> tuple[str, ...]:
    """Comma-separated env string → trimmed, non-empty tuple."""
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _build_publisher() -> AlertPublisher:
    """A composite over every channel (Teams · Slack · email · generic webhook).
    Each child resolves its secret + per-suite policy per run (so rotation is
    picked up) and stays a quiet no-op until configured; the registry only
    wires the store + names, never reads a secret.
    """
    settings = get_settings()
    store = get_secret_store()
    return CompositePublisher(
        [
            TeamsPublisher(
                secret_store=store,
                workspace_secret_name=settings.teams_webhook_secret_name,
            ),
            SlackPublisher(
                secret_store=store,
                webhook_secret_name=settings.slack_webhook_secret_name,
                allowed_hosts=_split_csv(settings.slack_webhook_allowed_hosts.lower()),
            ),
            EmailPublisher(
                secret_store=store,
                smtp_host=settings.email_smtp_host,
                smtp_port=settings.email_smtp_port,
                username=settings.email_username,
                password_secret_name=settings.email_password_secret_name,
                sender=settings.email_from,
                recipients=_split_csv(settings.email_to),
            ),
            # No workspace-level config — a generic webhook only ever exists as
            # a suite-linked channel (#1514/#1662), never a single default.
            WebhookPublisher(secret_store=store),
        ]
    )


def _get_publisher() -> AlertPublisher:
    """The cached composite (built once). Both seams below are views onto this one
    object — the channels and their secrets are identical, only the DTO differs.
    """
    global _publisher_singleton
    if _publisher_singleton is not None:
        return _publisher_singleton
    with _publisher_lock:
        if _publisher_singleton is None:
            _publisher_singleton = _build_publisher()
            log.info("result_publisher_initialized", impl=type(_publisher_singleton).__name__)
        return _publisher_singleton


def get_result_publisher() -> ResultPublisher:
    """Return the configured run-outcome publisher (built once, then cached)."""
    return _get_publisher()


def get_health_publisher() -> HealthPublisher:
    """Return the configured connection-health publisher (#837) — the same cached
    composite, viewed through the health seam.
    """
    return _get_publisher()


def reset_result_publisher_cache() -> None:
    """Test-only: clear the cached publisher so the next call rebuilds it."""
    global _publisher_singleton
    with _publisher_lock:
        _publisher_singleton = None
