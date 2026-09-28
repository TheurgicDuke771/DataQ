from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

T = TypeVar("T", bound="PrivacySettingsWrite")


@_attrs_define
class PrivacySettingsWrite:
    """
    Attributes:
        zero_sample_mode (bool):
    """

    zero_sample_mode: bool

    def to_dict(self) -> dict[str, Any]:
        zero_sample_mode = self.zero_sample_mode

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "zero_sample_mode": zero_sample_mode,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        zero_sample_mode = d.pop("zero_sample_mode")

        privacy_settings_write = cls(
            zero_sample_mode=zero_sample_mode,
        )

        return privacy_settings_write
