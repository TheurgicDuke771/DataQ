from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.member_read_source import MemberReadSource
from ..models.member_read_status import MemberReadStatus

T = TypeVar("T", bound="MemberRead")


@_attrs_define
class MemberRead:
    """
    Attributes:
        created_at (datetime.datetime):
        email (str):
        env_listed (bool):
        id (UUID):
        initial_role (str):
        invited_by_email (None | str):
        removable (bool):
        source (MemberReadSource):
        status (MemberReadStatus):
        stored_role (None | str):
        user_id (None | UUID):
    """

    created_at: datetime.datetime
    email: str
    env_listed: bool
    id: UUID
    initial_role: str
    invited_by_email: None | str
    removable: bool
    source: MemberReadSource
    status: MemberReadStatus
    stored_role: None | str
    user_id: None | UUID
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        email = self.email

        env_listed = self.env_listed

        id = str(self.id)

        initial_role = self.initial_role

        invited_by_email: None | str
        invited_by_email = self.invited_by_email

        removable = self.removable

        source = self.source.value

        status = self.status.value

        stored_role: None | str
        stored_role = self.stored_role

        user_id: None | str
        if isinstance(self.user_id, UUID):
            user_id = str(self.user_id)
        else:
            user_id = self.user_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "email": email,
                "env_listed": env_listed,
                "id": id,
                "initial_role": initial_role,
                "invited_by_email": invited_by_email,
                "removable": removable,
                "source": source,
                "status": status,
                "stored_role": stored_role,
                "user_id": user_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        email = d.pop("email")

        env_listed = d.pop("env_listed")

        id = UUID(d.pop("id"))

        initial_role = d.pop("initial_role")

        def _parse_invited_by_email(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        invited_by_email = _parse_invited_by_email(d.pop("invited_by_email"))

        removable = d.pop("removable")

        source = MemberReadSource(d.pop("source"))

        status = MemberReadStatus(d.pop("status"))

        def _parse_stored_role(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        stored_role = _parse_stored_role(d.pop("stored_role"))

        def _parse_user_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                user_id_type_0 = UUID(data)

                return user_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        user_id = _parse_user_id(d.pop("user_id"))

        member_read = cls(
            created_at=created_at,
            email=email,
            env_listed=env_listed,
            id=id,
            initial_role=initial_role,
            invited_by_email=invited_by_email,
            removable=removable,
            source=source,
            status=status,
            stored_role=stored_role,
            user_id=user_id,
        )

        member_read.additional_properties = d
        return member_read

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
