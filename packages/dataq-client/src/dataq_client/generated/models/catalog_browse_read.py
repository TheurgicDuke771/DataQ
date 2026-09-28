from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.catalog_browse_read_level import CatalogBrowseReadLevel

if TYPE_CHECKING:
    from ..models.catalog_entry_read import CatalogEntryRead


T = TypeVar("T", bound="CatalogBrowseRead")


@_attrs_define
class CatalogBrowseRead:
    """One level of the catalog → schema → table tree (a generic SQL connection such as
    PostgreSQL or MySQL starts at `schema`: its database is fixed). `truncated` means more names
    exist than `limit` — the list is then a prefix, and the target can still be typed by hand.

        Attributes:
            catalog (None | str):
            entries (list[CatalogEntryRead]):
            level (CatalogBrowseReadLevel):
            limit (int):
            schema (None | str):
            truncated (bool):
    """

    catalog: None | str
    entries: list[CatalogEntryRead]
    level: CatalogBrowseReadLevel
    limit: int
    schema: None | str
    truncated: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        catalog: None | str
        catalog = self.catalog

        entries = []
        for entries_item_data in self.entries:
            entries_item = entries_item_data.to_dict()
            entries.append(entries_item)

        level = self.level.value

        limit = self.limit

        schema: None | str
        schema = self.schema

        truncated = self.truncated

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "catalog": catalog,
                "entries": entries,
                "level": level,
                "limit": limit,
                "schema": schema,
                "truncated": truncated,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.catalog_entry_read import CatalogEntryRead

        d = dict(src_dict)

        def _parse_catalog(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        catalog = _parse_catalog(d.pop("catalog"))

        entries = []
        _entries = d.pop("entries")
        for entries_item_data in _entries:
            entries_item = CatalogEntryRead.from_dict(entries_item_data)

            entries.append(entries_item)

        level = CatalogBrowseReadLevel(d.pop("level"))

        limit = d.pop("limit")

        def _parse_schema(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        schema = _parse_schema(d.pop("schema"))

        truncated = d.pop("truncated")

        catalog_browse_read = cls(
            catalog=catalog,
            entries=entries,
            level=level,
            limit=limit,
            schema=schema,
            truncated=truncated,
        )

        catalog_browse_read.additional_properties = d
        return catalog_browse_read

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
