from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="SuiteDeletionImpactRead")


@_attrs_define
class SuiteDeletionImpactRead:
    """Exact dependent counts a suite delete would destroy (#1320) — computed via
    `COUNT(*)`, never estimated or capped. States the blast radius of `DELETE
    /suites/{id}` before the fact, since checks/runs/results cascade with no undo.

        Attributes:
            checks (int):
            notification_channel_links (int):
            results (int):
            runs (int):
            schedules (int):
            trigger_bindings (int):
    """

    checks: int
    notification_channel_links: int
    results: int
    runs: int
    schedules: int
    trigger_bindings: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        checks = self.checks

        notification_channel_links = self.notification_channel_links

        results = self.results

        runs = self.runs

        schedules = self.schedules

        trigger_bindings = self.trigger_bindings

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "checks": checks,
                "notification_channel_links": notification_channel_links,
                "results": results,
                "runs": runs,
                "schedules": schedules,
                "trigger_bindings": trigger_bindings,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        checks = d.pop("checks")

        notification_channel_links = d.pop("notification_channel_links")

        results = d.pop("results")

        runs = d.pop("runs")

        schedules = d.pop("schedules")

        trigger_bindings = d.pop("trigger_bindings")

        suite_deletion_impact_read = cls(
            checks=checks,
            notification_channel_links=notification_channel_links,
            results=results,
            runs=runs,
            schedules=schedules,
            trigger_bindings=trigger_bindings,
        )

        suite_deletion_impact_read.additional_properties = d
        return suite_deletion_impact_read

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
