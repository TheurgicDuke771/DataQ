from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from typing_extensions import Self

from ..models.share_create_permission import ShareCreatePermission

T = TypeVar("T", bound="ShareCreate")


@_attrs_define
class ShareCreate:
    """
    Attributes:
        permission (ShareCreatePermission):
        user_id (UUID):
    """

    permission: ShareCreatePermission
    user_id: UUID

    def to_dict(self) -> dict[str, Any]:
        permission = self.permission.value

        user_id = str(self.user_id)

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "permission": permission,
                "user_id": user_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        permission = ShareCreatePermission(d.pop("permission"))

        user_id = UUID(d.pop("user_id"))

        share_create = cls(
            permission=permission,
            user_id=user_id,
        )

        return share_create
