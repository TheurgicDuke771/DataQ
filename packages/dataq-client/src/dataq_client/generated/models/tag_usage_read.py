from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="TagUsageRead")


@_attrs_define
class TagUsageRead:
    """How many of each entity carry the tag — workspace-wide, including suites and checks the
    caller cannot open. Counts only: which entities carry it is read from the entities, where the
    suite grant applies.

        Attributes:
            assets (int):
            checks (int):
            connections (int):
            suites (int):
    """

    assets: int
    checks: int
    connections: int
    suites: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        assets = self.assets

        checks = self.checks

        connections = self.connections

        suites = self.suites

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "assets": assets,
                "checks": checks,
                "connections": connections,
                "suites": suites,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        assets = d.pop("assets")

        checks = d.pop("checks")

        connections = d.pop("connections")

        suites = d.pop("suites")

        tag_usage_read = cls(
            assets=assets,
            checks=checks,
            connections=connections,
            suites=suites,
        )

        tag_usage_read.additional_properties = d
        return tag_usage_read

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
