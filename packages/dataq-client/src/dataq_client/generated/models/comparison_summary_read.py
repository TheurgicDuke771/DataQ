from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="ComparisonSummaryRead")


@_attrs_define
class ComparisonSummaryRead:
    """
    Attributes:
        added (int):
        changed (int):
        improved (int):
        metric_changed (int):
        regressed (int):
        removed (int):
        unchanged (int):
    """

    added: int
    changed: int
    improved: int
    metric_changed: int
    regressed: int
    removed: int
    unchanged: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        added = self.added

        changed = self.changed

        improved = self.improved

        metric_changed = self.metric_changed

        regressed = self.regressed

        removed = self.removed

        unchanged = self.unchanged

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "added": added,
                "changed": changed,
                "improved": improved,
                "metric_changed": metric_changed,
                "regressed": regressed,
                "removed": removed,
                "unchanged": unchanged,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        added = d.pop("added")

        changed = d.pop("changed")

        improved = d.pop("improved")

        metric_changed = d.pop("metric_changed")

        regressed = d.pop("regressed")

        removed = d.pop("removed")

        unchanged = d.pop("unchanged")

        comparison_summary_read = cls(
            added=added,
            changed=changed,
            improved=improved,
            metric_changed=metric_changed,
            regressed=regressed,
            removed=removed,
            unchanged=unchanged,
        )

        comparison_summary_read.additional_properties = d
        return comparison_summary_read

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
