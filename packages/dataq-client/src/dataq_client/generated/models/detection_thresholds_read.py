from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="DetectionThresholdsRead")


@_attrs_define
class DetectionThresholdsRead:
    """
    Attributes:
        min_consecutive (int):
        ratio (float):
        same_time_days (int):
    """

    min_consecutive: int
    ratio: float
    same_time_days: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        min_consecutive = self.min_consecutive

        ratio = self.ratio

        same_time_days = self.same_time_days

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "min_consecutive": min_consecutive,
                "ratio": ratio,
                "same_time_days": same_time_days,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        min_consecutive = d.pop("min_consecutive")

        ratio = d.pop("ratio")

        same_time_days = d.pop("same_time_days")

        detection_thresholds_read = cls(
            min_consecutive=min_consecutive,
            ratio=ratio,
            same_time_days=same_time_days,
        )

        detection_thresholds_read.additional_properties = d
        return detection_thresholds_read

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
