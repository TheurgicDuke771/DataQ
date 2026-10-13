from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.effective_sensitivity_source import EffectiveSensitivitySource

T = TypeVar("T", bound="EffectiveSensitivity")


@_attrs_define
class EffectiveSensitivity:
    """
    Attributes:
        critical (float):
        fail (float):
        source (EffectiveSensitivitySource):
        warn (float):
    """

    critical: float
    fail: float
    source: EffectiveSensitivitySource
    warn: float
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        critical = self.critical

        fail = self.fail

        source = self.source.value

        warn = self.warn

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "critical": critical,
                "fail": fail,
                "source": source,
                "warn": warn,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        critical = d.pop("critical")

        fail = d.pop("fail")

        source = EffectiveSensitivitySource(d.pop("source"))

        warn = d.pop("warn")

        effective_sensitivity = cls(
            critical=critical,
            fail=fail,
            source=source,
            warn=warn,
        )

        effective_sensitivity.additional_properties = d
        return effective_sensitivity

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
