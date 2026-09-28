from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="CoverageGapRead")


@_attrs_define
class CoverageGapRead:
    """A table edge on the walk that carries no column pairs — the column MAY cross it.

    Attributes:
        coverage (str):
        downstream_asset_id (UUID):
        upstream_asset_id (UUID):
    """

    coverage: str
    downstream_asset_id: UUID
    upstream_asset_id: UUID
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        coverage = self.coverage

        downstream_asset_id = str(self.downstream_asset_id)

        upstream_asset_id = str(self.upstream_asset_id)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "coverage": coverage,
                "downstream_asset_id": downstream_asset_id,
                "upstream_asset_id": upstream_asset_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        coverage = d.pop("coverage")

        downstream_asset_id = UUID(d.pop("downstream_asset_id"))

        upstream_asset_id = UUID(d.pop("upstream_asset_id"))

        coverage_gap_read = cls(
            coverage=coverage,
            downstream_asset_id=downstream_asset_id,
            upstream_asset_id=upstream_asset_id,
        )

        coverage_gap_read.additional_properties = d
        return coverage_gap_read

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
