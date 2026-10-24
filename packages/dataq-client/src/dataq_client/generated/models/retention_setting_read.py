from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.retention_setting_read_unit import RetentionSettingReadUnit

T = TypeVar("T", bound="RetentionSettingRead")


@_attrs_define
class RetentionSettingRead:
    """One retention clock, as the deployment configured it.

    Attributes:
        detail (str):
        unit (RetentionSettingReadUnit):
        value (int):
        variable (str):
    """

    detail: str
    unit: RetentionSettingReadUnit
    value: int
    variable: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        detail = self.detail

        unit = self.unit.value

        value = self.value

        variable = self.variable

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "detail": detail,
                "unit": unit,
                "value": value,
                "variable": variable,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        detail = d.pop("detail")

        unit = RetentionSettingReadUnit(d.pop("unit"))

        value = d.pop("value")

        variable = d.pop("variable")

        retention_setting_read = cls(
            detail=detail,
            unit=unit,
            value=value,
            variable=variable,
        )

        retention_setting_read.additional_properties = d
        return retention_setting_read

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
