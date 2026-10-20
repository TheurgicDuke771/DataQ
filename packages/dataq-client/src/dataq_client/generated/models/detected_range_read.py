from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="DetectedRangeRead")


@_attrs_define
class DetectedRangeRead:
    """
    Attributes:
        end_day (int):
        end_month (int):
        start_day (int):
        start_month (int):
    """

    end_day: int
    end_month: int
    start_day: int
    start_month: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        end_day = self.end_day

        end_month = self.end_month

        start_day = self.start_day

        start_month = self.start_month

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "end_day": end_day,
                "end_month": end_month,
                "start_day": start_day,
                "start_month": start_month,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        end_day = d.pop("end_day")

        end_month = d.pop("end_month")

        start_day = d.pop("start_day")

        start_month = d.pop("start_month")

        detected_range_read = cls(
            end_day=end_day,
            end_month=end_month,
            start_day=start_day,
            start_month=start_month,
        )

        detected_range_read.additional_properties = d
        return detected_range_read

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
