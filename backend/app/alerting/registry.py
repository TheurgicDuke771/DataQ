"""Resolve the configured ``ResultPublisher`` (cached after first build).

Mirrors ``core.secrets.get_secret_store``: a process-wide singleton built from
settings, with a test-only reset.

v1 returns the Teams publisher: it reads each run's per-suite notification config
at delivery time and self-no-ops when the suite has no webhook (per-suite or
workspace), notifications are disabled, or the run is below the suite's threshold
— so it's safe even when alerting is entirely unconfigured. The ``NoopPublisher``
remains the explicit "alerting off" / test double.
"""

from __future__ import annotations

import threading

from backend.app.alerting.base import ResultPublisher
from backend.app.alerting.teams import TeamsPublisher
from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.core.secrets import get_secret_store

log = get_logger(__name__)

_publisher_singleton: ResultPublisher | None = None
_publisher_lock = threading.Lock()


def _build_publisher() -> ResultPublisher:
    """The Teams publisher, wired with the workspace webhook secret name as the
    per-suite fallback. Webhooks are resolved per run (so rotation is picked up)
    and per-suite config decides delivery; the registry only wires the store +
    fallback name, it never reads the secret."""
    return TeamsPublisher(
        secret_store=get_secret_store(),
        workspace_secret_name=get_settings().teams_webhook_secret_name,
    )


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
