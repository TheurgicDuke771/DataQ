from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.inherited_source_read import InheritedSourceRead


T = TypeVar("T", bound="InheritedClassificationRead")


@_attrs_define
class InheritedClassificationRead:
    """A column masked as sensitive only because recorded lineage traces it to a sensitive
    upstream column. Tagging the column `public` in the warehouse overrides the inheritance.

        Attributes:
            column (str):
            sources (list[InheritedSourceRead]):
    """

    column: str
    sources: list[InheritedSourceRead]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        column = self.column

        sources = []
        for sources_item_data in self.sources:
            sources_item = sources_item_data.to_dict()
            sources.append(sources_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "column": column,
                "sources": sources,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.inherited_source_read import InheritedSourceRead

        d = dict(src_dict)
        column = d.pop("column")

        sources = []
        _sources = d.pop("sources")
        for sources_item_data in _sources:
            sources_item = InheritedSourceRead.from_dict(sources_item_data)

            sources.append(sources_item)

        inherited_classification_read = cls(
            column=column,
            sources=sources,
        )

        inherited_classification_read.additional_properties = d
        return inherited_classification_read

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
