from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.suggestion_read_redaction_reasons_additional_property import (
    SuggestionReadRedactionReasonsAdditionalProperty,
)

T = TypeVar("T", bound="SuggestionReadRedactionReasons")


@_attrs_define
class SuggestionReadRedactionReasons:
    """Why each entry of `redacted_columns` is withheld, keyed by column."""

    additional_properties: dict[str, SuggestionReadRedactionReasonsAdditionalProperty] = (
        _attrs_field(init=False, factory=dict)
    )

    def to_dict(self) -> dict[str, Any]:

        field_dict: dict[str, Any] = {}
        for prop_name, prop in self.additional_properties.items():
            field_dict[prop_name] = prop.value

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        suggestion_read_redaction_reasons = cls()

        additional_properties = {}
        for prop_name, prop_dict in d.items():
            additional_property = SuggestionReadRedactionReasonsAdditionalProperty(prop_dict)

            additional_properties[prop_name] = additional_property

        suggestion_read_redaction_reasons.additional_properties = additional_properties
        return suggestion_read_redaction_reasons

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> SuggestionReadRedactionReasonsAdditionalProperty:
        return self.additional_properties[key]

    def __setitem__(
        self, key: str, value: SuggestionReadRedactionReasonsAdditionalProperty
    ) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
