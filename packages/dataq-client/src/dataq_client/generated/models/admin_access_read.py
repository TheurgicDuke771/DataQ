from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="AdminAccessRead")


@_attrs_define
class AdminAccessRead:
    """
    Attributes:
        grant_id (None | UUID):
        permission (str):
        suite_id (UUID):
        suite_name (str):
        user_email (str):
        user_id (UUID):
        user_name (None | str):
    """

    grant_id: None | UUID
    permission: str
    suite_id: UUID
    suite_name: str
    user_email: str
    user_id: UUID
    user_name: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        grant_id: None | str
        if isinstance(self.grant_id, UUID):
            grant_id = str(self.grant_id)
        else:
            grant_id = self.grant_id

        permission = self.permission

        suite_id = str(self.suite_id)

        suite_name = self.suite_name

        user_email = self.user_email

        user_id = str(self.user_id)

        user_name: None | str
        user_name = self.user_name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "grant_id": grant_id,
                "permission": permission,
                "suite_id": suite_id,
                "suite_name": suite_name,
                "user_email": user_email,
                "user_id": user_id,
                "user_name": user_name,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

        def _parse_grant_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                grant_id_type_0 = UUID(data)

                return grant_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        grant_id = _parse_grant_id(d.pop("grant_id"))

        permission = d.pop("permission")

        suite_id = UUID(d.pop("suite_id"))

        suite_name = d.pop("suite_name")

        user_email = d.pop("user_email")

        user_id = UUID(d.pop("user_id"))

        def _parse_user_name(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        user_name = _parse_user_name(d.pop("user_name"))

        admin_access_read = cls(
            grant_id=grant_id,
            permission=permission,
            suite_id=suite_id,
            suite_name=suite_name,
            user_email=user_email,
            user_id=user_id,
            user_name=user_name,
        )

        admin_access_read.additional_properties = d
        return admin_access_read

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
