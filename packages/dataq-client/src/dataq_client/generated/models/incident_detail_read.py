from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.incident_detail_read_evidence_type_0 import IncidentDetailReadEvidenceType0


T = TypeVar("T", bound="IncidentDetailRead")


@_attrs_define
class IncidentDetailRead:
    """Incident detail — the summary plus the full evidence card + transition
    actors/notes + the reopen link.

        Attributes:
            acknowledge_note (None | str):
            acknowledged_at (datetime.datetime | None):
            acknowledged_by (None | UUID):
            asset_id (UUID):
            asset_name (None | str):
            asset_namespace (None | str):
            check_id (UUID):
            check_name (None | str):
            created_at (datetime.datetime):
            evidence (IncidentDetailReadEvidenceType0 | None):
            id (UUID):
            last_seen_at (datetime.datetime):
            latest_status (None | str):
            occurrence_count (int):
            prior_incident_id (None | UUID):
            resolution_note (None | str):
            resolved_at (datetime.datetime | None):
            resolved_by (None | str):
            resolved_by_user_id (None | UUID):
            status (str):
            suite_id (UUID):
            resolution (None | str | Unset):
    """

    acknowledge_note: None | str
    acknowledged_at: datetime.datetime | None
    acknowledged_by: None | UUID
    asset_id: UUID
    asset_name: None | str
    asset_namespace: None | str
    check_id: UUID
    check_name: None | str
    created_at: datetime.datetime
    evidence: IncidentDetailReadEvidenceType0 | None
    id: UUID
    last_seen_at: datetime.datetime
    latest_status: None | str
    occurrence_count: int
    prior_incident_id: None | UUID
    resolution_note: None | str
    resolved_at: datetime.datetime | None
    resolved_by: None | str
    resolved_by_user_id: None | UUID
    status: str
    suite_id: UUID
    resolution: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.incident_detail_read_evidence_type_0 import (
            IncidentDetailReadEvidenceType0,
        )

        acknowledge_note: None | str
        acknowledge_note = self.acknowledge_note

        acknowledged_at: None | str
        if isinstance(self.acknowledged_at, datetime.datetime):
            acknowledged_at = self.acknowledged_at.isoformat()
        else:
            acknowledged_at = self.acknowledged_at

        acknowledged_by: None | str
        if isinstance(self.acknowledged_by, UUID):
            acknowledged_by = str(self.acknowledged_by)
        else:
            acknowledged_by = self.acknowledged_by

        asset_id = str(self.asset_id)

        asset_name: None | str
        asset_name = self.asset_name

        asset_namespace: None | str
        asset_namespace = self.asset_namespace

        check_id = str(self.check_id)

        check_name: None | str
        check_name = self.check_name

        created_at = self.created_at.isoformat()

        evidence: dict[str, Any] | None
        if isinstance(self.evidence, IncidentDetailReadEvidenceType0):
            evidence = self.evidence.to_dict()
        else:
            evidence = self.evidence

        id = str(self.id)

        last_seen_at = self.last_seen_at.isoformat()

        latest_status: None | str
        latest_status = self.latest_status

        occurrence_count = self.occurrence_count

        prior_incident_id: None | str
        if isinstance(self.prior_incident_id, UUID):
            prior_incident_id = str(self.prior_incident_id)
        else:
            prior_incident_id = self.prior_incident_id

        resolution_note: None | str
        resolution_note = self.resolution_note

        resolved_at: None | str
        if isinstance(self.resolved_at, datetime.datetime):
            resolved_at = self.resolved_at.isoformat()
        else:
            resolved_at = self.resolved_at

        resolved_by: None | str
        resolved_by = self.resolved_by

        resolved_by_user_id: None | str
        if isinstance(self.resolved_by_user_id, UUID):
            resolved_by_user_id = str(self.resolved_by_user_id)
        else:
            resolved_by_user_id = self.resolved_by_user_id

        status = self.status

        suite_id = str(self.suite_id)

        resolution: None | str | Unset
        if isinstance(self.resolution, Unset):
            resolution = UNSET
        else:
            resolution = self.resolution

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "acknowledge_note": acknowledge_note,
                "acknowledged_at": acknowledged_at,
                "acknowledged_by": acknowledged_by,
                "asset_id": asset_id,
                "asset_name": asset_name,
                "asset_namespace": asset_namespace,
                "check_id": check_id,
                "check_name": check_name,
                "created_at": created_at,
                "evidence": evidence,
                "id": id,
                "last_seen_at": last_seen_at,
                "latest_status": latest_status,
                "occurrence_count": occurrence_count,
                "prior_incident_id": prior_incident_id,
                "resolution_note": resolution_note,
                "resolved_at": resolved_at,
                "resolved_by": resolved_by,
                "resolved_by_user_id": resolved_by_user_id,
                "status": status,
                "suite_id": suite_id,
            }
        )
        if resolution is not UNSET:
            field_dict["resolution"] = resolution

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.incident_detail_read_evidence_type_0 import (
            IncidentDetailReadEvidenceType0,
        )

        d = dict(src_dict)

        def _parse_acknowledge_note(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        acknowledge_note = _parse_acknowledge_note(d.pop("acknowledge_note"))

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

        def _parse_acknowledged_by(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                acknowledged_by_type_0 = UUID(data)

                return acknowledged_by_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        acknowledged_by = _parse_acknowledged_by(d.pop("acknowledged_by"))

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

        def _parse_evidence(data: object) -> IncidentDetailReadEvidenceType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                evidence_type_0 = IncidentDetailReadEvidenceType0.from_dict(data)

                return evidence_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(IncidentDetailReadEvidenceType0 | None, data)

        evidence = _parse_evidence(d.pop("evidence"))

        id = UUID(d.pop("id"))

        last_seen_at = datetime.datetime.fromisoformat(d.pop("last_seen_at"))

        def _parse_latest_status(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        latest_status = _parse_latest_status(d.pop("latest_status"))

        occurrence_count = d.pop("occurrence_count")

        def _parse_prior_incident_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                prior_incident_id_type_0 = UUID(data)

                return prior_incident_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        prior_incident_id = _parse_prior_incident_id(d.pop("prior_incident_id"))

        def _parse_resolution_note(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        resolution_note = _parse_resolution_note(d.pop("resolution_note"))

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

        def _parse_resolved_by_user_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                resolved_by_user_id_type_0 = UUID(data)

                return resolved_by_user_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        resolved_by_user_id = _parse_resolved_by_user_id(d.pop("resolved_by_user_id"))

        status = d.pop("status")

        suite_id = UUID(d.pop("suite_id"))

        def _parse_resolution(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        resolution = _parse_resolution(d.pop("resolution", UNSET))

        incident_detail_read = cls(
            acknowledge_note=acknowledge_note,
            acknowledged_at=acknowledged_at,
            acknowledged_by=acknowledged_by,
            asset_id=asset_id,
            asset_name=asset_name,
            asset_namespace=asset_namespace,
            check_id=check_id,
            check_name=check_name,
            created_at=created_at,
            evidence=evidence,
            id=id,
            last_seen_at=last_seen_at,
            latest_status=latest_status,
            occurrence_count=occurrence_count,
            prior_incident_id=prior_incident_id,
            resolution_note=resolution_note,
            resolved_at=resolved_at,
            resolved_by=resolved_by,
            resolved_by_user_id=resolved_by_user_id,
            status=status,
            suite_id=suite_id,
            resolution=resolution,
        )

        incident_detail_read.additional_properties = d
        return incident_detail_read

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
