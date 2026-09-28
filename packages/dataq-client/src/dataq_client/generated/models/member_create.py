from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

from ..models.member_create_initial_role import MemberCreateInitialRole
from ..types import UNSET, Unset

T = TypeVar("T", bound="MemberCreate")


@_attrs_define
class MemberCreate:
    """
    Attributes:
        email (str):
        initial_role (MemberCreateInitialRole | Unset):  Default: MemberCreateInitialRole.MEMBER.
    """

    email: str
    initial_role: MemberCreateInitialRole | Unset = MemberCreateInitialRole.MEMBER

    def to_dict(self) -> dict[str, Any]:
        email = self.email

        initial_role: str | Unset = UNSET
        if not isinstance(self.initial_role, Unset):
            initial_role = self.initial_role.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "email": email,
            }
        )
        if initial_role is not UNSET:
            field_dict["initial_role"] = initial_role

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        email = d.pop("email")

        _initial_role = d.pop("initial_role", UNSET)
        initial_role: MemberCreateInitialRole | Unset
        if isinstance(_initial_role, Unset):
            initial_role = UNSET
        else:
            initial_role = MemberCreateInitialRole(_initial_role)

        member_create = cls(
            email=email,
            initial_role=initial_role,
        )

        return member_create
