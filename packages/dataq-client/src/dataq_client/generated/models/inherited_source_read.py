from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="InheritedSourceRead")


@_attrs_define
class InheritedSourceRead:
    """
    Attributes:
        asset_id (UUID):
        asset_name (str):
        column (str):
    """

    asset_id: UUID
    asset_name: str
    column: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        asset_id = str(self.asset_id)

        asset_name = self.asset_name

        column = self.column

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "asset_id": asset_id,
                "asset_name": asset_name,
                "column": column,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        asset_id = UUID(d.pop("asset_id"))

        asset_name = d.pop("asset_name")

        column = d.pop("column")

        inherited_source_read = cls(
            asset_id=asset_id,
            asset_name=asset_name,
            column=column,
        )

        inherited_source_read.additional_properties = d
        return inherited_source_read

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
