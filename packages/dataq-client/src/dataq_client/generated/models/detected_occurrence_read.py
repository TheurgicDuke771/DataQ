from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="DetectedOccurrenceRead")


@_attrs_define
class DetectedOccurrenceRead:
    """
    Attributes:
        backfilled_periods (int):
        end (datetime.date):
        peak_ratio (float):
        periods (int):
        start (datetime.date):
        year (int):
    """

    backfilled_periods: int
    end: datetime.date
    peak_ratio: float
    periods: int
    start: datetime.date
    year: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        backfilled_periods = self.backfilled_periods

        end = self.end.isoformat()

        peak_ratio = self.peak_ratio

        periods = self.periods

        start = self.start.isoformat()

        year = self.year

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "backfilled_periods": backfilled_periods,
                "end": end,
                "peak_ratio": peak_ratio,
                "periods": periods,
                "start": start,
                "year": year,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        backfilled_periods = d.pop("backfilled_periods")

        end = datetime.date.fromisoformat(d.pop("end"))

        peak_ratio = d.pop("peak_ratio")

        periods = d.pop("periods")

        start = datetime.date.fromisoformat(d.pop("start"))

        year = d.pop("year")

        detected_occurrence_read = cls(
            backfilled_periods=backfilled_periods,
            end=end,
            peak_ratio=peak_ratio,
            periods=periods,
            start=start,
            year=year,
        )

        detected_occurrence_read.additional_properties = d
        return detected_occurrence_read

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
