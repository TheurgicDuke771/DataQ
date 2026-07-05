"""Personal access tokens (PATs) — mint / list / revoke (ADR 0026 phase 1, #461).

User-scoped: every route operates on the caller's own keys. The plaintext token
appears exactly once, in the creation response; list/read return metadata only
(prefix, expiry, revocation, last-used). Revocation is a soft mark
(`revoked_at`), keeping the row for audit.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel
from backend.app.core.auth import get_current_user
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.services import api_key_service as svc

router = APIRouter(tags=["auth"])


class ApiKeyCreate(ApiModel):
    name: str = Field(min_length=1, max_length=128, description="Label, e.g. 'ci-smoke'")
    expires_in_days: int = Field(
        default=svc.DEFAULT_EXPIRY_DAYS,
        ge=1,
        le=svc.MAX_EXPIRY_DAYS,
        description="Days until the key expires (no non-expiring keys)",
    )


class ApiKeyRead(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    key_prefix: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None


class ApiKeyCreated(ApiKeyRead):
    """Creation response — the ONLY place the plaintext token ever appears."""

    token: str = Field(description="The API key. Shown once; store it now.")


@router.post(
    "/me/api-keys",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Mint an API key (personal access token) — plaintext shown once",
)
def create_api_key(
    payload: ApiKeyCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiKeyCreated:
    """Mint a PAT that authenticates as you (`Authorization: Bearer dq_live_…`)
    on the REST API and `/mcp` alike, inheriting your per-suite access."""
    key, token = svc.create_key(
        db, current_user, name=payload.name, expires_in_days=payload.expires_in_days
    )
    return ApiKeyCreated(**ApiKeyRead.model_validate(key).model_dump(), token=token)


@router.get(
    "/me/api-keys",
    response_model=list[ApiKeyRead],
    summary="List your API keys (metadata only — never the token)",
)
def list_api_keys(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[ApiKeyRead]:
    return [ApiKeyRead.model_validate(k) for k in svc.list_keys(db, current_user)]


@router.delete(
    "/me/api-keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke one of your API keys",
)
def revoke_api_key(
    key_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Revocation is immediate (the key stops authenticating) and idempotent.
    Another user's key 404s — indistinguishable from a nonexistent one."""
    svc.revoke_key(db, current_user, key_id)
