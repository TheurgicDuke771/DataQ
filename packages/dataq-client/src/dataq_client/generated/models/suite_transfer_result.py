from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="SuiteTransferResult")


@_attrs_define
class SuiteTransferResult:
    """
    Attributes:
        new_owner_id (UUID):
        previous_owner_id (None | UUID):
        previous_owner_permission (None | str):
        suite_id (UUID):
    """

    new_owner_id: UUID
    previous_owner_id: None | UUID
    previous_owner_permission: None | str
    suite_id: UUID
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        new_owner_id = str(self.new_owner_id)

        previous_owner_id: None | str
        if isinstance(self.previous_owner_id, UUID):
            previous_owner_id = str(self.previous_owner_id)
        else:
            previous_owner_id = self.previous_owner_id

        previous_owner_permission: None | str
        previous_owner_permission = self.previous_owner_permission

        suite_id = str(self.suite_id)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "new_owner_id": new_owner_id,
                "previous_owner_id": previous_owner_id,
                "previous_owner_permission": previous_owner_permission,
                "suite_id": suite_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        new_owner_id = UUID(d.pop("new_owner_id"))

        def _parse_previous_owner_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                previous_owner_id_type_0 = UUID(data)

                return previous_owner_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        previous_owner_id = _parse_previous_owner_id(d.pop("previous_owner_id"))

        def _parse_previous_owner_permission(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        previous_owner_permission = _parse_previous_owner_permission(
            d.pop("previous_owner_permission")
        )

        suite_id = UUID(d.pop("suite_id"))

        suite_transfer_result = cls(
            new_owner_id=new_owner_id,
            previous_owner_id=previous_owner_id,
            previous_owner_permission=previous_owner_permission,
            suite_id=suite_id,
        )

        suite_transfer_result.additional_properties = d
        return suite_transfer_result

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
