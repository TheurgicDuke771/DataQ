from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="FalsePositiveGroupRead")


@_attrs_define
class FalsePositiveGroupRead:
    """One slice of the false-positive rate. `rate` is `false_positive / stated` for the
    slice; a slice with nothing stated is not listed.

        Attributes:
            false_positive (int):
            key (str):
            label (str):
            rate (float):
            stated (int):
    """

    false_positive: int
    key: str
    label: str
    rate: float
    stated: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        false_positive = self.false_positive

        key = self.key

        label = self.label

        rate = self.rate

        stated = self.stated

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "false_positive": false_positive,
                "key": key,
                "label": label,
                "rate": rate,
                "stated": stated,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        false_positive = d.pop("false_positive")

        key = d.pop("key")

        label = d.pop("label")

        rate = d.pop("rate")

        stated = d.pop("stated")

        false_positive_group_read = cls(
            false_positive=false_positive,
            key=key,
            label=label,
            rate=rate,
            stated=stated,
        )

        false_positive_group_read.additional_properties = d
        return false_positive_group_read

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
