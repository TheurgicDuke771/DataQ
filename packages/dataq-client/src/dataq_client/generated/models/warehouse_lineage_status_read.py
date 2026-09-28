from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="WarehouseLineageStatusRead")


@_attrs_define
class WarehouseLineageStatusRead:
    """A warehouse-native lineage source (Snowflake / UC) that is degraded or failing —
    so the graph can be qualified rather than shown as complete + current (#828, #858).

        Attributes:
            connection_id (UUID):
            name (str):
            type_ (str):
            degraded_reason (None | str | Unset):
            last_error (None | str | Unset):
            last_refreshed_at (datetime.datetime | None | Unset):
            prune_suspended (bool | Unset):  Default: False.
            prune_suspended_since (datetime.datetime | None | Unset):
            stale (bool | Unset):  Default: False.
            tier (None | str | Unset):
    """

    connection_id: UUID
    name: str
    type_: str
    degraded_reason: None | str | Unset = UNSET
    last_error: None | str | Unset = UNSET
    last_refreshed_at: datetime.datetime | None | Unset = UNSET
    prune_suspended: bool | Unset = False
    prune_suspended_since: datetime.datetime | None | Unset = UNSET
    stale: bool | Unset = False
    tier: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        connection_id = str(self.connection_id)

        name = self.name

        type_ = self.type_

        degraded_reason: None | str | Unset
        if isinstance(self.degraded_reason, Unset):
            degraded_reason = UNSET
        else:
            degraded_reason = self.degraded_reason

        last_error: None | str | Unset
        if isinstance(self.last_error, Unset):
            last_error = UNSET
        else:
            last_error = self.last_error

        last_refreshed_at: None | str | Unset
        if isinstance(self.last_refreshed_at, Unset):
            last_refreshed_at = UNSET
        elif isinstance(self.last_refreshed_at, datetime.datetime):
            last_refreshed_at = self.last_refreshed_at.isoformat()
        else:
            last_refreshed_at = self.last_refreshed_at

        prune_suspended = self.prune_suspended

        prune_suspended_since: None | str | Unset
        if isinstance(self.prune_suspended_since, Unset):
            prune_suspended_since = UNSET
        elif isinstance(self.prune_suspended_since, datetime.datetime):
            prune_suspended_since = self.prune_suspended_since.isoformat()
        else:
            prune_suspended_since = self.prune_suspended_since

        stale = self.stale

        tier: None | str | Unset
        if isinstance(self.tier, Unset):
            tier = UNSET
        else:
            tier = self.tier

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "connection_id": connection_id,
                "name": name,
                "type": type_,
            }
        )
        if degraded_reason is not UNSET:
            field_dict["degraded_reason"] = degraded_reason
        if last_error is not UNSET:
            field_dict["last_error"] = last_error
        if last_refreshed_at is not UNSET:
            field_dict["last_refreshed_at"] = last_refreshed_at
        if prune_suspended is not UNSET:
            field_dict["prune_suspended"] = prune_suspended
        if prune_suspended_since is not UNSET:
            field_dict["prune_suspended_since"] = prune_suspended_since
        if stale is not UNSET:
            field_dict["stale"] = stale
        if tier is not UNSET:
            field_dict["tier"] = tier

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        connection_id = UUID(d.pop("connection_id"))

        name = d.pop("name")

        type_ = d.pop("type")

        def _parse_degraded_reason(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        degraded_reason = _parse_degraded_reason(d.pop("degraded_reason", UNSET))

        def _parse_last_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        last_error = _parse_last_error(d.pop("last_error", UNSET))

        def _parse_last_refreshed_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_refreshed_at_type_0 = datetime.datetime.fromisoformat(data)

                return last_refreshed_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        last_refreshed_at = _parse_last_refreshed_at(d.pop("last_refreshed_at", UNSET))

        prune_suspended = d.pop("prune_suspended", UNSET)

        def _parse_prune_suspended_since(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                prune_suspended_since_type_0 = datetime.datetime.fromisoformat(data)

                return prune_suspended_since_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        prune_suspended_since = _parse_prune_suspended_since(d.pop("prune_suspended_since", UNSET))

        stale = d.pop("stale", UNSET)

        def _parse_tier(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        tier = _parse_tier(d.pop("tier", UNSET))

        warehouse_lineage_status_read = cls(
            connection_id=connection_id,
            name=name,
            type_=type_,
            degraded_reason=degraded_reason,
            last_error=last_error,
            last_refreshed_at=last_refreshed_at,
            prune_suspended=prune_suspended,
            prune_suspended_since=prune_suspended_since,
            stale=stale,
            tier=tier,
        )

        warehouse_lineage_status_read.additional_properties = d
        return warehouse_lineage_status_read

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
