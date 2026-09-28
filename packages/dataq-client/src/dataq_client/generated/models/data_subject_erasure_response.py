from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="DataSubjectErasureResponse")


@_attrs_define
class DataSubjectErasureResponse:
    """
    Attributes:
        column (str):
        erased_count (int):
        erased_incident_count (int):
        erased_result_count (int):
        matched_count (int):
        matched_incident_count (int):
        matched_result_count (int):
        value (str):
    """

    column: str
    erased_count: int
    erased_incident_count: int
    erased_result_count: int
    matched_count: int
    matched_incident_count: int
    matched_result_count: int
    value: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        column = self.column

        erased_count = self.erased_count

        erased_incident_count = self.erased_incident_count

        erased_result_count = self.erased_result_count

        matched_count = self.matched_count

        matched_incident_count = self.matched_incident_count

        matched_result_count = self.matched_result_count

        value = self.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "column": column,
                "erased_count": erased_count,
                "erased_incident_count": erased_incident_count,
                "erased_result_count": erased_result_count,
                "matched_count": matched_count,
                "matched_incident_count": matched_incident_count,
                "matched_result_count": matched_result_count,
                "value": value,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        column = d.pop("column")

        erased_count = d.pop("erased_count")

        erased_incident_count = d.pop("erased_incident_count")

        erased_result_count = d.pop("erased_result_count")

        matched_count = d.pop("matched_count")

        matched_incident_count = d.pop("matched_incident_count")

        matched_result_count = d.pop("matched_result_count")

        value = d.pop("value")

        data_subject_erasure_response = cls(
            column=column,
            erased_count=erased_count,
            erased_incident_count=erased_incident_count,
            erased_result_count=erased_result_count,
            matched_count=matched_count,
            matched_incident_count=matched_incident_count,
            matched_result_count=matched_result_count,
            value=value,
        )

        data_subject_erasure_response.additional_properties = d
        return data_subject_erasure_response

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
