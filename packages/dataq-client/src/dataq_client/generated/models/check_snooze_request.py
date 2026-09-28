from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

T = TypeVar("T", bound="CheckSnoozeRequest")


@_attrs_define
class CheckSnoozeRequest:
    """
    Attributes:
        hours (float): Mute the check's alerts for this many hours
    """

    hours: float

    def to_dict(self) -> dict[str, Any]:
        hours = self.hours

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "hours": hours,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        hours = d.pop("hours")

        check_snooze_request = cls(
            hours=hours,
        )

        return check_snooze_request
