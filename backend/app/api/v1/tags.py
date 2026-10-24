"""The tag vocabulary (#888). Assignments are made on the entity (PATCH a suite / asset /
connection / check with `tags`) and read with it; this router is the workspace-wide list and the
Admin-only delete."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import ConfigDict
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel
from backend.app.core.auth import AdminUser, get_current_user
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.services import tag_service as svc

router = APIRouter(tags=["tags"])


class TagUsageRead(ApiModel):
    """How many of each entity carry the tag — workspace-wide, including suites and checks the
    caller cannot open. Counts only: which entities carry it is read from the entities, where the
    suite grant applies."""

    suites: int
    assets: int
    connections: int
    checks: int


class TagRead(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    #: Stored normalized: lower-case, trimmed, single-spaced.
    name: str
    created_at: datetime
    usage: TagUsageRead

    @classmethod
    def of(cls, usage: svc.TagUsage) -> TagRead:
        return cls(
            id=usage.id,
            name=usage.name,
            created_at=usage.created_at,
            usage=TagUsageRead(
                suites=usage.suites,
                assets=usage.assets,
                connections=usage.connections,
                checks=usage.checks,
            ),
        )


@router.get(
    "/tags",
    response_model=list[TagRead],
    summary="List the tag vocabulary",
    description=(
        "Every tag in the workspace, by name, with workspace-wide usage counts. The vocabulary is "
        "shared: a tag exists once any editor assigns the name, and this list shows it to every "
        "member. It never names the entities — read those through `/suites`, `/assets`, "
        "`/connections` and a suite's checks, where the suite grant applies."
    ),
)
def list_tags(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[TagRead]:
    return [TagRead.of(u) for u in svc.list_tags(db)]


@router.delete(
    "/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a tag (workspace-admin only)",
    description=(
        "Removes the tag from the vocabulary and detaches it from every suite, asset, connection "
        "and check that carried it. To take a tag off one entity, PATCH that entity's `tags` "
        "instead."
    ),
)
def delete_tag(
    tag_id: uuid.UUID,
    current_user: AdminUser,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    svc.delete_tag(db, tag_id, actor_id=current_user.id)
