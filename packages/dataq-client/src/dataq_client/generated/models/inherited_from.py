from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.inherited_from_baseline import InheritedFromBaseline
from ..models.inherited_from_sensitivity import InheritedFromSensitivity

T = TypeVar("T", bound="InheritedFrom")


@_attrs_define
class InheritedFrom:
    """The level each inherited group came from: this table's override (`asset`), the
    workspace setting, or the built-in default.

        Attributes:
            baseline (InheritedFromBaseline):
            sensitivity (InheritedFromSensitivity):
    """

    baseline: InheritedFromBaseline
    sensitivity: InheritedFromSensitivity
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        baseline = self.baseline.value

        sensitivity = self.sensitivity.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "baseline": baseline,
                "sensitivity": sensitivity,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        baseline = InheritedFromBaseline(d.pop("baseline"))

        sensitivity = InheritedFromSensitivity(d.pop("sensitivity"))

        inherited_from = cls(
            baseline=baseline,
            sensitivity=sensitivity,
        )

        inherited_from.additional_properties = d
        return inherited_from

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
