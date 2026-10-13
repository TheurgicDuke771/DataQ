from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

T = TypeVar("T", bound="BaselineShapeWrite")


@_attrs_define
class BaselineShapeWrite:
    """How much history a baseline compares against. All three are set together.

    Attributes:
        min_points (int):
        seasonality (bool):
        window (int):
    """

    min_points: int
    seasonality: bool
    window: int

    def to_dict(self) -> dict[str, Any]:
        min_points = self.min_points

        seasonality = self.seasonality

        window = self.window

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "min_points": min_points,
                "seasonality": seasonality,
                "window": window,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        min_points = d.pop("min_points")

        seasonality = d.pop("seasonality")

        window = d.pop("window")

        baseline_shape_write = cls(
            min_points=min_points,
            seasonality=seasonality,
            window=window,
        )

        return baseline_shape_write
