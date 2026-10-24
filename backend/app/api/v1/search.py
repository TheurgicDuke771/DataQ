"""Global search — the one read behind the command palette (⌘K)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel
from backend.app.core.auth import get_current_user
from backend.app.core.roles import is_workspace_admin
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.services import search_service as svc

router = APIRouter(tags=["search"])


class SearchHitRead(ApiModel):
    """One match. `label` is the entity's name (a run is labelled by its suite); `href` is the
    SPA path that opens it for THIS caller — a check the caller may only view opens its suite,
    a connection opens its own page only for a workspace admin. `when` is set on a run (when it
    started, or was queued if it never started)."""

    id: uuid.UUID
    label: str
    sublabel: str | None = None
    href: str
    when: datetime | None = None


class SearchGroupsRead(ApiModel):
    suites: list[SearchHitRead] = Field(default_factory=list)
    checks: list[SearchHitRead] = Field(default_factory=list)
    connections: list[SearchHitRead] = Field(default_factory=list)
    runs: list[SearchHitRead] = Field(default_factory=list)


class SearchResponse(ApiModel):
    """Matches grouped by entity, each group capped at `limit`. `truncated_groups` names the
    groups with more matches than returned (from the real count — a full group is not by itself
    truncated). Every group is scoped like its own list endpoint: a suite the caller cannot see
    is absent, and so are its checks and runs."""

    query: str
    groups: SearchGroupsRead
    truncated_groups: list[svc.GroupName] = Field(default_factory=list)


def _hits(hits: list[svc.SearchHit]) -> list[SearchHitRead]:
    return [
        SearchHitRead(id=h.id, label=h.label, sublabel=h.sublabel, href=h.href, when=h.when)
        for h in hits
    ]


@router.get(
    "/search",
    response_model=SearchResponse,
    summary="Search suites, checks, connections and runs by name",
)
def search(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    q: Annotated[
        str,
        Query(
            min_length=1,
            max_length=svc.MAX_QUERY_LEN,
            description=(
                "Case-insensitive substring; prefix matches rank first. A run matches on its "
                "suite's name or on a prefix of its id."
            ),
        ),
    ],
    limit: Annotated[int, Query(ge=1, le=svc.MAX_LIMIT)] = svc.DEFAULT_LIMIT,
) -> SearchResponse:
    found = svc.search(
        db,
        user_id=current_user.id,
        query=q,
        limit=limit,
        is_admin=is_workspace_admin(current_user),
    )
    return SearchResponse(
        query=found.query,
        groups=SearchGroupsRead(
            suites=_hits(found.suites),
            checks=_hits(found.checks),
            connections=_hits(found.connections),
            runs=_hits(found.runs),
        ),
        truncated_groups=found.truncated_groups,
    )
