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
it again **in the same transaction** — which is load-bearing, not tidiness. A
`GRANT` is transactional in Postgres (verified directly: a rolled-back `GRANT`
leaves `has_table_privilege` false; only a commit persists it), so committing it
separately from the `REVOKE` would mean a crashed worker leaves `DELETE` granted
**permanently**, and a long sweep leaves the guard off **workspace-wide** for its
whole duration. Keeping the pair in one transaction makes each committed batch a
net-zero privilege change, which no other session can observe.

Stated plainly, because it looks like a loophole and is instead the honest shape:

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
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, select, text
from sqlalchemy.orm import Session, selectinload

from backend.app.core.config import get_settings
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.chunked_dml import CHUNK_SIZE
from backend.app.db.models import AuditEvent
from backend.app.services import audit_service

log = get_logger(__name__)


class AuditFilterInvalidError(DataQError):
    """A filter names a value that cannot exist — a 422, never an empty page.

    Deliberately an error rather than an empty result: on this table an empty page
    is a statement about the workspace ("nobody did that"), so a typo must not be
    able to make one.
    """

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(
            code="audit_filter_invalid", message=message, status_code=422, detail=detail
        )


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
    #: The configured retention window, and the timestamp before which events have
    #: been swept. Present because pagination honesty is not the only honesty this
    #: page needs: a query for a window older than the retention period returns
    #: `total: 0`, which is indistinguishable from "nothing happened then" — the
    #: single most misleading answer an audit log can give. A reader can now tell
    #: "no events" from "no longer retained".
    retention_days: int
    retained_since: datetime | None


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
    retention_days: int | None = None,
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

    # An unvalidated filter that matches nothing returns `total: 0`, and on an
    # audit log that reads as "nothing happened" rather than "you asked for a
    # thing that does not exist" — the #828 class, in the place it is least
    # affordable. The write path already refuses an undeclared entity type, so the
    # read path refuses the same vocabulary rather than inventing a second one.
    if entity_type is not None and entity_type not in audit_service.declared_entity_types():
        raise AuditFilterInvalidError(
            f"unknown entity_type {entity_type!r}",
            detail={
                "entity_type": entity_type,
                "known": list(audit_service.declared_entity_types()),
            },
        )

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
    retention = get_settings().audit_retention_days if retention_days is None else retention_days
    return AuditPage(
        events=events,
        total=total,
        truncated=offset + len(events) < total,
        retention_days=retention,
        # None when the sweep is disabled — "nothing has been swept" is a different
        # statement from "swept back to the beginning of time", and collapsing them
        # would be the same conflation this field exists to prevent.
        retained_since=(datetime.now(UTC) - timedelta(days=retention) if retention > 0 else None),
    )


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

    **Each batch is GRANT → DELETE → REVOKE inside ONE transaction, and that is
    the whole design of this function.** Two properties follow, and neither is
    available if the privilege change is committed separately from the delete:

    * **A crash cannot leave the guard off.** `GRANT` is transactional in
      Postgres — verified directly, not assumed: a `GRANT` rolled back leaves
      `has_table_privilege` false, and only a commit persists it. So a worker
      SIGKILLed mid-sweep rolls back its open transaction and the privilege goes
      with it. Committing the `GRANT` separately would leave `DELETE ON
      audit_events` granted **permanently**, with nothing to notice it.
    * **No other session ever observes the guard off.** The net privilege change
      of each committed transaction is zero, so a concurrent session sees the
      before state or the after state and they are identical. A `GRANT` committed
      at the start of a long sweep is visible workspace-wide for its whole
      duration.

    This is why the shared `chunked_dml` helper is deliberately NOT used here,
    despite this being a chunked retention sweep exactly like its three siblings:
    that helper commits **per batch**, which is correct for them and is precisely
    the thing that must not happen across the privilege boundary. `CHUNK_SIZE` is
    still imported from it, so the sweeps stay on one convention for the one thing
    they genuinely share.

    Batching still matters for the same reason it does in the siblings: a
    first-enable catch-up over a long backlog must not hold one long transaction
    against the mutation path — which matters *more* here, because the writers it
    would block are fail-closed, so a blocked audit write does not queue, it rolls
    back the user's mutation.
    """
    if retention_days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)

    total = 0
    while True:
        try:
            _set_delete_privilege(session, granted=True)
            result = session.execute(
                delete(AuditEvent).where(
                    AuditEvent.occurred_at < cutoff,
                    AuditEvent.id.in_(
                        select(AuditEvent.id)
                        .where(AuditEvent.occurred_at < cutoff)
                        .order_by(AuditEvent.occurred_at)
                        .limit(CHUNK_SIZE)
                        .scalar_subquery()
                    ),
                )
            )
            # `max(..., 0)`, not a bare `or 0`: some DB-API drivers return -1 for
            # "unknown rowcount", which is truthy — it would corrupt both the
            # running total and the `affected == 0` termination check, spinning
            # this loop forever. Copied deliberately from `chunked_dml`, which
            # carries the same floor for the same reason (#323 review F6, where
            # one of two hand-rolled sweep loops had already lost it).
            affected = max(cast(CursorResult[Any], result).rowcount or 0, 0)
            _set_delete_privilege(session, granted=False)
            session.commit()
        except Exception:
            # Rollback FIRST. After a database-level error the session is in a
            # failed transaction and every further statement raises
            # `PendingRollbackError` — so an attempt to re-revoke here would mask
            # the real error rather than restore the guard. The rollback is what
            # actually restores it, by undoing the GRANT.
            session.rollback()
            raise
        total += affected
        # Exit on zero rather than `affected < CHUNK_SIZE`: a chunk size that
        # evenly divides the backlog never produces a partial batch, and a
        # concurrent delete can shrink one batch while a real backlog remains.
        if affected == 0:
            break

    if total:
        log.info("audit_events_purged", deleted=total, retention_days=retention_days)
    return total


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
