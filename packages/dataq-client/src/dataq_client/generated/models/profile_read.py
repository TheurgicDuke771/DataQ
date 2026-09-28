from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.column_profile_read import ColumnProfileRead


T = TypeVar("T", bound="ProfileRead")


@_attrs_define
class ProfileRead:
    """Profile result. Identity fields are type-specific: SQL datasources fill
    `table` / `schema` (+ `catalog` for Unity Catalog), flat-file datasources fill
    `path` / `file_format`.

        Attributes:
            columns (list[ColumnProfileRead]):
            row_count (int):
            catalog (None | str | Unset):
            file_format (None | str | Unset):
            path (None | str | Unset):
            schema (None | str | Unset):
            table (None | str | Unset):
    """

    columns: list[ColumnProfileRead]
    row_count: int
    catalog: None | str | Unset = UNSET
    file_format: None | str | Unset = UNSET
    path: None | str | Unset = UNSET
    schema: None | str | Unset = UNSET
    table: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        columns = []
        for columns_item_data in self.columns:
            columns_item = columns_item_data.to_dict()
            columns.append(columns_item)

        row_count = self.row_count

        catalog: None | str | Unset
        if isinstance(self.catalog, Unset):
            catalog = UNSET
        else:
            catalog = self.catalog

        file_format: None | str | Unset
        if isinstance(self.file_format, Unset):
            file_format = UNSET
        else:
            file_format = self.file_format

        path: None | str | Unset
        if isinstance(self.path, Unset):
            path = UNSET
        else:
            path = self.path

        schema: None | str | Unset
        if isinstance(self.schema, Unset):
            schema = UNSET
        else:
            schema = self.schema

        table: None | str | Unset
        if isinstance(self.table, Unset):
            table = UNSET
        else:
            table = self.table

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "columns": columns,
                "row_count": row_count,
            }
        )
        if catalog is not UNSET:
            field_dict["catalog"] = catalog
        if file_format is not UNSET:
            field_dict["file_format"] = file_format
        if path is not UNSET:
            field_dict["path"] = path
        if schema is not UNSET:
            field_dict["schema"] = schema
        if table is not UNSET:
            field_dict["table"] = table

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.column_profile_read import ColumnProfileRead

        d = dict(src_dict)
        columns = []
        _columns = d.pop("columns")
        for columns_item_data in _columns:
            columns_item = ColumnProfileRead.from_dict(columns_item_data)

            columns.append(columns_item)

        row_count = d.pop("row_count")

        def _parse_catalog(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        catalog = _parse_catalog(d.pop("catalog", UNSET))

        def _parse_file_format(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        file_format = _parse_file_format(d.pop("file_format", UNSET))

        def _parse_path(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        path = _parse_path(d.pop("path", UNSET))

        def _parse_schema(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        schema = _parse_schema(d.pop("schema", UNSET))

        def _parse_table(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        table = _parse_table(d.pop("table", UNSET))

        profile_read = cls(
            columns=columns,
            row_count=row_count,
            catalog=catalog,
            file_format=file_format,
            path=path,
            schema=schema,
            table=table,
        )

        profile_read.additional_properties = d
        return profile_read

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
