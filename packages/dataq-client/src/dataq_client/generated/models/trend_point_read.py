from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="TrendPointRead")


@_attrs_define
class TrendPointRead:
    """
    Attributes:
        day (datetime.date):
        failed (int):
        succeeded (int):
    """

    day: datetime.date
    failed: int
    succeeded: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        day = self.day.isoformat()

        failed = self.failed

        succeeded = self.succeeded

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "day": day,
                "failed": failed,
                "succeeded": succeeded,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        day = datetime.date.fromisoformat(d.pop("day"))

        failed = d.pop("failed")

        succeeded = d.pop("succeeded")

        trend_point_read = cls(
            day=day,
            failed=failed,
            succeeded=succeeded,
        )

        trend_point_read.additional_properties = d
        return trend_point_read

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
