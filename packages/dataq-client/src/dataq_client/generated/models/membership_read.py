from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.member_read import MemberRead


T = TypeVar("T", bound="MembershipRead")


@_attrs_define
class MembershipRead:
    """
    Attributes:
        enforced (bool):
        enforced_reason (None | str):
        enforcement_active (bool):
        env_allowed_domains (list[str]):
        members (list[MemberRead]):
        unmanaged_user_count (int):
    """

    enforced: bool
    enforced_reason: None | str
    enforcement_active: bool
    env_allowed_domains: list[str]
    members: list[MemberRead]
    unmanaged_user_count: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        enforced = self.enforced

        enforced_reason: None | str
        enforced_reason = self.enforced_reason

        enforcement_active = self.enforcement_active

        env_allowed_domains = self.env_allowed_domains

        members = []
        for members_item_data in self.members:
            members_item = members_item_data.to_dict()
            members.append(members_item)

        unmanaged_user_count = self.unmanaged_user_count

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "enforced": enforced,
                "enforced_reason": enforced_reason,
                "enforcement_active": enforcement_active,
                "env_allowed_domains": env_allowed_domains,
                "members": members,
                "unmanaged_user_count": unmanaged_user_count,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.member_read import MemberRead

        d = dict(src_dict)
        enforced = d.pop("enforced")

        def _parse_enforced_reason(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        enforced_reason = _parse_enforced_reason(d.pop("enforced_reason"))

        enforcement_active = d.pop("enforcement_active")

        env_allowed_domains = cast(list[str], d.pop("env_allowed_domains"))

        members = []
        _members = d.pop("members")
        for members_item_data in _members:
            members_item = MemberRead.from_dict(members_item_data)

            members.append(members_item)

        unmanaged_user_count = d.pop("unmanaged_user_count")

        membership_read = cls(
            enforced=enforced,
            enforced_reason=enforced_reason,
            enforcement_active=enforcement_active,
            env_allowed_domains=env_allowed_domains,
            members=members,
            unmanaged_user_count=unmanaged_user_count,
        )

        membership_read.additional_properties = d
        return membership_read

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
