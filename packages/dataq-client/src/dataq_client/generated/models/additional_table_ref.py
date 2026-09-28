from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="AdditionalTableRef")


@_attrs_define
class AdditionalTableRef:
    """A join-relevant related table on the SUITE'S OWN connection (#1649).
    There is deliberately no connection reference here: cross-connection
    joins are out of scope (a federated engine is a different ADR) and the
    `comparison` kind (ADR 0015) is the reconciliation shape for that —
    refusing it is structural, not an error path to hit.

        Attributes:
            table (str):
            catalog (None | str | Unset):
            schema (None | str | Unset):
    """

    table: str
    catalog: None | str | Unset = UNSET
    schema: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        table = self.table

        catalog: None | str | Unset
        if isinstance(self.catalog, Unset):
            catalog = UNSET
        else:
            catalog = self.catalog

        schema: None | str | Unset
        if isinstance(self.schema, Unset):
            schema = UNSET
        else:
            schema = self.schema

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "table": table,
            }
        )
        if catalog is not UNSET:
            field_dict["catalog"] = catalog
        if schema is not UNSET:
            field_dict["schema"] = schema

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        table = d.pop("table")

        def _parse_catalog(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        catalog = _parse_catalog(d.pop("catalog", UNSET))

        def _parse_schema(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        schema = _parse_schema(d.pop("schema", UNSET))

        additional_table_ref = cls(
            table=table,
            catalog=catalog,
            schema=schema,
        )

        return additional_table_ref
