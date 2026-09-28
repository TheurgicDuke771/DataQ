from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.catalog_entry_read_object_type_type_0 import CatalogEntryReadObjectTypeType0
from ..types import UNSET, Unset

T = TypeVar("T", bound="CatalogEntryRead")


@_attrs_define
class CatalogEntryRead:
    """
    Attributes:
        name (str):
        selectable (bool): False when DataQ cannot target this name (not a plain SQL identifier).
        object_type (CatalogEntryReadObjectTypeType0 | None | Unset): At the table level, the relation's kind; null at
            the catalog and schema levels.
    """

    name: str
    selectable: bool
    object_type: CatalogEntryReadObjectTypeType0 | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        selectable = self.selectable

        object_type: None | str | Unset
        if isinstance(self.object_type, Unset):
            object_type = UNSET
        elif isinstance(self.object_type, CatalogEntryReadObjectTypeType0):
            object_type = self.object_type.value
        else:
            object_type = self.object_type

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
                "selectable": selectable,
            }
        )
        if object_type is not UNSET:
            field_dict["object_type"] = object_type

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        name = d.pop("name")

        selectable = d.pop("selectable")

        def _parse_object_type(data: object) -> CatalogEntryReadObjectTypeType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                object_type_type_0 = CatalogEntryReadObjectTypeType0(data)

                return object_type_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(CatalogEntryReadObjectTypeType0 | None | Unset, data)

        object_type = _parse_object_type(d.pop("object_type", UNSET))

        catalog_entry_read = cls(
            name=name,
            selectable=selectable,
            object_type=object_type,
        )

        catalog_entry_read.additional_properties = d
        return catalog_entry_read

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
