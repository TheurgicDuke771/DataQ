from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="TriggerBindingWarningRead")


@_attrs_define
class TriggerBindingWarningRead:
    """Mirrors `trigger_binding_service.TriggerBindingWarning` (#1186).

    Attributes:
        code (str):
        message (str):
        other_envs (list[str]):
    """

    code: str
    message: str
    other_envs: list[str]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        code = self.code

        message = self.message

        other_envs = self.other_envs

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "code": code,
                "message": message,
                "other_envs": other_envs,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        code = d.pop("code")

        message = d.pop("message")

        other_envs = cast(list[str], d.pop("other_envs"))

        trigger_binding_warning_read = cls(
            code=code,
            message=message,
            other_envs=other_envs,
        )

        trigger_binding_warning_read.additional_properties = d
        return trigger_binding_warning_read

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
