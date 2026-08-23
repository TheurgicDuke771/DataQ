"""The audit seam — ADR [0041](../../../docs/site/adr/0041-history-audit-strategy.md), phase 1."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final

from sqlalchemy.orm import Session

from backend.app.core.logging import _scrub_secret_strings, get_logger, request_id_var
from backend.app.db.models import (
    AUDIT_ACTION_CLASSES,
    AUDIT_ACTOR_KINDS,
    AuditEvent,
    User,
)

log = get_logger(__name__)

# ── Redaction ───────────────────────────────────────────────────────────────── The **credential
# subset** of `_PII_KEYS`, not the whole set (ADR 0041 §2.6.4).
_CREDENTIAL_KEYS: Final[frozenset[str]] = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "auth",
        "access_key",
        "private_key",
        "passphrase",
        "catalog_secret",
        "x-vault-token",
        "client_token",
        "secret_id",
        "role_id",
        "api-key",
        "x-api-key",
        "cookie",
        "set-cookie",
    }
)
_REDACTED: Final[str] = "<redacted>"

#: Payload byte budget, measured on the serialized JSON.
MAX_PAYLOAD_BYTES: Final[int] = 16_384
#: Written into a truncated payload alongside whatever survived.
_TRUNCATION_KEY: Final[str] = "_truncated"


def _scrub_value(value: Any) -> Any:
    """Recursively scrub a value: credential-shaped KEYS are replaced wholesale,
    and every string is passed through the logger's own secret-string scrubber
    (query-param credentials, URL userinfo, bare bearer tokens).
    """
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, val in value.items():
            if str(key).strip().lower() in _CREDENTIAL_KEYS:
                out[str(key)] = _REDACTED
            else:
                out[str(key)] = _scrub_value(val)
        return out
    if isinstance(value, (list, tuple)):
        return [_scrub_value(item) for item in value]
    if isinstance(value, str):
        return _scrub_secret_strings(value)
    return value


def _jsonable(value: Any) -> Any:
    """Coerce to something the JSONB encoder accepts."""
    if isinstance(value, Decimal):
        # `float` would silently lose precision on a threshold an operator typed.
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _encoded_size(value: Any) -> int:
    """Serialized byte length — the unit the cap is expressed in, in one place so
    the budget check and the per-field measurement cannot drift apart.
    """
    return len(json.dumps(value, default=str).encode())


def _cap_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Bound the serialized payload to `MAX_PAYLOAD_BYTES`, dropping the largest
    fields first and recording WHICH ones went.
    """
    if _encoded_size(payload) <= MAX_PAYLOAD_BYTES:
        return payload
    sizes = sorted(
        ((k, _encoded_size({k: v})) for k, v in payload.items()),
        key=lambda kv: kv[1],
        reverse=True,
    )
    kept = dict(payload)
    dropped: list[str] = []

    def _marker(names: list[str]) -> dict[str, Any]:
        return {"dropped_fields": sorted(names), "limit_bytes": MAX_PAYLOAD_BYTES}

    for key, _size in sizes:
        # The marker is part of the payload, so it is part of the budget.
        if _encoded_size({**kept, _TRUNCATION_KEY: _marker(dropped)}) <= MAX_PAYLOAD_BYTES:
            break
        kept.pop(key, None)
        dropped.append(key)
    # A payload reduced to nothing but its marker is the honest outcome when a SINGLE field blows
    # the budget (an enormous custom-SQL body).
    kept[_TRUNCATION_KEY] = _marker(dropped)
    return kept


# ── Per-entity payload allow-lists ──────────────────────────────────────────── One serializer per
# entity type.

_CHECK_FIELDS: Final[tuple[str, ...]] = (
    "id",
    "suite_id",
    "name",
    "kind",
    "expectation_type",
    "dimension",
    "source_connection_id",
    "config",
    "warn_threshold",
    "fail_threshold",
    "critical_threshold",
    # Snooze is a deliberate act with a compliance-visible effect — it suppresses
    # alerting on a failing check — so the field it writes is in the payload.
    "alert_snoozed_until",
)

# `config` is deliberately ABSENT.
_CONNECTION_FIELDS: Final[tuple[str, ...]] = (
    "id",
    "name",
    "type",
    "env",
    # The credential POINTER, never the credential.
    "secret_ref",
)

_SUITE_FIELDS: Final[tuple[str, ...]] = (
    "id",
    "name",
    "description",
    "connection_id",
    "target",
    # The redaction override itself (#415).
    "column_policy",
    "asset_id",
)

_SCHEDULE_FIELDS: Final[tuple[str, ...]] = (
    "id",
    "suite_id",
    "cron",
    "timezone",
    "enabled",
)

_TRIGGER_BINDING_FIELDS: Final[tuple[str, ...]] = (
    "id",
    "provider",
    "pipeline_or_dag_id",
    "env",
    "suite_id",
    "enabled",
)

_SUITE_NOTIFICATION_FIELDS: Final[tuple[str, ...]] = (
    "id",
    "suite_id",
    "alert_on",
    "enabled",
    "auto_resolve_incidents",
    "email_recipients",
    # Pointers again, never values — see `_CONNECTION_FIELDS`.
    "webhook_secret_ref",
    "slack_webhook_secret_ref",
)

_SHARE_FIELDS: Final[tuple[str, ...]] = ("id", "suite_id", "user_id", "permission")

# `token_hash` is absent and must stay absent. ADR 0041 §2.5: an api_key event
# records the mint/revoke, **never the token or its hash**.
_API_KEY_FIELDS: Final[tuple[str, ...]] = ("id", "user_id", "name", "revoked_at", "expires_at")

_USER_FIELDS: Final[tuple[str, ...]] = ("id", "email", "display_name", "role")

# Metadata mutation only (ADR 0041 §2.5).
_ASSET_FIELDS: Final[tuple[str, ...]] = (
    "id",
    "namespace",
    "name",
    "env",
    "connection_id",
    "owner_user_id",
    "description",
)

# `evidence` is deliberately ABSENT: it is derived from a failing run and can
# carry warehouse values — the standing "no warehouse data in a payload" rule.
_INCIDENT_FIELDS: Final[tuple[str, ...]] = (
    "id",
    "check_id",
    "suite_id",
    "asset_id",
    "status",
    "acknowledged_by",
    "acknowledge_note",
    "resolved_by_user_id",
    "resolution_note",
)

_SERIALIZERS: Final[dict[str, tuple[str, ...]]] = {
    "check": _CHECK_FIELDS,
    "connection": _CONNECTION_FIELDS,
    "suite": _SUITE_FIELDS,
    "schedule": _SCHEDULE_FIELDS,
    "trigger_binding": _TRIGGER_BINDING_FIELDS,
    "suite_notification": _SUITE_NOTIFICATION_FIELDS,
    "share": _SHARE_FIELDS,
    "api_key": _API_KEY_FIELDS,
    "user": _USER_FIELDS,
    "asset": _ASSET_FIELDS,
    "incident": _INCIDENT_FIELDS,
}


class UnknownAuditEntityError(RuntimeError):
    """A caller asked to audit an entity type with no declared allow-list."""


def snapshot(entity_type: str, entity: Any) -> dict[str, Any] | None:
    """Build an allow-listed, scrubbed, JSON-safe payload for `entity`."""
    fields = _SERIALIZERS.get(entity_type)
    if fields is None:
        raise UnknownAuditEntityError(
            f"no audit allow-list declared for entity_type={entity_type!r} — "
            "add one to audit_service._SERIALIZERS rather than auditing an "
            "undeclared shape"
        )
    if entity is None:
        return None
    raw = {name: getattr(entity, name, None) for name in fields}
    return _cap_payload(_scrub_value(_jsonable(raw)))


def _resolve_actor(session: Session, actor: Any | None) -> Any | None:
    """Normalize the actor argument to a `User` row (or `None`)."""
    if actor is None:
        return None
    if isinstance(actor, uuid.UUID):
        return session.get(User, actor)
    return actor


def _sanitize_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Scrub, JSON-coerce and cap a payload, whoever built it. See `record`."""
    if payload is None:
        return None
    return _cap_payload(_scrub_value(_jsonable(payload)))


def record(
    session: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    actor: Any | None,
    action_class: str = "config",
    actor_kind: str = "user",
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> AuditEvent:
    """Add one audit row to the caller's transaction."""
    # Both discriminators are validated here, not just one.
    if action_class not in AUDIT_ACTION_CLASSES:
        raise ValueError(
            f"action_class={action_class!r} is not one of {AUDIT_ACTION_CLASSES} "
            "('access' is phase 2 / G1 #431)"
        )
    if actor_kind not in AUDIT_ACTOR_KINDS:
        raise ValueError(
            f"actor_kind={actor_kind!r} is not one of {AUDIT_ACTOR_KINDS} — "
            "there is deliberately no 'system' kind (ADR 0041 §2.1)"
        )
    actor_row = _resolve_actor(session, actor)
    label: str | None = None
    actor_id: uuid.UUID | None = None
    if actor_row is not None:
        actor_id = getattr(actor_row, "id", None)
        label = getattr(actor_row, "display_name", None) or getattr(actor_row, "email", None)
    elif isinstance(actor, uuid.UUID):
        # The id was given but the row could not be loaded (a user deleted between the request and
        # this write).
        label = f"<unresolved user {actor}>"
    # The module's three guarantees — allow-list, redaction, cap — must hold on EVERY payload, not
    # only the ones `snapshot` built.
    event = AuditEvent(
        action_class=action_class,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_user_id=actor_id,
        actor_kind=actor_kind,
        actor_label=label,
        before=_sanitize_payload(before),
        after=_sanitize_payload(after),
        request_id=request_id if request_id is not None else request_id_var.get(),
    )
    session.add(event)
    return event


def record_entity_change(
    session: Session,
    *,
    action: str,
    entity_type: str,
    entity: Any,
    actor: Any | None,
    before: Mapping[str, Any] | None = None,
    actor_kind: str = "user",
    if_changed: bool = False,
) -> AuditEvent | None:
    """`record`, with the `after` payload built from `entity` via the allow-list."""
    if entity is not None and getattr(entity, "id", None) is None and entity in session:
        session.flush([entity])
    entity_id = getattr(entity, "id", None) if entity is not None else None
    if entity_id is None and before is not None:
        raw_id = before.get("id")
        entity_id = uuid.UUID(str(raw_id)) if raw_id is not None else None
    after = snapshot(entity_type, entity)
    before_payload = dict(before) if before is not None else None
    if if_changed and before_payload is not None and before_payload == after:
        return None
    return record(
        session,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        actor_kind=actor_kind,
        before=before_payload,
        after=after,
    )


# ── Phase 2: data-access events (G1 / #431) ─────────────────────────────────── The opposite
# latency and failure contract from phase 1, deliberately (ADR 0041 §2.1).


def record_access(
    session: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    actor: Any | None,
    exposed: bool,
    detail: dict[str, Any] | None = None,
    actor_kind: str = "user",
) -> AuditEvent | None:
    """Record a read of regulated data."""
    payload: dict[str, Any] = {"exposed": exposed}
    if detail:
        payload.update(detail)
    previously_expiring = session.expire_on_commit
    try:
        with session.begin_nested():
            event = record(
                session,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                actor=actor,
                action_class="access",
                actor_kind=actor_kind,
                after=payload,
            )
        # `expire_on_commit` is True by default, so this commit would expire every ORM object the
        # CALLER is still holding — and a read path commits in the middle of building its response.
        session.expire_on_commit = False
        session.commit()
        return event
    except Exception:
        # Roll back before logging: after a database-level failure the session is in a failed
        # transaction.
        session.rollback()
        log.error("audit_access_write_failed", action=action, exc_info=True)
        return None
    finally:
        session.expire_on_commit = previously_expiring


def declared_entity_types() -> Sequence[str]:
    """The entity types with a declared allow-list — the drift-guard test's input."""
    return sorted(_SERIALIZERS)
