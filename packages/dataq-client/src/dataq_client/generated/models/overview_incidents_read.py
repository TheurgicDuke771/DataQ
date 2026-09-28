from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="OverviewIncidentsRead")


@_attrs_define
class OverviewIncidentsRead:
    """`open` counts every UNRESOLVED incident, and `acknowledged` is the subset of
    those someone has picked up — so `acknowledged` is never larger than `open`, and
    an acknowledged incident is still open (acknowledging silences nothing).

        Attributes:
            acknowledged (int):
            open_ (int):
    """

    acknowledged: int
    open_: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        acknowledged = self.acknowledged

        open_ = self.open_

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "acknowledged": acknowledged,
                "open": open_,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        acknowledged = d.pop("acknowledged")

        open_ = d.pop("open")

        overview_incidents_read = cls(
            acknowledged=acknowledged,
            open_=open_,
        )

        overview_incidents_read.additional_properties = d
        return overview_incidents_read

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
