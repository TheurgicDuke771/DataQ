from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="MeResponse")


@_attrs_define
class MeResponse:
    """
    Attributes:
        aad_object_id (None | str):
        display_name (None | str):
        email (str):
        id (UUID):
        last_seen_at (datetime.datetime | None):
        is_workspace_admin (bool | Unset):  Default: False.
        role (str | Unset):  Default: 'member'.
    """

    aad_object_id: None | str
    display_name: None | str
    email: str
    id: UUID
    last_seen_at: datetime.datetime | None
    is_workspace_admin: bool | Unset = False
    role: str | Unset = "member"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        aad_object_id: None | str
        aad_object_id = self.aad_object_id

        display_name: None | str
        display_name = self.display_name

        email = self.email

        id = str(self.id)

        last_seen_at: None | str
        if isinstance(self.last_seen_at, datetime.datetime):
            last_seen_at = self.last_seen_at.isoformat()
        else:
            last_seen_at = self.last_seen_at

        is_workspace_admin = self.is_workspace_admin

        role = self.role

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "aad_object_id": aad_object_id,
                "display_name": display_name,
                "email": email,
                "id": id,
                "last_seen_at": last_seen_at,
            }
        )
        if is_workspace_admin is not UNSET:
            field_dict["is_workspace_admin"] = is_workspace_admin
        if role is not UNSET:
            field_dict["role"] = role

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

        def _parse_aad_object_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        aad_object_id = _parse_aad_object_id(d.pop("aad_object_id"))

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

        is_workspace_admin = d.pop("is_workspace_admin", UNSET)

        role = d.pop("role", UNSET)

        me_response = cls(
            aad_object_id=aad_object_id,
            display_name=display_name,
            email=email,
            id=id,
            last_seen_at=last_seen_at,
            is_workspace_admin=is_workspace_admin,
            role=role,
        )

        me_response.additional_properties = d
        return me_response

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
