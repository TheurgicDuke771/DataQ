from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="IncidentRead")


@_attrs_define
class IncidentRead:
    """List-row / summary view of an incident. ``check_name`` / ``asset_*`` are
    lifted from the snapshotted evidence card (fallbacks when absent) so the list
    renders without a join; ``latest_status`` is the breaching tier of the most
    recent occurrence.

        Attributes:
            acknowledged_at (datetime.datetime | None):
            asset_id (UUID):
            asset_name (None | str):
            asset_namespace (None | str):
            check_id (UUID):
            check_name (None | str):
            created_at (datetime.datetime):
            id (UUID):
            last_seen_at (datetime.datetime):
            latest_status (None | str):
            occurrence_count (int):
            resolved_at (datetime.datetime | None):
            resolved_by (None | str):
            status (str):
            suite_id (UUID):
    """

    acknowledged_at: datetime.datetime | None
    asset_id: UUID
    asset_name: None | str
    asset_namespace: None | str
    check_id: UUID
    check_name: None | str
    created_at: datetime.datetime
    id: UUID
    last_seen_at: datetime.datetime
    latest_status: None | str
    occurrence_count: int
    resolved_at: datetime.datetime | None
    resolved_by: None | str
    status: str
    suite_id: UUID
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        acknowledged_at: None | str
        if isinstance(self.acknowledged_at, datetime.datetime):
            acknowledged_at = self.acknowledged_at.isoformat()
        else:
            acknowledged_at = self.acknowledged_at

        asset_id = str(self.asset_id)

        asset_name: None | str
        asset_name = self.asset_name

        asset_namespace: None | str
        asset_namespace = self.asset_namespace

        check_id = str(self.check_id)

        check_name: None | str
        check_name = self.check_name

        created_at = self.created_at.isoformat()

        id = str(self.id)

        last_seen_at = self.last_seen_at.isoformat()

        latest_status: None | str
        latest_status = self.latest_status

        occurrence_count = self.occurrence_count

        resolved_at: None | str
        if isinstance(self.resolved_at, datetime.datetime):
            resolved_at = self.resolved_at.isoformat()
        else:
            resolved_at = self.resolved_at

        resolved_by: None | str
        resolved_by = self.resolved_by

        status = self.status

        suite_id = str(self.suite_id)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "acknowledged_at": acknowledged_at,
                "asset_id": asset_id,
                "asset_name": asset_name,
                "asset_namespace": asset_namespace,
                "check_id": check_id,
                "check_name": check_name,
                "created_at": created_at,
                "id": id,
                "last_seen_at": last_seen_at,
                "latest_status": latest_status,
                "occurrence_count": occurrence_count,
                "resolved_at": resolved_at,
                "resolved_by": resolved_by,
                "status": status,
                "suite_id": suite_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

        def _parse_acknowledged_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                acknowledged_at_type_0 = datetime.datetime.fromisoformat(data)

                return acknowledged_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        acknowledged_at = _parse_acknowledged_at(d.pop("acknowledged_at"))

        asset_id = UUID(d.pop("asset_id"))

        def _parse_asset_name(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        asset_name = _parse_asset_name(d.pop("asset_name"))

        def _parse_asset_namespace(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        asset_namespace = _parse_asset_namespace(d.pop("asset_namespace"))

        check_id = UUID(d.pop("check_id"))

        def _parse_check_name(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        check_name = _parse_check_name(d.pop("check_name"))

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        id = UUID(d.pop("id"))

        last_seen_at = datetime.datetime.fromisoformat(d.pop("last_seen_at"))

        def _parse_latest_status(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        latest_status = _parse_latest_status(d.pop("latest_status"))

        occurrence_count = d.pop("occurrence_count")

        def _parse_resolved_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                resolved_at_type_0 = datetime.datetime.fromisoformat(data)

                return resolved_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        resolved_at = _parse_resolved_at(d.pop("resolved_at"))

        def _parse_resolved_by(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        resolved_by = _parse_resolved_by(d.pop("resolved_by"))

        status = d.pop("status")

        suite_id = UUID(d.pop("suite_id"))

        incident_read = cls(
            acknowledged_at=acknowledged_at,
            asset_id=asset_id,
            asset_name=asset_name,
            asset_namespace=asset_namespace,
            check_id=check_id,
            check_name=check_name,
            created_at=created_at,
            id=id,
            last_seen_at=last_seen_at,
            latest_status=latest_status,
            occurrence_count=occurrence_count,
            resolved_at=resolved_at,
            resolved_by=resolved_by,
            status=status,
            suite_id=suite_id,
        )

        incident_read.additional_properties = d
        return incident_read

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
