from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="ColumnHopRead")


@_attrs_define
class ColumnHopRead:
    """
    Attributes:
        downstream_asset_id (UUID):
        downstream_column (str):
        upstream_asset_id (UUID):
        upstream_column (str):
    """

    downstream_asset_id: UUID
    downstream_column: str
    upstream_asset_id: UUID
    upstream_column: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        downstream_asset_id = str(self.downstream_asset_id)

        downstream_column = self.downstream_column

        upstream_asset_id = str(self.upstream_asset_id)

        upstream_column = self.upstream_column

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "downstream_asset_id": downstream_asset_id,
                "downstream_column": downstream_column,
                "upstream_asset_id": upstream_asset_id,
                "upstream_column": upstream_column,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        downstream_asset_id = UUID(d.pop("downstream_asset_id"))

        downstream_column = d.pop("downstream_column")

        upstream_asset_id = UUID(d.pop("upstream_asset_id"))

        upstream_column = d.pop("upstream_column")

        column_hop_read = cls(
            downstream_asset_id=downstream_asset_id,
            downstream_column=downstream_column,
            upstream_asset_id=upstream_asset_id,
            upstream_column=upstream_column,
        )

        column_hop_read.additional_properties = d
        return column_hop_read

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
