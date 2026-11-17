"""What the Profile page shows of a user's suite access: counts per level and the suites they own.

The numbers are exactly what ``GET /suites`` stamped on each suite's ``my_permission``
(`suite_authz.effective_permissions`), counted in the database instead of by listing every suite:

- **Admin:** every suite is visible; each is ``owner`` if the admin created it, else ``admin``
  (ADR 0033), whether or not anyone shared it. Per-share counts do not exist for them.
- **Member:** the suites they created (``owner``) plus the shares they hold on suites they did not
  create, at the share's level.
- **Viewer:** every suite they can reach is capped to ``view``, so they own none.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.roles import resolve_role
from backend.app.db.models import (
    ADMIN_ROLE,
    VIEWER_ROLE,
    Share,
    Suite,
    User,
)

LEVELS = ("owner", "admin", "edit", "view")


@dataclass(frozen=True)
class OwnedSuite:
    id: uuid.UUID
    name: str
    description: str | None


@dataclass(frozen=True)
class AccessSummary:
    total_visible: int
    counts_by_level: dict[str, int]
    owned: list[OwnedSuite]


def access_summary(session: Session, user: User) -> AccessSummary:
    role = resolve_role(user)
    created = session.execute(
        select(Suite.id, Suite.name, Suite.description)
        .where(Suite.created_by == user.id)
        .order_by(Suite.name, Suite.id)
    ).all()
    counts = dict.fromkeys(LEVELS, 0)

    if role == ADMIN_ROLE:
        total = int(session.scalar(select(func.count(Suite.id))) or 0)
        counts["owner"] = len(created)
        counts["admin"] = total - len(created)
        owned = [OwnedSuite(*row) for row in created]
        return AccessSummary(total, counts, owned)

    shared = session.execute(
        select(Share.permission, func.count())
        .join(Suite, Suite.id == Share.suite_id)
        # `is_distinct_from`: a suite whose creator was deleted has NULL `created_by`, and a share
        # on it still counts (`!=` would drop it).
        .where(Share.user_id == user.id, Suite.created_by.is_distinct_from(user.id))
        .group_by(Share.permission)
    ).all()
    total = len(created) + sum(int(n) for _, n in shared)
    if role == VIEWER_ROLE:
        counts["view"] = total
        return AccessSummary(total, counts, [])
    counts["owner"] = len(created)
    for permission, n in shared:
        counts[permission] += int(n)
    return AccessSummary(total, counts, [OwnedSuite(*row) for row in created])
