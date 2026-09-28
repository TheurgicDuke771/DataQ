from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="ScoringWeightsWrite")


@_attrs_define
class ScoringWeightsWrite:
    """
    Attributes:
        critical (float):
        fail (float):
        warn (float):
    """

    critical: float
    fail: float
    warn: float
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        critical = self.critical

        fail = self.fail

        warn = self.warn

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "critical": critical,
                "fail": fail,
                "warn": warn,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        critical = d.pop("critical")

        fail = d.pop("fail")

        warn = d.pop("warn")

        scoring_weights_write = cls(
            critical=critical,
            fail=fail,
            warn=warn,
        )

        scoring_weights_write.additional_properties = d
        return scoring_weights_write

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
