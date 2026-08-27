"""The external anchor for audit-chain tamper-evidence — ADR 0041 §9 / #1460.

Same shape as `SecretStore`/the lineage provider seam: a `Protocol`, a dark-by-
default `NoopTamperAnchor`, and one concrete BYOL-safe implementation. Publishing
here is the load-bearing half of tamper-evidence — the hash chain alone only
proves nothing was rewritten to someone who can already read the whole table;
an actor who can also write it can recompute the chain forward from wherever
they edited. Anchoring the head somewhere outside the database is what makes
that recomputation detectable.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from datetime import datetime
from typing import Protocol, runtime_checkable

import httpx

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger

log = get_logger(__name__)

_WEBHOOK_MODE = "webhook"

#: The webhook publish must not hang the beat task indefinitely.
_PUBLISH_TIMEOUT_SECONDS = 10.0


@runtime_checkable
class TamperAnchor(Protocol):
    def publish(self, *, label: str, head_hash: str, event_count: int, as_of: datetime) -> bool:
        """Publish the current chain head. Returns whether it was actually sent
        somewhere outside the database — never raises; a failed publish is a
        logged, non-fatal event (see callers: neither the retention purge nor
        the daily anchor task may block on this).
        """
        ...


class NoopTamperAnchor:
    """The default. Says so out loud, once per process, rather than silently
    doing nothing — the same "dark by default" honesty as `LINEAGE_PROVIDER`
    unset.
    """

    def __init__(self) -> None:
        self._warned = False
        self._lock = threading.Lock()

    def publish(self, *, label: str, head_hash: str, event_count: int, as_of: datetime) -> bool:
        with self._lock:
            if not self._warned:
                log.warning(
                    "tamper_anchor_unconfigured",
                    detail=(
                        "TAMPER_ANCHOR is unset — the audit hash chain is computed "
                        "and verifiable for accidental corruption, but an actor with "
                        "database write access could recompute it forward from any "
                        "point they edit. Set TAMPER_ANCHOR=webhook to anchor the "
                        "head outside the database."
                    ),
                )
                self._warned = True
        return False


class WebhookTamperAnchor:
    """POSTs the head hash, HMAC-SHA256-signed, to an operator-controlled URL —
    a separate log sink, an append-only object store's ingest endpoint, whatever
    the operator's regime requires. DataQ never reads it back; the anchor's
    entire value is that DataQ does NOT control it.
    """

    def __init__(self, url: str, secret: str, *, client: httpx.Client | None = None) -> None:
        self._url = url
        self._secret = secret.encode("utf-8")
        self._client = client or httpx.Client(timeout=_PUBLISH_TIMEOUT_SECONDS)

    def _sign(self, body: bytes) -> str:
        return hmac.new(self._secret, body, hashlib.sha256).hexdigest()

    def publish(self, *, label: str, head_hash: str, event_count: int, as_of: datetime) -> bool:
        payload = {
            "label": label,
            "head_hash": head_hash,
            "event_count": event_count,
            "as_of": as_of.isoformat(),
        }
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        try:
            response = self._client.post(
                self._url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-DataQ-Signature": f"sha256={self._sign(body)}",
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("tamper_anchor_publish_failed", url=self._url, error=str(exc))
            return False
        return True


_anchor_singleton: TamperAnchor | None = None
_anchor_lock = threading.Lock()


def _build_anchor() -> TamperAnchor:
    settings = get_settings()
    if settings.tamper_anchor == _WEBHOOK_MODE:
        return WebhookTamperAnchor(
            settings.tamper_anchor_webhook_url, settings.tamper_anchor_webhook_secret
        )
    return NoopTamperAnchor()


def get_tamper_anchor() -> TamperAnchor:
    """Return the configured anchor (cached after first call)."""
    global _anchor_singleton
    if _anchor_singleton is not None:
        return _anchor_singleton
    with _anchor_lock:
        if _anchor_singleton is None:
            _anchor_singleton = _build_anchor()
        return _anchor_singleton


def reset_tamper_anchor_cache() -> None:
    """Test-only: clear the cached anchor so the next call rebuilds it."""
    global _anchor_singleton
    with _anchor_lock:
        _anchor_singleton = None
