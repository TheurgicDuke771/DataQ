from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from backend.app.core.auth import get_current_user
from backend.app.db.models import User

router = APIRouter(tags=["auth"])


class MeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    aad_object_id: str
    email: str
    display_name: str | None
    last_seen_at: datetime | None


@router.get("/me", response_model=MeResponse)
def me(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    return current_user
