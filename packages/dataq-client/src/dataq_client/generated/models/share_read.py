from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="ShareRead")


@_attrs_define
class ShareRead:
    """
    Attributes:
        display_name (None | str):
        email (str):
        permission (str):
        suite_id (UUID):
        user_id (UUID):
    """

    display_name: None | str
    email: str
    permission: str
    suite_id: UUID
    user_id: UUID
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        display_name: None | str
        display_name = self.display_name

        email = self.email

        permission = self.permission

        suite_id = str(self.suite_id)

        user_id = str(self.user_id)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "display_name": display_name,
                "email": email,
                "permission": permission,
                "suite_id": suite_id,
                "user_id": user_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

        def _parse_display_name(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        display_name = _parse_display_name(d.pop("display_name"))

        email = d.pop("email")

        permission = d.pop("permission")

        suite_id = UUID(d.pop("suite_id"))

        user_id = UUID(d.pop("user_id"))

        share_read = cls(
            display_name=display_name,
            email=email,
            permission=permission,
            suite_id=suite_id,
            user_id=user_id,
        )

        share_read.additional_properties = d
        return share_read

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
