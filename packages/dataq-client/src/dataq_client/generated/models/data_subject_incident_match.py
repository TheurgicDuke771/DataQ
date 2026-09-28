from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.data_subject_incident_match_observed_value_type_0 import (
        DataSubjectIncidentMatchObservedValueType0,
    )


T = TypeVar("T", bound="DataSubjectIncidentMatch")


@_attrs_define
class DataSubjectIncidentMatch:
    """An incident whose stored evidence snapshot carries the subject's value — a third
    persisted copy, outside the retention clock, that `results` matching cannot see.

        Attributes:
            check_id (UUID):
            check_name (str):
            created_at (datetime.datetime):
            incident_id (UUID):
            observed_value (DataSubjectIncidentMatchObservedValueType0 | None):
            status (str):
            suite_id (UUID):
            suite_name (str):
    """

    check_id: UUID
    check_name: str
    created_at: datetime.datetime
    incident_id: UUID
    observed_value: DataSubjectIncidentMatchObservedValueType0 | None
    status: str
    suite_id: UUID
    suite_name: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.data_subject_incident_match_observed_value_type_0 import (
            DataSubjectIncidentMatchObservedValueType0,
        )

        check_id = str(self.check_id)

        check_name = self.check_name

        created_at = self.created_at.isoformat()

        incident_id = str(self.incident_id)

        observed_value: dict[str, Any] | None
        if isinstance(self.observed_value, DataSubjectIncidentMatchObservedValueType0):
            observed_value = self.observed_value.to_dict()
        else:
            observed_value = self.observed_value

        status = self.status

        suite_id = str(self.suite_id)

        suite_name = self.suite_name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "check_id": check_id,
                "check_name": check_name,
                "created_at": created_at,
                "incident_id": incident_id,
                "observed_value": observed_value,
                "status": status,
                "suite_id": suite_id,
                "suite_name": suite_name,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.data_subject_incident_match_observed_value_type_0 import (
            DataSubjectIncidentMatchObservedValueType0,
        )

        d = dict(src_dict)
        check_id = UUID(d.pop("check_id"))

        check_name = d.pop("check_name")

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        incident_id = UUID(d.pop("incident_id"))

        def _parse_observed_value(
            data: object,
        ) -> DataSubjectIncidentMatchObservedValueType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                observed_value_type_0 = DataSubjectIncidentMatchObservedValueType0.from_dict(data)

                return observed_value_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(DataSubjectIncidentMatchObservedValueType0 | None, data)

        observed_value = _parse_observed_value(d.pop("observed_value"))

        status = d.pop("status")

        suite_id = UUID(d.pop("suite_id"))

        suite_name = d.pop("suite_name")

        data_subject_incident_match = cls(
            check_id=check_id,
            check_name=check_name,
            created_at=created_at,
            incident_id=incident_id,
            observed_value=observed_value,
            status=status,
            suite_id=suite_id,
            suite_name=suite_name,
        )

        data_subject_incident_match.additional_properties = d
        return data_subject_incident_match

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
