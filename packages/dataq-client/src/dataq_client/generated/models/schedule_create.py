from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="ScheduleCreate")


@_attrs_define
class ScheduleCreate:
    """
    Attributes:
        cron (str):
        suite_id (UUID):
        enabled (bool | Unset):  Default: True.
        timezone (str | Unset):  Default: 'UTC'.
    """

    cron: str
    suite_id: UUID
    enabled: bool | Unset = True
    timezone: str | Unset = "UTC"

    def to_dict(self) -> dict[str, Any]:
        cron = self.cron

        suite_id = str(self.suite_id)

        enabled = self.enabled

        timezone = self.timezone

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "cron": cron,
                "suite_id": suite_id,
            }
        )
        if enabled is not UNSET:
            field_dict["enabled"] = enabled
        if timezone is not UNSET:
            field_dict["timezone"] = timezone

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        cron = d.pop("cron")

        suite_id = UUID(d.pop("suite_id"))

        enabled = d.pop("enabled", UNSET)

        timezone = d.pop("timezone", UNSET)

        schedule_create = cls(
            cron=cron,
            suite_id=suite_id,
            enabled=enabled,
            timezone=timezone,
        )

        return schedule_create
