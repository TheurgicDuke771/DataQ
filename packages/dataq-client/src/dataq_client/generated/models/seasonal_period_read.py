from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.seasonal_period_read_kind import SeasonalPeriodReadKind

T = TypeVar("T", bound="SeasonalPeriodRead")


@_attrs_define
class SeasonalPeriodRead:
    """
    Attributes:
        attached_checks (int):
        created_at (datetime.datetime):
        end_date (datetime.date | None):
        end_day (int | None):
        end_month (int | None):
        id (UUID):
        kind (SeasonalPeriodReadKind):
        name (str):
        note (None | str):
        series (None | str):
        start_date (datetime.date | None):
        start_day (int | None):
        start_month (int | None):
        updated_at (datetime.datetime):
    """

    attached_checks: int
    created_at: datetime.datetime
    end_date: datetime.date | None
    end_day: int | None
    end_month: int | None
    id: UUID
    kind: SeasonalPeriodReadKind
    name: str
    note: None | str
    series: None | str
    start_date: datetime.date | None
    start_day: int | None
    start_month: int | None
    updated_at: datetime.datetime
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        attached_checks = self.attached_checks

        created_at = self.created_at.isoformat()

        end_date: None | str
        if isinstance(self.end_date, datetime.date):
            end_date = self.end_date.isoformat()
        else:
            end_date = self.end_date

        end_day: int | None
        end_day = self.end_day

        end_month: int | None
        end_month = self.end_month

        id = str(self.id)

        kind = self.kind.value

        name = self.name

        note: None | str
        note = self.note

        series: None | str
        series = self.series

        start_date: None | str
        if isinstance(self.start_date, datetime.date):
            start_date = self.start_date.isoformat()
        else:
            start_date = self.start_date

        start_day: int | None
        start_day = self.start_day

        start_month: int | None
        start_month = self.start_month

        updated_at = self.updated_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "attached_checks": attached_checks,
                "created_at": created_at,
                "end_date": end_date,
                "end_day": end_day,
                "end_month": end_month,
                "id": id,
                "kind": kind,
                "name": name,
                "note": note,
                "series": series,
                "start_date": start_date,
                "start_day": start_day,
                "start_month": start_month,
                "updated_at": updated_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        attached_checks = d.pop("attached_checks")

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        def _parse_end_date(data: object) -> datetime.date | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                end_date_type_0 = datetime.date.fromisoformat(data)

                return end_date_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.date | None, data)

        end_date = _parse_end_date(d.pop("end_date"))

        def _parse_end_day(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        end_day = _parse_end_day(d.pop("end_day"))

        def _parse_end_month(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        end_month = _parse_end_month(d.pop("end_month"))

        id = UUID(d.pop("id"))

        kind = SeasonalPeriodReadKind(d.pop("kind"))

        name = d.pop("name")

        def _parse_note(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        note = _parse_note(d.pop("note"))

        def _parse_series(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        series = _parse_series(d.pop("series"))

        def _parse_start_date(data: object) -> datetime.date | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                start_date_type_0 = datetime.date.fromisoformat(data)

                return start_date_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.date | None, data)

        start_date = _parse_start_date(d.pop("start_date"))

        def _parse_start_day(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        start_day = _parse_start_day(d.pop("start_day"))

        def _parse_start_month(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        start_month = _parse_start_month(d.pop("start_month"))

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        seasonal_period_read = cls(
            attached_checks=attached_checks,
            created_at=created_at,
            end_date=end_date,
            end_day=end_day,
            end_month=end_month,
            id=id,
            kind=kind,
            name=name,
            note=note,
            series=series,
            start_date=start_date,
            start_day=start_day,
            start_month=start_month,
            updated_at=updated_at,
        )

        seasonal_period_read.additional_properties = d
        return seasonal_period_read

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
