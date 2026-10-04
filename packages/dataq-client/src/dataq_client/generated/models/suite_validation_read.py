from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.document_problem_read import DocumentProblemRead


T = TypeVar("T", bound="SuiteValidationRead")


@_attrs_define
class SuiteValidationRead:
    """`valid` is true exactly when `problems` is empty — importing this document onto
    this connection would then be accepted. It says nothing about whether the checks
    would PASS: no datasource is opened and no column is looked up.

        Attributes:
            check_count (int):
            problems (list[DocumentProblemRead]):
            valid (bool):
    """

    check_count: int
    problems: list[DocumentProblemRead]
    valid: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        check_count = self.check_count

        problems = []
        for problems_item_data in self.problems:
            problems_item = problems_item_data.to_dict()
            problems.append(problems_item)

        valid = self.valid

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "check_count": check_count,
                "problems": problems,
                "valid": valid,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.document_problem_read import DocumentProblemRead

        d = dict(src_dict)
        check_count = d.pop("check_count")

        problems = []
        _problems = d.pop("problems")
        for problems_item_data in _problems:
            problems_item = DocumentProblemRead.from_dict(problems_item_data)

            problems.append(problems_item)

        valid = d.pop("valid")

        suite_validation_read = cls(
            check_count=check_count,
            problems=problems,
            valid=valid,
        )

        suite_validation_read.additional_properties = d
        return suite_validation_read

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
