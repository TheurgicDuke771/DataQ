from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

from ..models.user_role_update_role import UserRoleUpdateRole
from ..types import UNSET, Unset

T = TypeVar("T", bound="UserRoleUpdate")


@_attrs_define
class UserRoleUpdate:
    """`PATCH /admin/users/{id}/role` body (ADR 0033, #742).

    Attributes:
        role (UserRoleUpdateRole):
        confirm_self (bool | Unset):  Default: False.
    """

    role: UserRoleUpdateRole
    confirm_self: bool | Unset = False

    def to_dict(self) -> dict[str, Any]:
        role = self.role.value

        confirm_self = self.confirm_self

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "role": role,
            }
        )
        if confirm_self is not UNSET:
            field_dict["confirm_self"] = confirm_self

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        role = UserRoleUpdateRole(d.pop("role"))

        confirm_self = d.pop("confirm_self", UNSET)

        user_role_update = cls(
            role=role,
            confirm_self=confirm_self,
        )

        return user_role_update
