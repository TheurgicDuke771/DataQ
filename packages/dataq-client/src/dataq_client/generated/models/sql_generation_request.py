from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.additional_table_ref import AdditionalTableRef


T = TypeVar("T", bound="SqlGenerationRequest")


@_attrs_define
class SqlGenerationRequest:
    """
    Attributes:
        description (str):
        suite_id (UUID):
        additional_tables (list[AdditionalTableRef] | Unset):
        include_profile (bool | Unset):  Default: False.
    """

    description: str
    suite_id: UUID
    additional_tables: list[AdditionalTableRef] | Unset = UNSET
    include_profile: bool | Unset = False

    def to_dict(self) -> dict[str, Any]:
        description = self.description

        suite_id = str(self.suite_id)

        additional_tables: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.additional_tables, Unset):
            additional_tables = []
            for additional_tables_item_data in self.additional_tables:
                additional_tables_item = additional_tables_item_data.to_dict()
                additional_tables.append(additional_tables_item)

        include_profile = self.include_profile

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "description": description,
                "suite_id": suite_id,
            }
        )
        if additional_tables is not UNSET:
            field_dict["additional_tables"] = additional_tables
        if include_profile is not UNSET:
            field_dict["include_profile"] = include_profile

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.additional_table_ref import AdditionalTableRef

        d = dict(src_dict)
        description = d.pop("description")

        suite_id = UUID(d.pop("suite_id"))

        _additional_tables = d.pop("additional_tables", UNSET)
        additional_tables: list[AdditionalTableRef] | Unset = UNSET
        if _additional_tables is not UNSET:
            additional_tables = []
            for additional_tables_item_data in _additional_tables:
                additional_tables_item = AdditionalTableRef.from_dict(additional_tables_item_data)

                additional_tables.append(additional_tables_item)

        include_profile = d.pop("include_profile", UNSET)

        sql_generation_request = cls(
            description=description,
            suite_id=suite_id,
            additional_tables=additional_tables,
            include_profile=include_profile,
        )

        return sql_generation_request
