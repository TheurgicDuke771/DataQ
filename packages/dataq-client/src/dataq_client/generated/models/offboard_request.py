from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="OffboardRequest")


@_attrs_define
class OffboardRequest:
    """`POST /admin/offboarding/{user_id}` body.

    Attributes:
        confirm_email (str):
        keep_previous_owner_access (bool | Unset):  Default: False.
        new_owner_user_id (None | Unset | UUID):
    """

    confirm_email: str
    keep_previous_owner_access: bool | Unset = False
    new_owner_user_id: None | Unset | UUID = UNSET

    def to_dict(self) -> dict[str, Any]:
        confirm_email = self.confirm_email

        keep_previous_owner_access = self.keep_previous_owner_access

        new_owner_user_id: None | str | Unset
        if isinstance(self.new_owner_user_id, Unset):
            new_owner_user_id = UNSET
        elif isinstance(self.new_owner_user_id, UUID):
            new_owner_user_id = str(self.new_owner_user_id)
        else:
            new_owner_user_id = self.new_owner_user_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "confirm_email": confirm_email,
            }
        )
        if keep_previous_owner_access is not UNSET:
            field_dict["keep_previous_owner_access"] = keep_previous_owner_access
        if new_owner_user_id is not UNSET:
            field_dict["new_owner_user_id"] = new_owner_user_id

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        confirm_email = d.pop("confirm_email")

        keep_previous_owner_access = d.pop("keep_previous_owner_access", UNSET)

        def _parse_new_owner_user_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                new_owner_user_id_type_0 = UUID(data)

                return new_owner_user_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | Unset | UUID, data)

        new_owner_user_id = _parse_new_owner_user_id(d.pop("new_owner_user_id", UNSET))

        offboard_request = cls(
            confirm_email=confirm_email,
            keep_previous_owner_access=keep_previous_owner_access,
            new_owner_user_id=new_owner_user_id,
        )

        return offboard_request
