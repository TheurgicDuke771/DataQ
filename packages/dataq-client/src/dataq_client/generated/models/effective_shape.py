from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.effective_shape_source import EffectiveShapeSource

T = TypeVar("T", bound="EffectiveShape")


@_attrs_define
class EffectiveShape:
    """
    Attributes:
        min_points (int):
        seasonality (bool):
        source (EffectiveShapeSource):
        window (int):
    """

    min_points: int
    seasonality: bool
    source: EffectiveShapeSource
    window: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        min_points = self.min_points

        seasonality = self.seasonality

        source = self.source.value

        window = self.window

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "min_points": min_points,
                "seasonality": seasonality,
                "source": source,
                "window": window,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        min_points = d.pop("min_points")

        seasonality = d.pop("seasonality")

        source = EffectiveShapeSource(d.pop("source"))

        window = d.pop("window")

        effective_shape = cls(
            min_points=min_points,
            seasonality=seasonality,
            source=source,
            window=window,
        )

        effective_shape.additional_properties = d
        return effective_shape

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
