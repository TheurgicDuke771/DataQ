from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="DetectionHistoryRead")


@_attrs_define
class DetectionHistoryRead:
    """
    Attributes:
        backfilled (int):
        evaluated (int):
        first (datetime.date | None):
        last (datetime.date | None):
        points (int):
        unit (None | str):
        years (float):
    """

    backfilled: int
    evaluated: int
    first: datetime.date | None
    last: datetime.date | None
    points: int
    unit: None | str
    years: float
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        backfilled = self.backfilled

        evaluated = self.evaluated

        first: None | str
        if isinstance(self.first, datetime.date):
            first = self.first.isoformat()
        else:
            first = self.first

        last: None | str
        if isinstance(self.last, datetime.date):
            last = self.last.isoformat()
        else:
            last = self.last

        points = self.points

        unit: None | str
        unit = self.unit

        years = self.years

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "backfilled": backfilled,
                "evaluated": evaluated,
                "first": first,
                "last": last,
                "points": points,
                "unit": unit,
                "years": years,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        backfilled = d.pop("backfilled")

        evaluated = d.pop("evaluated")

        def _parse_first(data: object) -> datetime.date | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                first_type_0 = datetime.date.fromisoformat(data)

                return first_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.date | None, data)

        first = _parse_first(d.pop("first"))

        def _parse_last(data: object) -> datetime.date | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_type_0 = datetime.date.fromisoformat(data)

                return last_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.date | None, data)

        last = _parse_last(d.pop("last"))

        points = d.pop("points")

        def _parse_unit(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        unit = _parse_unit(d.pop("unit"))

        years = d.pop("years")

        detection_history_read = cls(
            backfilled=backfilled,
            evaluated=evaluated,
            first=first,
            last=last,
            points=points,
            unit=unit,
            years=years,
        )

        detection_history_read.additional_properties = d
        return detection_history_read

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
