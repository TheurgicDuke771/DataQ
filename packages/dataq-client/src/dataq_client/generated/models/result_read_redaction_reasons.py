from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.result_read_redaction_reasons_additional_property import (
    ResultReadRedactionReasonsAdditionalProperty,
)

T = TypeVar("T", bound="ResultReadRedactionReasons")


@_attrs_define
class ResultReadRedactionReasons:
    """Why each entry of `redacted_columns` was masked, keyed by column — the same key set. `own_tag`: the column's own
    warehouse classification; `inherited_tag`: `sensitive` inherited through recorded column lineage; `policy`: the
    suite's `pii_columns`; `unclassified`: fail-closed mode and nothing cleared the column; `detected`: the profiler
    found PII-shaped values; `classifier`: the read-time name/value heuristic; `default_mask`: nobody classified the
    column, so masking is precautionary.

    """

    additional_properties: dict[str, ResultReadRedactionReasonsAdditionalProperty] = _attrs_field(
        init=False, factory=dict
    )

    def to_dict(self) -> dict[str, Any]:

        field_dict: dict[str, Any] = {}
        for prop_name, prop in self.additional_properties.items():
            field_dict[prop_name] = prop.value

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        result_read_redaction_reasons = cls()

        additional_properties = {}
        for prop_name, prop_dict in d.items():
            additional_property = ResultReadRedactionReasonsAdditionalProperty(prop_dict)

            additional_properties[prop_name] = additional_property

        result_read_redaction_reasons.additional_properties = additional_properties
        return result_read_redaction_reasons

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> ResultReadRedactionReasonsAdditionalProperty:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: ResultReadRedactionReasonsAdditionalProperty) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
