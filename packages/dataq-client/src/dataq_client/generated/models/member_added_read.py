from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.member_read import MemberRead


T = TypeVar("T", bound="MemberAddedRead")


@_attrs_define
class MemberAddedRead:
    """
    Attributes:
        auto_imported_count (int):
        enforced (bool):
        enforced_reason (None | str):
        enforcement_active (bool):
        member (MemberRead):
    """

    auto_imported_count: int
    enforced: bool
    enforced_reason: None | str
    enforcement_active: bool
    member: MemberRead
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        auto_imported_count = self.auto_imported_count

        enforced = self.enforced

        enforced_reason: None | str
        enforced_reason = self.enforced_reason

        enforcement_active = self.enforcement_active

        member = self.member.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "auto_imported_count": auto_imported_count,
                "enforced": enforced,
                "enforced_reason": enforced_reason,
                "enforcement_active": enforcement_active,
                "member": member,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.member_read import MemberRead

        d = dict(src_dict)
        auto_imported_count = d.pop("auto_imported_count")

        enforced = d.pop("enforced")

        def _parse_enforced_reason(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        enforced_reason = _parse_enforced_reason(d.pop("enforced_reason"))

        enforcement_active = d.pop("enforcement_active")

        member = MemberRead.from_dict(d.pop("member"))

        member_added_read = cls(
            auto_imported_count=auto_imported_count,
            enforced=enforced,
            enforced_reason=enforced_reason,
            enforcement_active=enforcement_active,
            member=member,
        )

        member_added_read.additional_properties = d
        return member_added_read

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
