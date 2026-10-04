from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from typing_extensions import Self

T = TypeVar("T", bound="BulkSnoozeRequest")


@_attrs_define
class BulkSnoozeRequest:
    """
    Attributes:
        check_ids (list[UUID]):
        hours (float): Mute each check's alerts for this many hours
    """

    check_ids: list[UUID]
    hours: float

    def to_dict(self) -> dict[str, Any]:
        check_ids = []
        for check_ids_item_data in self.check_ids:
            check_ids_item = str(check_ids_item_data)
            check_ids.append(check_ids_item)

        hours = self.hours

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "check_ids": check_ids,
                "hours": hours,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        check_ids = []
        _check_ids = d.pop("check_ids")
        for check_ids_item_data in _check_ids:
            check_ids_item = UUID(check_ids_item_data)

            check_ids.append(check_ids_item)

        hours = d.pop("hours")

        bulk_snooze_request = cls(
            check_ids=check_ids,
            hours=hours,
        )

        return bulk_snooze_request
