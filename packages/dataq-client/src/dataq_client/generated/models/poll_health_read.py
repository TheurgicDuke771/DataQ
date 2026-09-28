from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.poll_health_read_status import PollHealthReadStatus

T = TypeVar("T", bound="PollHealthRead")


@_attrs_define
class PollHealthRead:
    """One orchestration connection's poll staleness. `status="unknown"` — never
    healthy — means the connection has never been polled at all (#828).
    `last_polled_at` is the last ATTEMPT (success or failure), not the last
    success — the model tracks no separate success timestamp, so none is
    fabricated here. `status="failing"` means the connection is being polled on
    schedule but every recent attempt has errored; `last_error` then carries the
    classified, secret-free reason.

        Attributes:
            cadence_seconds (int):
            connection_id (UUID):
            last_error (None | str):
            last_polled_at (datetime.datetime | None):
            name (str):
            next_expected_at (datetime.datetime | None):
            provider (str):
            status (PollHealthReadStatus):
    """

    cadence_seconds: int
    connection_id: UUID
    last_error: None | str
    last_polled_at: datetime.datetime | None
    name: str
    next_expected_at: datetime.datetime | None
    provider: str
    status: PollHealthReadStatus
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        cadence_seconds = self.cadence_seconds

        connection_id = str(self.connection_id)

        last_error: None | str
        last_error = self.last_error

        last_polled_at: None | str
        if isinstance(self.last_polled_at, datetime.datetime):
            last_polled_at = self.last_polled_at.isoformat()
        else:
            last_polled_at = self.last_polled_at

        name = self.name

        next_expected_at: None | str
        if isinstance(self.next_expected_at, datetime.datetime):
            next_expected_at = self.next_expected_at.isoformat()
        else:
            next_expected_at = self.next_expected_at

        provider = self.provider

        status = self.status.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "cadence_seconds": cadence_seconds,
                "connection_id": connection_id,
                "last_error": last_error,
                "last_polled_at": last_polled_at,
                "name": name,
                "next_expected_at": next_expected_at,
                "provider": provider,
                "status": status,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        cadence_seconds = d.pop("cadence_seconds")

        connection_id = UUID(d.pop("connection_id"))

        def _parse_last_error(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        last_error = _parse_last_error(d.pop("last_error"))

        def _parse_last_polled_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_polled_at_type_0 = datetime.datetime.fromisoformat(data)

                return last_polled_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        last_polled_at = _parse_last_polled_at(d.pop("last_polled_at"))

        name = d.pop("name")

        def _parse_next_expected_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                next_expected_at_type_0 = datetime.datetime.fromisoformat(data)

                return next_expected_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        next_expected_at = _parse_next_expected_at(d.pop("next_expected_at"))

        provider = d.pop("provider")

        status = PollHealthReadStatus(d.pop("status"))

        poll_health_read = cls(
            cadence_seconds=cadence_seconds,
            connection_id=connection_id,
            last_error=last_error,
            last_polled_at=last_polled_at,
            name=name,
            next_expected_at=next_expected_at,
            provider=provider,
            status=status,
        )

        poll_health_read.additional_properties = d
        return poll_health_read

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
