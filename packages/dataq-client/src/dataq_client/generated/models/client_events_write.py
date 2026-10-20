from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.client_event import ClientEvent


T = TypeVar("T", bound="ClientEventsWrite")


@_attrs_define
class ClientEventsWrite:
    """
    Attributes:
        events (list[ClientEvent]):
    """

    events: list[ClientEvent]

    def to_dict(self) -> dict[str, Any]:
        events = []
        for events_item_data in self.events:
            events_item = events_item_data.to_dict()
            events.append(events_item)

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "events": events,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.client_event import ClientEvent

        d = dict(src_dict)
        events = []
        _events = d.pop("events")
        for events_item_data in _events:
            events_item = ClientEvent.from_dict(events_item_data)

            events.append(events_item)

        client_events_write = cls(
            events=events,
        )

        return client_events_write
