from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="AdminSuiteRead")


@_attrs_define
class AdminSuiteRead:
    """
    Attributes:
        check_count (int):
        connection_name (str):
        connection_type (str):
        created_at (datetime.datetime):
        env (str):
        id (UUID):
        name (str):
        owner_email (None | str):
        owner_id (None | UUID):
        owner_name (None | str):
        share_count (int):
        updated_at (datetime.datetime):
    """

    check_count: int
    connection_name: str
    connection_type: str
    created_at: datetime.datetime
    env: str
    id: UUID
    name: str
    owner_email: None | str
    owner_id: None | UUID
    owner_name: None | str
    share_count: int
    updated_at: datetime.datetime
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        check_count = self.check_count

        connection_name = self.connection_name

        connection_type = self.connection_type

        created_at = self.created_at.isoformat()

        env = self.env

        id = str(self.id)

        name = self.name

        owner_email: None | str
        owner_email = self.owner_email

        owner_id: None | str
        if isinstance(self.owner_id, UUID):
            owner_id = str(self.owner_id)
        else:
            owner_id = self.owner_id

        owner_name: None | str
        owner_name = self.owner_name

        share_count = self.share_count

        updated_at = self.updated_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "check_count": check_count,
                "connection_name": connection_name,
                "connection_type": connection_type,
                "created_at": created_at,
                "env": env,
                "id": id,
                "name": name,
                "owner_email": owner_email,
                "owner_id": owner_id,
                "owner_name": owner_name,
                "share_count": share_count,
                "updated_at": updated_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        check_count = d.pop("check_count")

        connection_name = d.pop("connection_name")

        connection_type = d.pop("connection_type")

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        env = d.pop("env")

        id = UUID(d.pop("id"))

        name = d.pop("name")

        def _parse_owner_email(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        owner_email = _parse_owner_email(d.pop("owner_email"))

        def _parse_owner_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                owner_id_type_0 = UUID(data)

                return owner_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        owner_id = _parse_owner_id(d.pop("owner_id"))

        def _parse_owner_name(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        owner_name = _parse_owner_name(d.pop("owner_name"))

        share_count = d.pop("share_count")

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        admin_suite_read = cls(
            check_count=check_count,
            connection_name=connection_name,
            connection_type=connection_type,
            created_at=created_at,
            env=env,
            id=id,
            name=name,
            owner_email=owner_email,
            owner_id=owner_id,
            owner_name=owner_name,
            share_count=share_count,
            updated_at=updated_at,
        )

        admin_suite_read.additional_properties = d
        return admin_suite_read

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
