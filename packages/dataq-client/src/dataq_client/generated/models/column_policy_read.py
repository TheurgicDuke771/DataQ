from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="ColumnPolicyRead")


@_attrs_define
class ColumnPolicyRead:
    """A suite's failing-sample redaction policy: the shown ``identifier_column``
    (a non-PII row locator) + the always-masked ``pii_columns``, plus whether the
    suite is in fail-closed mode.

        Attributes:
            identifier_column (None | str | Unset):
            pii_columns (list[str] | Unset):
            require_classification (bool | Unset):  Default: False.
    """

    identifier_column: None | str | Unset = UNSET
    pii_columns: list[str] | Unset = UNSET
    require_classification: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        identifier_column: None | str | Unset
        if isinstance(self.identifier_column, Unset):
            identifier_column = UNSET
        else:
            identifier_column = self.identifier_column

        pii_columns: list[str] | Unset = UNSET
        if not isinstance(self.pii_columns, Unset):
            pii_columns = self.pii_columns

        require_classification = self.require_classification

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if identifier_column is not UNSET:
            field_dict["identifier_column"] = identifier_column
        if pii_columns is not UNSET:
            field_dict["pii_columns"] = pii_columns
        if require_classification is not UNSET:
            field_dict["require_classification"] = require_classification

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

        def _parse_identifier_column(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        identifier_column = _parse_identifier_column(d.pop("identifier_column", UNSET))

        pii_columns = cast(list[str], d.pop("pii_columns", UNSET))

        require_classification = d.pop("require_classification", UNSET)

        column_policy_read = cls(
            identifier_column=identifier_column,
            pii_columns=pii_columns,
            require_classification=require_classification,
        )

        column_policy_read.additional_properties = d
        return column_policy_read

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
