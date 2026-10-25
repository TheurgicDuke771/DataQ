from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.unmapped_clause_read_clause import UnmappedClauseReadClause


T = TypeVar("T", bound="UnmappedClauseRead")


@_attrs_define
class UnmappedClauseRead:
    """A clause no DataQ check enforces, and why — shown as *not enforced*, never dropped.

    Attributes:
        clause (UnmappedClauseReadClause):
        field (str):
        object_ (None | str):
        property_ (None | str):
        reason (str):
        rule_id (None | str):
    """

    clause: UnmappedClauseReadClause
    field: str
    object_: None | str
    property_: None | str
    reason: str
    rule_id: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        clause = self.clause.to_dict()

        field = self.field

        object_: None | str
        object_ = self.object_

        property_: None | str
        property_ = self.property_

        reason = self.reason

        rule_id: None | str
        rule_id = self.rule_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "clause": clause,
                "field": field,
                "object": object_,
                "property": property_,
                "reason": reason,
                "rule_id": rule_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.unmapped_clause_read_clause import UnmappedClauseReadClause

        d = dict(src_dict)
        clause = UnmappedClauseReadClause.from_dict(d.pop("clause"))

        field = d.pop("field")

        def _parse_object_(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        object_ = _parse_object_(d.pop("object"))

        def _parse_property_(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        property_ = _parse_property_(d.pop("property"))

        reason = d.pop("reason")

        def _parse_rule_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        rule_id = _parse_rule_id(d.pop("rule_id"))

        unmapped_clause_read = cls(
            clause=clause,
            field=field,
            object_=object_,
            property_=property_,
            reason=reason,
            rule_id=rule_id,
        )

        unmapped_clause_read.additional_properties = d
        return unmapped_clause_read

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
