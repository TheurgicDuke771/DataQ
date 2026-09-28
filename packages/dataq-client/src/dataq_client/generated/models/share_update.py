from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

from ..models.share_update_permission import ShareUpdatePermission

T = TypeVar("T", bound="ShareUpdate")


@_attrs_define
class ShareUpdate:
    """
    Attributes:
        permission (ShareUpdatePermission):
    """

    permission: ShareUpdatePermission

    def to_dict(self) -> dict[str, Any]:
        permission = self.permission.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "permission": permission,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        permission = ShareUpdatePermission(d.pop("permission"))

        share_update = cls(
            permission=permission,
        )

        return share_update
