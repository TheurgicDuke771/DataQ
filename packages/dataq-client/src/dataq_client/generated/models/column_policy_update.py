from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="ColumnPolicyUpdate")


@_attrs_define
class ColumnPolicyUpdate:
    """
    Attributes:
        identifier_column (None | str | Unset):
        pii_columns (list[str] | Unset):
        require_classification (bool | None | Unset):
    """

    identifier_column: None | str | Unset = UNSET
    pii_columns: list[str] | Unset = UNSET
    require_classification: bool | None | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        identifier_column: None | str | Unset
        if isinstance(self.identifier_column, Unset):
            identifier_column = UNSET
        else:
            identifier_column = self.identifier_column

        pii_columns: list[str] | Unset = UNSET
        if not isinstance(self.pii_columns, Unset):
            pii_columns = self.pii_columns

        require_classification: bool | None | Unset
        if isinstance(self.require_classification, Unset):
            require_classification = UNSET
        else:
            require_classification = self.require_classification

        field_dict: dict[str, Any] = {}

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

        def _parse_require_classification(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        require_classification = _parse_require_classification(
            d.pop("require_classification", UNSET)
        )

        column_policy_update = cls(
            identifier_column=identifier_column,
            pii_columns=pii_columns,
            require_classification=require_classification,
        )

        return column_policy_update
