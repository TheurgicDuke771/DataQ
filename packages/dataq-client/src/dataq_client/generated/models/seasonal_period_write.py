from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..models.seasonal_period_write_kind import SeasonalPeriodWriteKind
from ..types import UNSET, Unset

T = TypeVar("T", bound="SeasonalPeriodWrite")


@_attrs_define
class SeasonalPeriodWrite:
    """A recurring entry sets the four month/day fields (the range may wrap the year end:
    20 December to 5 January; 29 February is taken as 28 February in other years). A dated
    entry sets `start_date` and `end_date`, and optionally a `series`: dated entries sharing
    a series are occurrences of one period across years, which is how a moving sale finds
    its previous occurrence.

        Attributes:
            kind (SeasonalPeriodWriteKind):
            name (str):
            end_date (datetime.date | None | Unset):
            end_day (int | None | Unset):
            end_month (int | None | Unset):
            note (None | str | Unset):
            series (None | str | Unset):
            start_date (datetime.date | None | Unset):
            start_day (int | None | Unset):
            start_month (int | None | Unset):
    """

    kind: SeasonalPeriodWriteKind
    name: str
    end_date: datetime.date | None | Unset = UNSET
    end_day: int | None | Unset = UNSET
    end_month: int | None | Unset = UNSET
    note: None | str | Unset = UNSET
    series: None | str | Unset = UNSET
    start_date: datetime.date | None | Unset = UNSET
    start_day: int | None | Unset = UNSET
    start_month: int | None | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind.value

        name = self.name

        end_date: None | str | Unset
        if isinstance(self.end_date, Unset):
            end_date = UNSET
        elif isinstance(self.end_date, datetime.date):
            end_date = self.end_date.isoformat()
        else:
            end_date = self.end_date

        end_day: int | None | Unset
        if isinstance(self.end_day, Unset):
            end_day = UNSET
        else:
            end_day = self.end_day

        end_month: int | None | Unset
        if isinstance(self.end_month, Unset):
            end_month = UNSET
        else:
            end_month = self.end_month

        note: None | str | Unset
        if isinstance(self.note, Unset):
            note = UNSET
        else:
            note = self.note

        series: None | str | Unset
        if isinstance(self.series, Unset):
            series = UNSET
        else:
            series = self.series

        start_date: None | str | Unset
        if isinstance(self.start_date, Unset):
            start_date = UNSET
        elif isinstance(self.start_date, datetime.date):
            start_date = self.start_date.isoformat()
        else:
            start_date = self.start_date

        start_day: int | None | Unset
        if isinstance(self.start_day, Unset):
            start_day = UNSET
        else:
            start_day = self.start_day

        start_month: int | None | Unset
        if isinstance(self.start_month, Unset):
            start_month = UNSET
        else:
            start_month = self.start_month

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "kind": kind,
                "name": name,
            }
        )
        if end_date is not UNSET:
            field_dict["end_date"] = end_date
        if end_day is not UNSET:
            field_dict["end_day"] = end_day
        if end_month is not UNSET:
            field_dict["end_month"] = end_month
        if note is not UNSET:
            field_dict["note"] = note
        if series is not UNSET:
            field_dict["series"] = series
        if start_date is not UNSET:
            field_dict["start_date"] = start_date
        if start_day is not UNSET:
            field_dict["start_day"] = start_day
        if start_month is not UNSET:
            field_dict["start_month"] = start_month

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        kind = SeasonalPeriodWriteKind(d.pop("kind"))

        name = d.pop("name")

        def _parse_end_date(data: object) -> datetime.date | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                end_date_type_0 = datetime.date.fromisoformat(data)

                return end_date_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.date | None | Unset, data)

        end_date = _parse_end_date(d.pop("end_date", UNSET))

        def _parse_end_day(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        end_day = _parse_end_day(d.pop("end_day", UNSET))

        def _parse_end_month(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        end_month = _parse_end_month(d.pop("end_month", UNSET))

        def _parse_note(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        note = _parse_note(d.pop("note", UNSET))

        def _parse_series(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        series = _parse_series(d.pop("series", UNSET))

        def _parse_start_date(data: object) -> datetime.date | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                start_date_type_0 = datetime.date.fromisoformat(data)

                return start_date_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.date | None | Unset, data)

        start_date = _parse_start_date(d.pop("start_date", UNSET))

        def _parse_start_day(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        start_day = _parse_start_day(d.pop("start_day", UNSET))

        def _parse_start_month(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        start_month = _parse_start_month(d.pop("start_month", UNSET))

        seasonal_period_write = cls(
            kind=kind,
            name=name,
            end_date=end_date,
            end_day=end_day,
            end_month=end_month,
            note=note,
            series=series,
            start_date=start_date,
            start_day=start_day,
            start_month=start_month,
        )

        return seasonal_period_write
