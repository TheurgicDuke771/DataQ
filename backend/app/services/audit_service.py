"""The audit seam — ADR [0041](../../../docs/site/adr/0041-history-audit-strategy.md), phase 1.

One append-only `audit_events` table records **deliberate acts by a principal**.
This module owns the three things that make such a table safe to write:

1. **The payload allow-list** (`_SERIALIZERS`) — a per-entity, hand-declared field
   set. Never `dict(row)`: a deny-list fails open the moment a column is added,
   which is exactly the #124/#952 shape where a change silently reclassified every
   check in prod. An entity with no serializer registered cannot be audited at all;
   `record` raises rather than guessing.
2. **The redaction pass** (`_scrub_payload`) — a belt-and-braces sweep over the
   already-allow-listed payload. An audit row is a JSONB write that **never passes
   through structlog**, so CLAUDE.md §10's standing *redact at the logger, not the
   call site* rule genuinely does not cover it. Saying so explicitly matters:
   assuming coverage here is the #849 shape in the one place the project's own rule
   does not reach.
3. **The payload cap** (`_cap_payload`) — with a loud, stored truncation marker. No
   silent caps (the same rule as ADR 0040 §5).

**The write is same-transaction and fail-closed** (ADR 0041 §2.1). `record` only
`session.add`s the row; the caller's commit is what persists it. So if the audit
write fails, the mutation rolls back with it, and an applied change and its record
can never diverge — the change that "wasn't recorded" also didn't happen. Phase 2
(#431, data-*read* events) must **not** take this contract: its own AC forbids a
latency regression on the read path.

**Machine writes are out of scope**, deliberately — a run insert, a `lineage_edges`
refresh, an `assets.last_seen` bump, an inventory sync, a retention purge, and the
bulk-DML deletes. Auditing those would bury the actor-attributable events in noise.
"""

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

# ── Redaction ─────────────────────────────────────────────────────────────────
#
# The **credential subset** of `_PII_KEYS`, not the whole set (ADR 0041 §2.6.4).
# `_PII_KEYS` also carries `name`, `display_name`, `user_id` and `email`, which are
# tuned for log lines and are actively WRONG here: `name` is the *content* of a
# rename event, and `actor_label` — an email or display name — is the entire point
# of the actor record. Reusing the whole set would redact the audit log into
# uselessness while looking maximally careful.
#
# `_CREDENTIAL_KEYS` is asserted to be a SUBSET of `_PII_KEYS` by a drift guard
# test, so a key removed from the logger's set cannot silently stop being scrubbed
# here — the same arrangement `core/logging.py` already uses to pin the
# `dq_live_`/`dq_sess_` prefixes to their services.
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

#: Payload byte budget, measured on the serialized JSON. A custom-SQL body or a
#: `schema_drift` baseline can be large, and an audit row must not become the
#: reason a mutation fails. Truncation is LOUD — see `_TRUNCATION_KEY`.
MAX_PAYLOAD_BYTES: Final[int] = 16_384
#: Written into a truncated payload alongside whatever survived. A reader must be
#: able to tell "this field was absent" from "this field was dropped for size" —
#: a silent cap reads as a complete record, which is the failure this table exists
#: to prevent.
_TRUNCATION_KEY: Final[str] = "_truncated"


def _scrub_value(value: Any) -> Any:
    """Recursively scrub a value: credential-shaped KEYS are replaced wholesale,
    and every string is passed through the logger's own secret-string scrubber
    (query-param credentials, URL userinfo, bare bearer tokens).

    The key match is case-insensitive and exact, matching `_redact_pii`'s
    contract — a SHAPE-based match cannot be fooled by a token format nobody
    anticipated, which is precisely how #849 happened.
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
    """Coerce to something the JSONB encoder accepts.

    `Decimal` is called out because it is the #1273 class exactly: NUMERIC
    thresholds reach the JSON encoder as `Decimal` and raise, and a check with no
    thresholds never exercises the path — so the crash waits for the first audited
    check that has one. `warn/fail/critical_threshold` are NUMERIC on `checks`, so
    this is on the very first serializer below, not a hypothetical.
    """
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
    the budget check and the per-field measurement cannot drift apart."""
    return len(json.dumps(value, default=str).encode())


def _cap_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Bound the serialized payload to `MAX_PAYLOAD_BYTES`, dropping the largest
    fields first and recording WHICH ones went.

    Dropping largest-first is what makes the surviving record most informative: an
    oversized payload is almost always one giant field (a custom-SQL body, a drift
    baseline) beside a dozen small ones that are individually the interesting part.
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
        # The marker is part of the payload, so it is part of the budget. Measuring
        # WITHOUT it and appending afterwards — as an earlier version did — puts the
        # stored row over the limit it advertises, every single time it truncates.
        if _encoded_size({**kept, _TRUNCATION_KEY: _marker(dropped)}) <= MAX_PAYLOAD_BYTES:
            break
        kept.pop(key, None)
        dropped.append(key)
    # A payload reduced to nothing but its marker is the honest outcome when a
    # SINGLE field blows the budget (an enormous custom-SQL body). It says "there
    # was a record and this is why you cannot see it", which is the point; keeping
    # the oversized field to avoid an empty-looking row would defeat the cap.
    kept[_TRUNCATION_KEY] = _marker(dropped)
    return kept


# ── Per-entity payload allow-lists ────────────────────────────────────────────
#
# One serializer per entity type. These are the ONLY fields that can ever reach a
# payload. Three standing prohibitions, each with a concrete reason:
#
# * **No secret values, ever, including "redacted in place."** They are not in the
#   DB to begin with — `connections.config` holds `*_secret_name` POINTERS and
#   `secret_ref`, never a credential. A `connection.reauth` event therefore records
#   *that* the credential rotated and *which pointer*, never a before/after of the
#   value.
# * **No warehouse data, in either phase.** `results.sample_failures` and
#   `results.observed_value` are the incidental PII/PHI stores; copying them into an
#   append-only table with a LONGER retention would silently defeat the #1253 purge
#   and the #432 erasure path.
# * **No `dict(row)` shortcut.** See the module docstring.

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

# `config` is deliberately ABSENT. It is the JSONB blob that carries every
# `*_secret_name` pointer and every adapter-specific field, and it is the one place
# a future adapter could put something credential-shaped without this file
# noticing. The audited facts about a connection are its identity and its
# credential POINTERS, which are listed explicitly below.
_CONNECTION_FIELDS: Final[tuple[str, ...]] = (
    "id",
    "name",
    "type",
    "env",
    # The credential POINTER, never the credential. Recording which pointer a
    # connection resolves through is exactly what makes a `connection.reauth`
    # event meaningful (ADR 0020 shipped credential rotation as a known
    # unrecorded hole); recording the value would be the one thing this table
    # must never do.
    "secret_ref",
)

_SUITE_FIELDS: Final[tuple[str, ...]] = (
    "id",
    "name",
    "description",
    "connection_id",
    "target",
    # The redaction override itself (#415). A change to `pii_columns` /
    # `identifier_column` changes what personal data the product will surface, so
    # it is among the highest-value config events in the table.
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

# Metadata mutation only (ADR 0041 §2.5). `first_seen`/`last_seen` and the whole
# inventory-sync column family are machine writes and are deliberately absent —
# auditing them would bury the actor-attributable events in noise.
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
    """A caller asked to audit an entity type with no declared allow-list.

    Raised rather than falling back to `dict(row)` or to an empty payload. An
    unknown entity type means a new audited surface was added without declaring
    what is safe to record about it, and guessing is precisely the fail-open the
    allow-list exists to prevent.
    """


def snapshot(entity_type: str, entity: Any) -> dict[str, Any] | None:
    """Build an allow-listed, scrubbed, JSON-safe payload for `entity`.

    `None` in → `None` out, so a create event's `before` and a delete event's
    `after` are naturally null rather than an empty dict that reads as "we looked
    and it was blank".

    **The entity_type is validated FIRST, before that None short-circuit.** An
    earlier version checked `entity is None` first, which made the guard
    unreachable on exactly the path that needs it most: a delete passes
    `entity=None`, so an undeclared or typo'd `entity_type` was recorded silently
    there while the same typo raised everywhere else. A guard that is skipped on
    one of its doors is the shape this module exists to avoid.
    """
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
    """Add one audit row to the caller's transaction. **The caller commits.**

    That is the fail-closed contract (ADR 0041 §2.1): the event and the mutation it
    records commit together or not at all, so a change can never be applied without
    its record. `record` therefore never commits, never flushes on its own behalf,
    and never swallows an exception — a broken audit write is *supposed* to fail
    the mutation.

    `actor` is a `User` row, a bare `actor_id` UUID, or `None` for a webhook
    principal (which has no user). The UUID form exists because the service layer
    threads `actor_id: uuid.UUID`, not the row — accepting only the row would mean
    changing thirty service signatures to satisfy the audit seam, which is the
    tail wagging the dog. Resolving it costs nothing in practice: the
    authentication dependency has already loaded that `User` into the same
    session, so `session.get` is an identity-map hit rather than a query.

    `actor_label` is denormalized here, at write time, so the attribution survives
    both the `ON DELETE SET NULL` and any later rename.
    """
    # Both discriminators are validated here, not just one. Each carries a DB
    # CHECK, so an invalid value is caught either way — but only at COMMIT, as an
    # `IntegrityError` that (the write being same-transaction and fail-closed)
    # aborts the user's mutation with an opaque database error. Raising at the
    # call site turns a production 500 into a failing test.
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
        # The id was given but the row could not be loaded (a user deleted between
        # the request and this write). Attribution is preserved in `actor_label`,
        # NOT in `actor_user_id`.
        #
        # An earlier version put the id in the FK column "so the event still says
        # WHO". That inverted the intent: the column is a real foreign key, so a
        # user id with no row fails the INSERT — and because several callers wrap
        # their commit in `except IntegrityError` to map a duplicate to 409, the
        # failure surfaced as a bogus conflict on an unrelated resource. The
        # graceful-degradation branch was the one that broke the request.
        label = f"<unresolved user {actor}>"
    # The module's three guarantees — allow-list, redaction, cap — must hold on
    # EVERY payload, not only the ones `snapshot` built. A slice-2 caller
    # hand-building a `before`/`after` dict (a role change, a share grant) would
    # otherwise bypass all three: an unredacted credential could be stored, and a
    # stray `Decimal` would hit the #1273 JSON-encoder crash which — the write
    # being same-transaction and fail-closed — rolls back the user's mutation. The
    # allow-list still cannot be applied to a free-form dict, so this is the two
    # halves that CAN be: scrub, then coerce, then cap. Re-running them over a
    # `snapshot` result is idempotent and cheap.
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
    """`record`, with the `after` payload built from `entity` via the allow-list.

    ``if_changed`` skips the write when the allow-listed payload is identical
    before and after, returning ``None``. Use it on partial-update paths, where a
    PATCH that sets a field to its current value — or names no fields at all — is
    a real request that changed nothing. Recording those produces a log whose
    entries mostly did not happen, and a reader learns to skim it; `check_service`
    already applies the same rule via `session.is_modified`, so this makes the
    other update paths consistent with it rather than each inventing an answer.

    It is deliberately opt-in: a create and a delete have nothing to compare, and
    an entity whose audited payload is unchanged while an UNAUDITED field moved
    (`schedules.next_run_at`, say) should still be skipped — that is a config
    non-event by the allow-list's own definition of config.

    The common shape: the caller captures `before = snapshot(...)` ahead of the
    mutation and hands the mutated entity here. For a delete, pass the pre-delete
    snapshot as `before` and let `entity` be `None` — the audit row is then the
    only surviving record of what was destroyed, which is the whole reason this
    table has no foreign key on `entity_id`.

    **A pending entity is flushed first, and that is load-bearing.** `id` is
    `gen_random_uuid()` — a SERVER default with no Python-side counterpart — so on
    a CREATE the attribute is still `None` until the row reaches the database.
    Without the flush the event would store `entity_id = NULL` and `after.id =
    null`, and the `(entity_type, entity_id, occurred_at)` index read — "everything
    that happened to this check" — would never return the creation event. The row
    would exist and look fine; only the query that matters would come up short.

    A flush is not a commit: it stays inside the caller's transaction and rolls
    back with it, so the fail-closed contract is untouched. It is scoped to this
    one object rather than the whole session, so auditing cannot force an
    unrelated pending change to the database early.
    """
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


# ── Phase 2: data-access events (G1 / #431) ───────────────────────────────────
#
# The opposite latency and failure contract from phase 1, deliberately (ADR 0041
# §2.1). A config event is written INSIDE the mutation's transaction and is
# fail-closed: if it cannot be written, the change must not happen. A read event
# cannot take that contract — #431's own AC forbids a latency regression on the
# read path, and failing a legitimate read because the audit insert failed trades
# a real outage for a bookkeeping problem.
#
# **What it records, and what it must never record.** A read event names WHICH
# result was read and WHETHER regulated data was actually surfaced — never WHAT it
# contained (ADR 0041 §2.6.3). Copying `sample_failures` or `observed_value` into
# an append-only table with a longer retention would silently defeat both the
# #1253 purge and the #432 erasure path, which is the exact failure this whole
# design exists to avoid.
#
# **`exposed` is the field that makes the log answer the question it is for.** The
# HIPAA question is "who accessed PHI", not "who opened a page". A read whose
# sample came back fully redacted surfaced nothing regulated, and recording it
# identically to one that surfaced real failing rows would bury the handful of
# events an investigator actually wants among the many they do not.


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
    """Record a read of regulated data. **Never raises, never blocks the read.**

    Written in a SAVEPOINT so a failed audit insert cannot poison the caller's
    session: a read path holds no other pending work, so rolling the savepoint
    back is harmless, whereas letting the error escape would turn a successful
    read into a 500 — the outcome AC-3 exists to prevent.

    **It COMMITS, and that is not an optional convenience.** Phase 2 is by
    definition not part of the caller's transaction (phase 1 is; that asymmetry is
    the whole point of ADR 0041 §2.1), and the request-scoped `get_db` session
    never commits on its own — services do, and a *read* route has nothing to
    commit, so it does not. An access event merely `add`-ed to that session is
    therefore rolled back by `db.close()` and **never persists**.

    That is not hypothetical: it is exactly what the first version of this code
    did. The read returned 200, the row was added, and nothing reached the
    database — while four tests passed, because the test fixture holds an outer
    transaction the assertions read from. It was found by re-reading the event on
    an INDEPENDENT session against a real database, and the regression test now
    does the same. Committing here, rather than asking every caller to remember,
    is what removes the whole class.

    A failure is logged at ERROR with `audit_access_write_failed`, deliberately
    loudly: this is a compliance control, so "it silently stopped recording" must
    be visible in telemetry rather than discovered during an audit. That is the
    honest residue of not being fail-closed, and it is stated rather than implied.
    """
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
        # `expire_on_commit` is True by default, so this commit would expire every
        # ORM object the CALLER is still holding — and a read path commits in the
        # middle of building its response. A 50-result run then issues ~50 refresh
        # SELECTs on attribute access afterwards, and a re-read of a row deleted
        # concurrently raises `ObjectDeletedError` — a 500 caused entirely by the
        # audit write, on the path whose own AC forbids a latency regression.
        #
        # Suppressed only around this commit, and restored in `finally`, so the
        # caller's own commit semantics are untouched. Safe here because the rows
        # being committed are this event alone: a read path has nothing else
        # pending, which is the same property that makes the savepoint rollback
        # harmless.
        session.expire_on_commit = False
        session.commit()
        return event
    except Exception:
        # Roll back before logging: after a database-level failure the session is
        # in a failed transaction, and leaving it that way would turn the NEXT
        # statement on this request into the visible error instead of this one.
        session.rollback()
        log.error("audit_access_write_failed", action=action, exc_info=True)
        return None
    finally:
        session.expire_on_commit = previously_expiring


def declared_entity_types() -> Sequence[str]:
    """The entity types with a declared allow-list — the drift-guard test's input."""
    return sorted(_SERIALIZERS)
