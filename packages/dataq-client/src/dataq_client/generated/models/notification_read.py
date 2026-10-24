from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="NotificationRead")


@_attrs_define
class NotificationRead:
    """One entry of the caller's feed. `deep_link` is the run page while the run still
    exists and null once it is gone (the entry outlives the run); `incident_link` is the
    incident's own view while the incident exists.

        Attributes:
            body (str):
            created_at (datetime.datetime):
            deep_link (None | str):
            id (UUID):
            incident_id (None | UUID):
            incident_link (None | str):
            read_at (datetime.datetime | None):
            run_id (None | UUID):
            severity (str):
            suite_id (UUID):
            title (str):
    """

    body: str
    created_at: datetime.datetime
    deep_link: None | str
    id: UUID
    incident_id: None | UUID
    incident_link: None | str
    read_at: datetime.datetime | None
    run_id: None | UUID
    severity: str
    suite_id: UUID
    title: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        body = self.body

        created_at = self.created_at.isoformat()

        deep_link: None | str
        deep_link = self.deep_link

        id = str(self.id)

        incident_id: None | str
        if isinstance(self.incident_id, UUID):
            incident_id = str(self.incident_id)
        else:
            incident_id = self.incident_id

        incident_link: None | str
        incident_link = self.incident_link

        read_at: None | str
        if isinstance(self.read_at, datetime.datetime):
            read_at = self.read_at.isoformat()
        else:
            read_at = self.read_at

        run_id: None | str
        if isinstance(self.run_id, UUID):
            run_id = str(self.run_id)
        else:
            run_id = self.run_id

        severity = self.severity

        suite_id = str(self.suite_id)

        title = self.title

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "body": body,
                "created_at": created_at,
                "deep_link": deep_link,
                "id": id,
                "incident_id": incident_id,
                "incident_link": incident_link,
                "read_at": read_at,
                "run_id": run_id,
                "severity": severity,
                "suite_id": suite_id,
                "title": title,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        body = d.pop("body")

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        def _parse_deep_link(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        deep_link = _parse_deep_link(d.pop("deep_link"))

        id = UUID(d.pop("id"))

        def _parse_incident_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                incident_id_type_0 = UUID(data)

                return incident_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        incident_id = _parse_incident_id(d.pop("incident_id"))

        def _parse_incident_link(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        incident_link = _parse_incident_link(d.pop("incident_link"))

        def _parse_read_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                read_at_type_0 = datetime.datetime.fromisoformat(data)

                return read_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        read_at = _parse_read_at(d.pop("read_at"))

        def _parse_run_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                run_id_type_0 = UUID(data)

                return run_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        run_id = _parse_run_id(d.pop("run_id"))

        severity = d.pop("severity")

        suite_id = UUID(d.pop("suite_id"))

        title = d.pop("title")

        notification_read = cls(
            body=body,
            created_at=created_at,
            deep_link=deep_link,
            id=id,
            incident_id=incident_id,
            incident_link=incident_link,
            read_at=read_at,
            run_id=run_id,
            severity=severity,
            suite_id=suite_id,
            title=title,
        )

        notification_read.additional_properties = d
        return notification_read

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
