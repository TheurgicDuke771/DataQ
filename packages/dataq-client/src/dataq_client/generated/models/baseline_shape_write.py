from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="BaselineShapeWrite")


@_attrs_define
class BaselineShapeWrite:
    """How much history a baseline compares against. `window`, `min_points` and
    `seasonality` are set together; `yearly` (compare with the same time in earlier years,
    on the row-count and freshness baselines only) is off when left out, so a level that sets
    this group without it switches it off for its tables.

        Attributes:
            min_points (int):
            seasonality (bool):
            window (int):
            yearly (bool | Unset):  Default: False.
    """

    min_points: int
    seasonality: bool
    window: int
    yearly: bool | Unset = False

    def to_dict(self) -> dict[str, Any]:
        min_points = self.min_points

        seasonality = self.seasonality

        window = self.window

        yearly = self.yearly

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "min_points": min_points,
                "seasonality": seasonality,
                "window": window,
            }
        )
        if yearly is not UNSET:
            field_dict["yearly"] = yearly

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        min_points = d.pop("min_points")

        seasonality = d.pop("seasonality")

        window = d.pop("window")

        yearly = d.pop("yearly", UNSET)

        baseline_shape_write = cls(
            min_points=min_points,
            seasonality=seasonality,
            window=window,
            yearly=yearly,
        )

        return baseline_shape_write
