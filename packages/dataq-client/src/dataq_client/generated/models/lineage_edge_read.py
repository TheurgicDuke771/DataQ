from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="LineageEdgeRead")


@_attrs_define
class LineageEdgeRead:
    """One edge of the lineage neighbourhood, `source` (upstream) → `target`
    (downstream) asset id. The UI draws exactly these — without them a graph could
    only guess which depth-2 node hangs off which depth-1 node (#805).

        Attributes:
            source (UUID):
            target (UUID):
            column_coverage (str | Unset):  Default: 'unknown'.
            columns (list[list[str]] | None | Unset):
    """

    source: UUID
    target: UUID
    column_coverage: str | Unset = "unknown"
    columns: list[list[str]] | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        source = str(self.source)

        target = str(self.target)

        column_coverage = self.column_coverage

        columns: list[list[str]] | None | Unset
        if isinstance(self.columns, Unset):
            columns = UNSET
        elif isinstance(self.columns, list):
            columns = []
            for columns_type_0_item_data in self.columns:
                columns_type_0_item = []
                for columns_type_0_item_item_data in columns_type_0_item_data:
                    columns_type_0_item_item: str
                    columns_type_0_item_item = columns_type_0_item_item_data
                    columns_type_0_item.append(columns_type_0_item_item)

                columns.append(columns_type_0_item)

        else:
            columns = self.columns

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "source": source,
                "target": target,
            }
        )
        if column_coverage is not UNSET:
            field_dict["column_coverage"] = column_coverage
        if columns is not UNSET:
            field_dict["columns"] = columns

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        source = UUID(d.pop("source"))

        target = UUID(d.pop("target"))

        column_coverage = d.pop("column_coverage", UNSET)

        def _parse_columns(data: object) -> list[list[str]] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                columns_type_0 = []
                _columns_type_0 = data
                for columns_type_0_item_data in _columns_type_0:
                    columns_type_0_item = []
                    _columns_type_0_item = columns_type_0_item_data
                    for columns_type_0_item_item_data in _columns_type_0_item:

                        def _parse_columns_type_0_item_item(data: object) -> str:
                            return cast(str, data)

                        columns_type_0_item_item = _parse_columns_type_0_item_item(
                            columns_type_0_item_item_data
                        )

                        columns_type_0_item.append(columns_type_0_item_item)

                    columns_type_0.append(columns_type_0_item)

                return columns_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[list[str]] | None | Unset, data)

        columns = _parse_columns(d.pop("columns", UNSET))

        lineage_edge_read = cls(
            source=source,
            target=target,
            column_coverage=column_coverage,
            columns=columns,
        )

        lineage_edge_read.additional_properties = d
        return lineage_edge_read

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
