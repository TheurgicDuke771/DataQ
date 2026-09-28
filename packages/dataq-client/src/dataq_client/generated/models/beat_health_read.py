from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.beat_health_read_status import BeatHealthReadStatus

T = TypeVar("T", bound="BeatHealthRead")


@_attrs_define
class BeatHealthRead:
    """The beat→broker→worker heartbeat. `status="not_monitored"` means the heartbeat
    task has never recorded a tick — not the same as `alive`.

        Attributes:
            last_tick_at (datetime.datetime | None):
            status (BeatHealthReadStatus):
    """

    last_tick_at: datetime.datetime | None
    status: BeatHealthReadStatus
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        last_tick_at: None | str
        if isinstance(self.last_tick_at, datetime.datetime):
            last_tick_at = self.last_tick_at.isoformat()
        else:
            last_tick_at = self.last_tick_at

        status = self.status.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "last_tick_at": last_tick_at,
                "status": status,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

        def _parse_last_tick_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_tick_at_type_0 = datetime.datetime.fromisoformat(data)

                return last_tick_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        last_tick_at = _parse_last_tick_at(d.pop("last_tick_at"))

        status = BeatHealthReadStatus(d.pop("status"))

        beat_health_read = cls(
            last_tick_at=last_tick_at,
            status=status,
        )

        beat_health_read.additional_properties = d
        return beat_health_read

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
