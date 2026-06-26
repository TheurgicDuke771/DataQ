"""Resolve the configured ``ResultPublisher`` (cached after first build).

Mirrors ``core.secrets.get_secret_store``: a process-wide singleton built from
settings, with a test-only reset. v1 has only the no-op; the Teams publisher is
wired here (gated on a configured webhook) when it lands, so callers never change.
"""

from __future__ import annotations

import threading

from backend.app.alerting.base import ResultPublisher
from backend.app.alerting.noop import NoopPublisher
from backend.app.core.logging import get_logger

log = get_logger(__name__)

_publisher_singleton: ResultPublisher | None = None
_publisher_lock = threading.Lock()


def _build_publisher() -> ResultPublisher:
    """Pick the publisher to use. v1: always the no-op — the Teams branch (gated
    on a configured webhook, read from settings) lands with the Teams publisher."""
    return NoopPublisher()


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
