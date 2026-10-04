from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="DocumentProblemRead")


@_attrs_define
class DocumentProblemRead:
    """One reason the document would be refused. `location` is `document` for a problem
    with the document as a whole, `checks[2]` for the third check, or a field path such
    as `checks[2].expectation_type` for a shape error.

        Attributes:
            code (str):
            location (str):
            message (str):
            check_name (None | str | Unset):
    """

    code: str
    location: str
    message: str
    check_name: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        code = self.code

        location = self.location

        message = self.message

        check_name: None | str | Unset
        if isinstance(self.check_name, Unset):
            check_name = UNSET
        else:
            check_name = self.check_name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "code": code,
                "location": location,
                "message": message,
            }
        )
        if check_name is not UNSET:
            field_dict["check_name"] = check_name

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        code = d.pop("code")

        location = d.pop("location")

        message = d.pop("message")

        def _parse_check_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        check_name = _parse_check_name(d.pop("check_name", UNSET))

        document_problem_read = cls(
            code=code,
            location=location,
            message=message,
            check_name=check_name,
        )

        document_problem_read.additional_properties = d
        return document_problem_read

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
