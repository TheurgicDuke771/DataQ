from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.top_value import TopValue


T = TypeVar("T", bound="ColumnProfileRead")


@_attrs_define
class ColumnProfileRead:
    """
    Attributes:
        column (str):
        distinct_count (int | None):
        max_value (Any | None):
        min_value (Any | None):
        null_count (int):
        null_fraction (float):
        top_values (list[TopValue]):
    """

    column: str
    distinct_count: int | None
    max_value: Any | None
    min_value: Any | None
    null_count: int
    null_fraction: float
    top_values: list[TopValue]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        column = self.column

        distinct_count: int | None
        distinct_count = self.distinct_count

        max_value: Any | None
        max_value = self.max_value

        min_value: Any | None
        min_value = self.min_value

        null_count = self.null_count

        null_fraction = self.null_fraction

        top_values = []
        for top_values_item_data in self.top_values:
            top_values_item = top_values_item_data.to_dict()
            top_values.append(top_values_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "column": column,
                "distinct_count": distinct_count,
                "max_value": max_value,
                "min_value": min_value,
                "null_count": null_count,
                "null_fraction": null_fraction,
                "top_values": top_values,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.top_value import TopValue

        d = dict(src_dict)
        column = d.pop("column")

        def _parse_distinct_count(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        distinct_count = _parse_distinct_count(d.pop("distinct_count"))

        def _parse_max_value(data: object) -> Any | None:
            if data is None:
                return data
            return cast(Any | None, data)

        max_value = _parse_max_value(d.pop("max_value"))

        def _parse_min_value(data: object) -> Any | None:
            if data is None:
                return data
            return cast(Any | None, data)

        min_value = _parse_min_value(d.pop("min_value"))

        null_count = d.pop("null_count")

        null_fraction = d.pop("null_fraction")

        top_values = []
        _top_values = d.pop("top_values")
        for top_values_item_data in _top_values:
            top_values_item = TopValue.from_dict(top_values_item_data)

            top_values.append(top_values_item)

        column_profile_read = cls(
            column=column,
            distinct_count=distinct_count,
            max_value=max_value,
            min_value=min_value,
            null_count=null_count,
            null_fraction=null_fraction,
            top_values=top_values,
        )

        column_profile_read.additional_properties = d
        return column_profile_read

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
