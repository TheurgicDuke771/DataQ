from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.telemetry_settings_read_sink import TelemetrySettingsReadSink

if TYPE_CHECKING:
    from ..models.telemetry_event_read import TelemetryEventRead


T = TypeVar("T", bound="TelemetrySettingsRead")


@_attrs_define
class TelemetrySettingsRead:
    """`enabled` is the EFFECTIVE state: the stored opt-in AND an operator-configured sink.
    `stored` is only what the toggle last wrote; with `sink = off` it sends nothing, and the
    page says so rather than reporting a state the emitter ignores. `events` is the whole
    vocabulary — the only values that can ever leave the deployment.

        Attributes:
            enabled (bool):
            events (list[TelemetryEventRead]):
            sink (TelemetrySettingsReadSink):
            sink_ready (bool):
            stored (bool):
            updated_at (datetime.datetime | None):
            updated_by (None | str):
    """

    enabled: bool
    events: list[TelemetryEventRead]
    sink: TelemetrySettingsReadSink
    sink_ready: bool
    stored: bool
    updated_at: datetime.datetime | None
    updated_by: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        enabled = self.enabled

        events = []
        for events_item_data in self.events:
            events_item = events_item_data.to_dict()
            events.append(events_item)

        sink = self.sink.value

        sink_ready = self.sink_ready

        stored = self.stored

        updated_at: None | str
        if isinstance(self.updated_at, datetime.datetime):
            updated_at = self.updated_at.isoformat()
        else:
            updated_at = self.updated_at

        updated_by: None | str
        updated_by = self.updated_by

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "enabled": enabled,
                "events": events,
                "sink": sink,
                "sink_ready": sink_ready,
                "stored": stored,
                "updated_at": updated_at,
                "updated_by": updated_by,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.telemetry_event_read import TelemetryEventRead

        d = dict(src_dict)
        enabled = d.pop("enabled")

        events = []
        _events = d.pop("events")
        for events_item_data in _events:
            events_item = TelemetryEventRead.from_dict(events_item_data)

            events.append(events_item)

        sink = TelemetrySettingsReadSink(d.pop("sink"))

        sink_ready = d.pop("sink_ready")

        stored = d.pop("stored")

        def _parse_updated_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                updated_at_type_0 = datetime.datetime.fromisoformat(data)

                return updated_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        updated_at = _parse_updated_at(d.pop("updated_at"))

        def _parse_updated_by(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        updated_by = _parse_updated_by(d.pop("updated_by"))

        telemetry_settings_read = cls(
            enabled=enabled,
            events=events,
            sink=sink,
            sink_ready=sink_ready,
            stored=stored,
            updated_at=updated_at,
            updated_by=updated_by,
        )

        telemetry_settings_read.additional_properties = d
        return telemetry_settings_read

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
