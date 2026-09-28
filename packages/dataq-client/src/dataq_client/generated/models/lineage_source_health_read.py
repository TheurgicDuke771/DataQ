from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="LineageSourceHealthRead")


@_attrs_define
class LineageSourceHealthRead:
    """A lineage-feeding connection whose poll is currently failing (#828).

    Attributes:
        connection_id (UUID):
        consecutive_failures (int):
        name (str):
        type_ (str):
        last_error (None | str | Unset):
        last_polled_at (datetime.datetime | None | Unset):
    """

    connection_id: UUID
    consecutive_failures: int
    name: str
    type_: str
    last_error: None | str | Unset = UNSET
    last_polled_at: datetime.datetime | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        connection_id = str(self.connection_id)

        consecutive_failures = self.consecutive_failures

        name = self.name

        type_ = self.type_

        last_error: None | str | Unset
        if isinstance(self.last_error, Unset):
            last_error = UNSET
        else:
            last_error = self.last_error

        last_polled_at: None | str | Unset
        if isinstance(self.last_polled_at, Unset):
            last_polled_at = UNSET
        elif isinstance(self.last_polled_at, datetime.datetime):
            last_polled_at = self.last_polled_at.isoformat()
        else:
            last_polled_at = self.last_polled_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "connection_id": connection_id,
                "consecutive_failures": consecutive_failures,
                "name": name,
                "type": type_,
            }
        )
        if last_error is not UNSET:
            field_dict["last_error"] = last_error
        if last_polled_at is not UNSET:
            field_dict["last_polled_at"] = last_polled_at

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        connection_id = UUID(d.pop("connection_id"))

        consecutive_failures = d.pop("consecutive_failures")

        name = d.pop("name")

        type_ = d.pop("type")

        def _parse_last_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        last_error = _parse_last_error(d.pop("last_error", UNSET))

        def _parse_last_polled_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_polled_at_type_0 = datetime.datetime.fromisoformat(data)

                return last_polled_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        last_polled_at = _parse_last_polled_at(d.pop("last_polled_at", UNSET))

        lineage_source_health_read = cls(
            connection_id=connection_id,
            consecutive_failures=consecutive_failures,
            name=name,
            type_=type_,
            last_error=last_error,
            last_polled_at=last_polled_at,
        )

        lineage_source_health_read.additional_properties = d
        return lineage_source_health_read

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
