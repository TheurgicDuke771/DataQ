from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.audit_event_read import AuditEventRead


T = TypeVar("T", bound="AuditEventPage")


@_attrs_define
class AuditEventPage:
    """A page of events plus the fields needed to interpret it honestly.

    Attributes:
        events (list[AuditEventRead]):
        retained_since (datetime.datetime | None):
        retention_days (int):
        total (int):
        truncated (bool):
    """

    events: list[AuditEventRead]
    retained_since: datetime.datetime | None
    retention_days: int
    total: int
    truncated: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        events = []
        for events_item_data in self.events:
            events_item = events_item_data.to_dict()
            events.append(events_item)

        retained_since: None | str
        if isinstance(self.retained_since, datetime.datetime):
            retained_since = self.retained_since.isoformat()
        else:
            retained_since = self.retained_since

        retention_days = self.retention_days

        total = self.total

        truncated = self.truncated

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "events": events,
                "retained_since": retained_since,
                "retention_days": retention_days,
                "total": total,
                "truncated": truncated,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.audit_event_read import AuditEventRead

        d = dict(src_dict)
        events = []
        _events = d.pop("events")
        for events_item_data in _events:
            events_item = AuditEventRead.from_dict(events_item_data)

            events.append(events_item)

        def _parse_retained_since(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                retained_since_type_0 = datetime.datetime.fromisoformat(data)

                return retained_since_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        retained_since = _parse_retained_since(d.pop("retained_since"))

        retention_days = d.pop("retention_days")

        total = d.pop("total")

        truncated = d.pop("truncated")

        audit_event_page = cls(
            events=events,
            retained_since=retained_since,
            retention_days=retention_days,
            total=total,
            truncated=truncated,
        )

        audit_event_page.additional_properties = d
        return audit_event_page

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
