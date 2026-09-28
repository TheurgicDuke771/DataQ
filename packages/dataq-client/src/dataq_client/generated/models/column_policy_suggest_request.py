from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..models.column_policy_suggest_request_file_format_type_0 import (
    ColumnPolicySuggestRequestFileFormatType0,
)
from ..types import UNSET, Unset

T = TypeVar("T", bound="ColumnPolicySuggestRequest")


@_attrs_define
class ColumnPolicySuggestRequest:
    """The suite's target to profile + classify — same shape as the profiler request,
    minus ``columns`` (all of the target's columns are classified).

        Attributes:
            catalog (None | str | Unset):
            file_format (ColumnPolicySuggestRequestFileFormatType0 | None | Unset):
            namespace (None | str | Unset):
            path (None | str | Unset):
            schema (None | str | Unset):
            table (None | str | Unset):
            top_n (int | Unset):  Default: 20.
    """

    catalog: None | str | Unset = UNSET
    file_format: ColumnPolicySuggestRequestFileFormatType0 | None | Unset = UNSET
    namespace: None | str | Unset = UNSET
    path: None | str | Unset = UNSET
    schema: None | str | Unset = UNSET
    table: None | str | Unset = UNSET
    top_n: int | Unset = 20

    def to_dict(self) -> dict[str, Any]:
        catalog: None | str | Unset
        if isinstance(self.catalog, Unset):
            catalog = UNSET
        else:
            catalog = self.catalog

        file_format: None | str | Unset
        if isinstance(self.file_format, Unset):
            file_format = UNSET
        elif isinstance(self.file_format, ColumnPolicySuggestRequestFileFormatType0):
            file_format = self.file_format.value
        else:
            file_format = self.file_format

        namespace: None | str | Unset
        if isinstance(self.namespace, Unset):
            namespace = UNSET
        else:
            namespace = self.namespace

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

        top_n = self.top_n

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if catalog is not UNSET:
            field_dict["catalog"] = catalog
        if file_format is not UNSET:
            field_dict["file_format"] = file_format
        if namespace is not UNSET:
            field_dict["namespace"] = namespace
        if path is not UNSET:
            field_dict["path"] = path
        if schema is not UNSET:
            field_dict["schema"] = schema
        if table is not UNSET:
            field_dict["table"] = table
        if top_n is not UNSET:
            field_dict["top_n"] = top_n

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

        def _parse_catalog(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        catalog = _parse_catalog(d.pop("catalog", UNSET))

        def _parse_file_format(
            data: object,
        ) -> ColumnPolicySuggestRequestFileFormatType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                file_format_type_0 = ColumnPolicySuggestRequestFileFormatType0(data)

                return file_format_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ColumnPolicySuggestRequestFileFormatType0 | None | Unset, data)

        file_format = _parse_file_format(d.pop("file_format", UNSET))

        def _parse_namespace(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        namespace = _parse_namespace(d.pop("namespace", UNSET))

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

        top_n = d.pop("top_n", UNSET)

        column_policy_suggest_request = cls(
            catalog=catalog,
            file_format=file_format,
            namespace=namespace,
            path=path,
            schema=schema,
            table=table,
            top_n=top_n,
        )

        return column_policy_suggest_request
