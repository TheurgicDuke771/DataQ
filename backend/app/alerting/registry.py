"""Resolve the configured ``ResultPublisher`` (cached after first build).

Mirrors ``core.secrets.get_secret_store``: a process-wide singleton built from
settings, with a test-only reset. v1 has only the no-op; the Teams publisher is
wired here (gated on a configured webhook) when it lands, so callers never change.
"""

from __future__ import annotations

import threading

from backend.app.alerting.base import ResultPublisher, RunReport
from backend.app.alerting.noop import NoopPublisher
from backend.app.alerting.teams import TeamsPublisher
from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretNotFoundError, get_secret_store

log = get_logger(__name__)

_publisher_singleton: ResultPublisher | None = None
_publisher_lock = threading.Lock()


def _build_publisher() -> ResultPublisher:
    """Pick the publisher from config: the Teams publisher when a workspace
    webhook secret is configured, else the no-op.

    The webhook URL is resolved **per report** through the SecretStore (so a
    rotated webhook is picked up, and per-suite config can extend the resolver
    later) — the registry only wires the resolver, it doesn't read the secret."""
    secret_name = get_settings().teams_webhook_secret_name
    if not secret_name:
        return NoopPublisher()

    store = get_secret_store()

    def _resolve_webhook(_report: RunReport) -> str | None:
        try:
            return store.get(secret_name)
        except SecretNotFoundError:
            log.warning("teams_webhook_unresolved", secret_name=secret_name)
            return None

    return TeamsPublisher(_resolve_webhook)


def get_result_publisher() -> ResultPublisher:
    """Return the configured publisher (built once, then cached)."""
    global _publisher_singleton
    if _publisher_singleton is not None:
        return _publisher_singleton
    with _publisher_lock:
        if _publisher_singleton is None:
            _publisher_singleton = _build_publisher()
            log.info("result_publisher_initialized", impl=type(_publisher_singleton).__name__)
        return _publisher_singleton


def reset_result_publisher_cache() -> None:
    """Test-only: clear the cached publisher so the next call rebuilds it."""
    global _publisher_singleton
    with _publisher_lock:
        _publisher_singleton = None
