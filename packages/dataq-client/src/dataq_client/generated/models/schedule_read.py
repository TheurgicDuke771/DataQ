from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="ScheduleRead")


@_attrs_define
class ScheduleRead:
    """
    Attributes:
        cron (str):
        enabled (bool):
        id (UUID):
        last_run_at (datetime.datetime | None):
        next_run_at (datetime.datetime):
        suite_id (UUID):
        timezone (str):
    """

    cron: str
    enabled: bool
    id: UUID
    last_run_at: datetime.datetime | None
    next_run_at: datetime.datetime
    suite_id: UUID
    timezone: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        cron = self.cron

        enabled = self.enabled

        id = str(self.id)

        last_run_at: None | str
        if isinstance(self.last_run_at, datetime.datetime):
            last_run_at = self.last_run_at.isoformat()
        else:
            last_run_at = self.last_run_at

        next_run_at = self.next_run_at.isoformat()

        suite_id = str(self.suite_id)

        timezone = self.timezone

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "cron": cron,
                "enabled": enabled,
                "id": id,
                "last_run_at": last_run_at,
                "next_run_at": next_run_at,
                "suite_id": suite_id,
                "timezone": timezone,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        cron = d.pop("cron")

        enabled = d.pop("enabled")

        id = UUID(d.pop("id"))

        def _parse_last_run_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_run_at_type_0 = datetime.datetime.fromisoformat(data)

                return last_run_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        last_run_at = _parse_last_run_at(d.pop("last_run_at"))

        next_run_at = datetime.datetime.fromisoformat(d.pop("next_run_at"))

        suite_id = UUID(d.pop("suite_id"))

        timezone = d.pop("timezone")

        schedule_read = cls(
            cron=cron,
            enabled=enabled,
            id=id,
            last_run_at=last_run_at,
            next_run_at=next_run_at,
            suite_id=suite_id,
            timezone=timezone,
        )

        schedule_read.additional_properties = d
        return schedule_read

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
