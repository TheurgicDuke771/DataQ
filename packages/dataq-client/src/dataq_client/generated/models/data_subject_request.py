from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

T = TypeVar("T", bound="DataSubjectRequest")


@_attrs_define
class DataSubjectRequest:
    """The (column, value) pair identifying a subject's warehouse row — DataQ has
    no people-table, so this IS the subject identifier.

        Attributes:
            column (str):
            value (str):
    """

    column: str
    value: str

    def to_dict(self) -> dict[str, Any]:
        column = self.column

        value = self.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "column": column,
                "value": value,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        column = d.pop("column")

        value = d.pop("value")

        data_subject_request = cls(
            column=column,
            value=value,
        )

        return data_subject_request
