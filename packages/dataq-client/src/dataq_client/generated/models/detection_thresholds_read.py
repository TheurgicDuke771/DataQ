from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.detection_thresholds_read_level_type_0 import DetectionThresholdsReadLevelType0

T = TypeVar("T", bound="DetectionThresholdsRead")


@_attrs_define
class DetectionThresholdsRead:
    """
    Attributes:
        level (DetectionThresholdsReadLevelType0 | None):
        min_consecutive (int):
        ratio (float):
        same_time_days (int):
    """

    level: DetectionThresholdsReadLevelType0 | None
    min_consecutive: int
    ratio: float
    same_time_days: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        level: None | str
        if isinstance(self.level, DetectionThresholdsReadLevelType0):
            level = self.level.value
        else:
            level = self.level

        min_consecutive = self.min_consecutive

        ratio = self.ratio

        same_time_days = self.same_time_days

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "level": level,
                "min_consecutive": min_consecutive,
                "ratio": ratio,
                "same_time_days": same_time_days,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

        def _parse_level(data: object) -> DetectionThresholdsReadLevelType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                level_type_0 = DetectionThresholdsReadLevelType0(data)

                return level_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(DetectionThresholdsReadLevelType0 | None, data)

        level = _parse_level(d.pop("level"))

        min_consecutive = d.pop("min_consecutive")

        ratio = d.pop("ratio")

        same_time_days = d.pop("same_time_days")

        detection_thresholds_read = cls(
            level=level,
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
