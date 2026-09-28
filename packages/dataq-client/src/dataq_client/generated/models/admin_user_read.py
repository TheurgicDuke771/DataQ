from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="AdminUserRead")


@_attrs_define
class AdminUserRead:
    """
    Attributes:
        allowlist_admin (bool):
        created_at (datetime.datetime):
        display_name (None | str):
        email (str):
        id (UUID):
        last_seen_at (datetime.datetime | None):
        owned_suite_count (int):
        role (str):
        shared_suite_count (int):
    """

    allowlist_admin: bool
    created_at: datetime.datetime
    display_name: None | str
    email: str
    id: UUID
    last_seen_at: datetime.datetime | None
    owned_suite_count: int
    role: str
    shared_suite_count: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        allowlist_admin = self.allowlist_admin

        created_at = self.created_at.isoformat()

        display_name: None | str
        display_name = self.display_name

        email = self.email

        id = str(self.id)

        last_seen_at: None | str
        if isinstance(self.last_seen_at, datetime.datetime):
            last_seen_at = self.last_seen_at.isoformat()
        else:
            last_seen_at = self.last_seen_at

        owned_suite_count = self.owned_suite_count

        role = self.role

        shared_suite_count = self.shared_suite_count

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "allowlist_admin": allowlist_admin,
                "created_at": created_at,
                "display_name": display_name,
                "email": email,
                "id": id,
                "last_seen_at": last_seen_at,
                "owned_suite_count": owned_suite_count,
                "role": role,
                "shared_suite_count": shared_suite_count,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        allowlist_admin = d.pop("allowlist_admin")

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        def _parse_display_name(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        display_name = _parse_display_name(d.pop("display_name"))

        email = d.pop("email")

        id = UUID(d.pop("id"))

        def _parse_last_seen_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_seen_at_type_0 = datetime.datetime.fromisoformat(data)

                return last_seen_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        last_seen_at = _parse_last_seen_at(d.pop("last_seen_at"))

        owned_suite_count = d.pop("owned_suite_count")

        role = d.pop("role")

        shared_suite_count = d.pop("shared_suite_count")

        admin_user_read = cls(
            allowlist_admin=allowlist_admin,
            created_at=created_at,
            display_name=display_name,
            email=email,
            id=id,
            last_seen_at=last_seen_at,
            owned_suite_count=owned_suite_count,
            role=role,
            shared_suite_count=shared_suite_count,
        )

        admin_user_read.additional_properties = d
        return admin_user_read

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
