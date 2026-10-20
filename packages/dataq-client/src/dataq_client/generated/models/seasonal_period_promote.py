from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="SeasonalPeriodPromote")


@_attrs_define
class SeasonalPeriodPromote:
    """Promote a period detected in a check's history
    (`GET /suites/{suite_id}/checks/{check_id}/seasonal-periods/detected`) to the calendar.
    `start` is the detected period's `start` (its most recent season); the server reads the
    history again and refuses a start it no longer shows (404), or a season an entry on the
    calendar already covers (409, naming the entry to attach instead). A recurring period becomes a
    month/day range, a period seen once a dated entry (`series` applies only to that).
    `attach` also attaches the new entry to the check, which needs `yearly` on it.

        Attributes:
            check_id (UUID):
            name (str):
            start (datetime.date):
            suite_id (UUID):
            attach (bool | Unset):  Default: False.
            note (None | str | Unset):
            series (None | str | Unset):
    """

    check_id: UUID
    name: str
    start: datetime.date
    suite_id: UUID
    attach: bool | Unset = False
    note: None | str | Unset = UNSET
    series: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        check_id = str(self.check_id)

        name = self.name

        start = self.start.isoformat()

        suite_id = str(self.suite_id)

        attach = self.attach

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

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "check_id": check_id,
                "name": name,
                "start": start,
                "suite_id": suite_id,
            }
        )
        if attach is not UNSET:
            field_dict["attach"] = attach
        if note is not UNSET:
            field_dict["note"] = note
        if series is not UNSET:
            field_dict["series"] = series

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        check_id = UUID(d.pop("check_id"))

        name = d.pop("name")

        start = datetime.date.fromisoformat(d.pop("start"))

        suite_id = UUID(d.pop("suite_id"))

        attach = d.pop("attach", UNSET)

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

        seasonal_period_promote = cls(
            check_id=check_id,
            name=name,
            start=start,
            suite_id=suite_id,
            attach=attach,
            note=note,
            series=series,
        )

        return seasonal_period_promote
