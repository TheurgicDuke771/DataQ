from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="SuiteTransfer")


@_attrs_define
class SuiteTransfer:
    """`POST /admin/suites/{id}/transfer` body.

    Attributes:
        new_owner_user_id (UUID):
        keep_previous_owner_access (bool | Unset):  Default: True.
    """

    new_owner_user_id: UUID
    keep_previous_owner_access: bool | Unset = True

    def to_dict(self) -> dict[str, Any]:
        new_owner_user_id = str(self.new_owner_user_id)

        keep_previous_owner_access = self.keep_previous_owner_access

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "new_owner_user_id": new_owner_user_id,
            }
        )
        if keep_previous_owner_access is not UNSET:
            field_dict["keep_previous_owner_access"] = keep_previous_owner_access

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        new_owner_user_id = UUID(d.pop("new_owner_user_id"))

        keep_previous_owner_access = d.pop("keep_previous_owner_access", UNSET)

        suite_transfer = cls(
            new_owner_user_id=new_owner_user_id,
            keep_previous_owner_access=keep_previous_owner_access,
        )

        return suite_transfer
