from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.inventory_sync_read_status import InventorySyncReadStatus

T = TypeVar("T", bound="InventorySyncRead")


@_attrs_define
class InventorySyncRead:
    """One warehouse connection's inventory-sync state.

    `tables_discovered` and `unmonitored` are `null` — never `0` — until the connection
    has been synced at least once: DataQ has not enumerated it, so "no tables" is not
    something it can claim. `unmonitored` counts assets from this connection that no
    suite targets, over the whole workspace (ADR 0037), not the caller's grants.

    `last_error` is the classified, secret-free reason for the last attempt and is
    present only while the connection is failing; `last_attempted_at` is an ATTEMPT, so
    a timestamp with an error beside it is a failure, not a successful sync.

        Attributes:
            connection_id (UUID):
            enabled (bool):
            env (str):
            failing_since (datetime.datetime | None):
            last_attempted_at (datetime.datetime | None):
            last_error (None | str):
            name (str):
            status (InventorySyncReadStatus):
            tables_discovered (int | None):
            type_ (str):
            unmonitored (int | None):
    """

    connection_id: UUID
    enabled: bool
    env: str
    failing_since: datetime.datetime | None
    last_attempted_at: datetime.datetime | None
    last_error: None | str
    name: str
    status: InventorySyncReadStatus
    tables_discovered: int | None
    type_: str
    unmonitored: int | None
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        connection_id = str(self.connection_id)

        enabled = self.enabled

        env = self.env

        failing_since: None | str
        if isinstance(self.failing_since, datetime.datetime):
            failing_since = self.failing_since.isoformat()
        else:
            failing_since = self.failing_since

        last_attempted_at: None | str
        if isinstance(self.last_attempted_at, datetime.datetime):
            last_attempted_at = self.last_attempted_at.isoformat()
        else:
            last_attempted_at = self.last_attempted_at

        last_error: None | str
        last_error = self.last_error

        name = self.name

        status = self.status.value

        tables_discovered: int | None
        tables_discovered = self.tables_discovered

        type_ = self.type_

        unmonitored: int | None
        unmonitored = self.unmonitored

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "connection_id": connection_id,
                "enabled": enabled,
                "env": env,
                "failing_since": failing_since,
                "last_attempted_at": last_attempted_at,
                "last_error": last_error,
                "name": name,
                "status": status,
                "tables_discovered": tables_discovered,
                "type": type_,
                "unmonitored": unmonitored,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        connection_id = UUID(d.pop("connection_id"))

        enabled = d.pop("enabled")

        env = d.pop("env")

        def _parse_failing_since(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                failing_since_type_0 = datetime.datetime.fromisoformat(data)

                return failing_since_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        failing_since = _parse_failing_since(d.pop("failing_since"))

        def _parse_last_attempted_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_attempted_at_type_0 = datetime.datetime.fromisoformat(data)

                return last_attempted_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        last_attempted_at = _parse_last_attempted_at(d.pop("last_attempted_at"))

        def _parse_last_error(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        last_error = _parse_last_error(d.pop("last_error"))

        name = d.pop("name")

        status = InventorySyncReadStatus(d.pop("status"))

        def _parse_tables_discovered(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        tables_discovered = _parse_tables_discovered(d.pop("tables_discovered"))

        type_ = d.pop("type")

        def _parse_unmonitored(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        unmonitored = _parse_unmonitored(d.pop("unmonitored"))

        inventory_sync_read = cls(
            connection_id=connection_id,
            enabled=enabled,
            env=env,
            failing_since=failing_since,
            last_attempted_at=last_attempted_at,
            last_error=last_error,
            name=name,
            status=status,
            tables_discovered=tables_discovered,
            type_=type_,
            unmonitored=unmonitored,
        )

        inventory_sync_read.additional_properties = d
        return inventory_sync_read

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
