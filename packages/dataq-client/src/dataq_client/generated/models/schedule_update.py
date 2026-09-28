from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="ScheduleUpdate")


@_attrs_define
class ScheduleUpdate:
    """Partial update — only the supplied fields change. `next_run_at` is
    recomputed by the service when the cadence changes or a paused schedule is
    re-enabled.

        Attributes:
            cron (None | str | Unset):
            enabled (bool | None | Unset):
            timezone (None | str | Unset):
    """

    cron: None | str | Unset = UNSET
    enabled: bool | None | Unset = UNSET
    timezone: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        cron: None | str | Unset
        if isinstance(self.cron, Unset):
            cron = UNSET
        else:
            cron = self.cron

        enabled: bool | None | Unset
        if isinstance(self.enabled, Unset):
            enabled = UNSET
        else:
            enabled = self.enabled

        timezone: None | str | Unset
        if isinstance(self.timezone, Unset):
            timezone = UNSET
        else:
            timezone = self.timezone

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if cron is not UNSET:
            field_dict["cron"] = cron
        if enabled is not UNSET:
            field_dict["enabled"] = enabled
        if timezone is not UNSET:
            field_dict["timezone"] = timezone

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

        def _parse_cron(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        cron = _parse_cron(d.pop("cron", UNSET))

        def _parse_enabled(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        enabled = _parse_enabled(d.pop("enabled", UNSET))

        def _parse_timezone(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        timezone = _parse_timezone(d.pop("timezone", UNSET))

        schedule_update = cls(
            cron=cron,
            enabled=enabled,
            timezone=timezone,
        )

        return schedule_update
