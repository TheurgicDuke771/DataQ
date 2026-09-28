from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.data_subject_incident_match import DataSubjectIncidentMatch
    from ..models.data_subject_match import DataSubjectMatch


T = TypeVar("T", bound="DataSubjectExportResponse")


@_attrs_define
class DataSubjectExportResponse:
    """
    Attributes:
        column (str):
        incident_match_count (int):
        incident_matches (list[DataSubjectIncidentMatch]):
        match_count (int):
        matches (list[DataSubjectMatch]):
        value (str):
    """

    column: str
    incident_match_count: int
    incident_matches: list[DataSubjectIncidentMatch]
    match_count: int
    matches: list[DataSubjectMatch]
    value: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        column = self.column

        incident_match_count = self.incident_match_count

        incident_matches = []
        for incident_matches_item_data in self.incident_matches:
            incident_matches_item = incident_matches_item_data.to_dict()
            incident_matches.append(incident_matches_item)

        match_count = self.match_count

        matches = []
        for matches_item_data in self.matches:
            matches_item = matches_item_data.to_dict()
            matches.append(matches_item)

        value = self.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "column": column,
                "incident_match_count": incident_match_count,
                "incident_matches": incident_matches,
                "match_count": match_count,
                "matches": matches,
                "value": value,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.data_subject_incident_match import DataSubjectIncidentMatch
        from ..models.data_subject_match import DataSubjectMatch

        d = dict(src_dict)
        column = d.pop("column")

        incident_match_count = d.pop("incident_match_count")

        incident_matches = []
        _incident_matches = d.pop("incident_matches")
        for incident_matches_item_data in _incident_matches:
            incident_matches_item = DataSubjectIncidentMatch.from_dict(incident_matches_item_data)

            incident_matches.append(incident_matches_item)

        match_count = d.pop("match_count")

        matches = []
        _matches = d.pop("matches")
        for matches_item_data in _matches:
            matches_item = DataSubjectMatch.from_dict(matches_item_data)

            matches.append(matches_item)

        value = d.pop("value")

        data_subject_export_response = cls(
            column=column,
            incident_match_count=incident_match_count,
            incident_matches=incident_matches,
            match_count=match_count,
            matches=matches,
            value=value,
        )

        data_subject_export_response.additional_properties = d
        return data_subject_export_response

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
