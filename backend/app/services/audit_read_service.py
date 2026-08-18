"""The workspace-admin read surface and retention sweep for `audit_events`
— ADR [0041](../../../docs/adr/0041-history-audit-strategy.md) phase 1 (#1318).

Two things live here and they are opposites, which is why the module says so out
loud: one hands an auditor the record, the other destroys it on a clock. The
second is the reason `AUDIT_RETENTION_DAYS` is deliberately **not** coupled to
`sample_failures_retention_days` (ADR 0041 §2.7) — they protect opposite things.
One keeps a record of what people did; the other destroys personal data that was
incidentally captured. A single knob would force an operator to trade one against
the other, and the sensible settings point in opposite directions: a short
PII-minimisation window and a long accountability window.

**The sweep has to work around the migration's own `REVOKE`, and that is the
interesting part of this file.** ADR 0041 §2.7 requires retention to run as
`dataq_app` — a second, less-trusted database role is forbidden, because the
single-`dataq_app`-role model is the whole mitigation for the unpatched Postgres
referential-integrity owner-switched-cast escalation. But the same ADR revokes
`DELETE` on this table from that role. Both requirements are real and they
collide, which was verified live rather than inferred: after the migration the
role holds INSERT/SELECT and neither UPDATE nor DELETE, and a plain `DELETE FROM
audit_events` is refused with `permission denied`.

So the sweep re-grants `DELETE` for the duration of its own statement and revokes
it again, in the same transaction. Stated plainly, because it looks like a
loophole and is instead the honest shape:

* The table's owner can always do this. That is exactly why ADR 0041 §2.7 says
  the `REVOKE` is a guard against **accidental** in-app mutation and **not**
  tamper-resistance. This does not weaken a property the deployment ever had.
* The guard still does its job. A stray ORM `session.delete` or a careless bulk
  `UPDATE` on the request path issues no `GRANT`, so it still fails loudly. What
  the `REVOKE` buys is that deleting audit rows requires *code that says it is
  deleting audit rows* — which is this module, and nothing else.
* Real tamper-evidence needs cryptographic chaining anchored outside the
  database, and is deferred to #431 where the requirement actually lives.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session, selectinload

from backend.app.core.logging import get_logger
from backend.app.db.chunked_dml import chunked_dml
from backend.app.db.models import AuditEvent

log = get_logger(__name__)

#: Hard ceiling on a single page. A read surface with no ceiling is a way to pull
#: the whole table through the API one request at a time, and this table is the
#: one an auditor is most interested in exfiltrating wholesale.
MAX_PAGE_SIZE = 200


@dataclass(frozen=True)
class AuditPage:
    """One page of events plus the honesty fields a reader needs to interpret it.

    `total` and `truncated` exist because a page of `limit` rows is
    indistinguishable from "that is everything" — and on an audit log, "there are
    no more events" is a conclusion someone might actually act on. `truncated` is
    computed against the real total rather than from `len(events) == limit`, which
    is wrong on the exact-boundary page.
    """

    events: Sequence[AuditEvent]
    total: int
    truncated: bool


def list_events(
    session: Session,
    *,
    action_class: str | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    action: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> AuditPage:
    """Query the audit log, newest first. **Workspace-admin only — gated at the
    API, not here**, matching how every other admin read in this codebase is
    arranged (`admin_service`).

    Every filter is optional and every one is an equality or a range, so the
    predicates line up with the three indexes the migration created:
    `(entity_type, entity_id, occurred_at desc)`, `(action_class, occurred_at
    desc)` and `(actor_user_id, occurred_at desc)`.

    The author is eager-loaded because the natural rendering of an event names
    its actor, and this is the only query that needs it — an N+1 across a page of
    200 would be the whole cost of the endpoint.
    """
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    offset = max(0, offset)

    filters = []
    if action_class is not None:
        filters.append(AuditEvent.action_class == action_class)
    if entity_type is not None:
        filters.append(AuditEvent.entity_type == entity_type)
    if entity_id is not None:
        filters.append(AuditEvent.entity_id == entity_id)
    if actor_user_id is not None:
        filters.append(AuditEvent.actor_user_id == actor_user_id)
    if action is not None:
        filters.append(AuditEvent.action == action)
    if since is not None:
        filters.append(AuditEvent.occurred_at >= since)
    if until is not None:
        filters.append(AuditEvent.occurred_at < until)

    total = int(session.scalar(select(func.count()).select_from(AuditEvent).where(*filters)) or 0)
    events = list(
        session.scalars(
            select(AuditEvent)
            .where(*filters)
            .options(selectinload(AuditEvent.actor))
            # `id` breaks the tie: `occurred_at` has a `now()` default, so events
            # written in the same transaction share a timestamp EXACTLY (Postgres
            # freezes `now()` per transaction). Without it, paging over a suite
            # create and its checks could repeat or skip rows between pages —
            # deterministic-looking and wrong.
            .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return AuditPage(events=events, total=total, truncated=offset + len(events) < total)


def _set_delete_privilege(session: Session, *, granted: bool) -> None:
    """Grant or revoke `DELETE ON audit_events` for the connected role.

    `CURRENT_USER` rather than a hardcoded role name: the deployed role is
    `dataq_app`, but a self-hosted deployment may have named it anything, and the
    grant has to follow whoever is actually connected. Deliberately NOT
    parameterized — a Postgres role name is an identifier and cannot be bound;
    `CURRENT_USER` is a keyword, so no user input reaches the statement at all,
    which is a stronger property than escaping would give.
    """
    verb = "GRANT" if granted else "REVOKE"
    preposition = "TO" if granted else "FROM"
    session.execute(text(f"{verb} DELETE ON audit_events {preposition} CURRENT_USER"))


def purge_expired_events(session: Session, *, retention_days: int) -> int:
    """Delete `audit_events` older than `retention_days`. Returns the count.

    ``retention_days <= 0`` no-ops and returns 0 — the same "clean off-switch,
    never an unconditional wipe" contract every sibling sweep enforces
    (`purge_expired_sample_failures`, `purge_expired_codes`,
    `sweep_orphan_assets`, `sweep_orphan_secrets`). Load-bearing rather than
    defensive: the cutoff is `now - retention_days`, so a non-positive value
    collapses it to "now" and **every row matches**, including the event written a
    moment ago. On this table that is the difference between a disabled sweep and
    erasing the entire audit trail.

    Chunked and individually committed, like its siblings, so a first-enable
    catch-up over a long backlog never holds one long transaction against the
    mutation path — which matters more here than elsewhere, because the writers
    it would block are fail-closed: a blocked audit write does not queue, it rolls
    back the user's mutation.
    """
    if retention_days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)

    # See the module docstring: the migration revokes DELETE from this very role,
    # so the sweep has to hand it back for the length of its own work. The
    # try/finally is not decoration — leaving DELETE granted after a failed batch
    # would silently undo the guard for every later request on this connection.
    _set_delete_privilege(session, granted=True)
    try:
        deleted = chunked_dml(
            session,
            lambda: delete(AuditEvent).where(
                AuditEvent.occurred_at < cutoff,
                AuditEvent.id.in_(
                    select(AuditEvent.id)
                    .where(AuditEvent.occurred_at < cutoff)
                    .order_by(AuditEvent.occurred_at)
                    .limit(500)
                    .scalar_subquery()
                ),
            ),
        )
    finally:
        _set_delete_privilege(session, granted=False)
        session.commit()

    if deleted:
        log.info("audit_events_purged", deleted=deleted, retention_days=retention_days)
    return deleted


def as_dict(event: AuditEvent) -> dict[str, Any]:
    """Serialize one event for the read API.

    `actor_label` is served alongside the resolved `actor_display` rather than
    instead of it: they differ exactly when the actor was renamed or deleted since
    the event, and that difference is information an auditor wants rather than a
    discrepancy to hide.
    """
    return {
        "id": str(event.id),
        "occurred_at": event.occurred_at.isoformat(),
        "action_class": event.action_class,
        "action": event.action,
        "entity_type": event.entity_type,
        "entity_id": str(event.entity_id) if event.entity_id else None,
        "actor_user_id": str(event.actor_user_id) if event.actor_user_id else None,
        "actor_kind": event.actor_kind,
        "actor_label": event.actor_label,
        "actor_display": event.actor_display,
        "before": event.before,
        "after": event.after,
        "request_id": event.request_id,
    }
