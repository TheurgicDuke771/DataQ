from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.comparison_delta_read_status_change import ComparisonDeltaReadStatusChange

T = TypeVar("T", bound="ComparisonDeltaRead")


@_attrs_define
class ComparisonDeltaRead:
    """`status_change`: `improved`/`regressed` rank pass < warn < fail < critical;
    `changed` is a transition through `skip`/`error`, which carry no rank;
    `added`/`removed` mean the check has a result on only one side. `metric_delta`
    is target minus base, `metric_delta_pct` relative to a non-zero base; both null
    when either side has no metric.

        Attributes:
            metric_changed (bool):
            metric_delta (float | None):
            metric_delta_pct (float | None):
            status_change (ComparisonDeltaReadStatusChange):
    """

    metric_changed: bool
    metric_delta: float | None
    metric_delta_pct: float | None
    status_change: ComparisonDeltaReadStatusChange
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        metric_changed = self.metric_changed

        metric_delta: float | None
        metric_delta = self.metric_delta

        metric_delta_pct: float | None
        metric_delta_pct = self.metric_delta_pct

        status_change = self.status_change.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "metric_changed": metric_changed,
                "metric_delta": metric_delta,
                "metric_delta_pct": metric_delta_pct,
                "status_change": status_change,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        metric_changed = d.pop("metric_changed")

        def _parse_metric_delta(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        metric_delta = _parse_metric_delta(d.pop("metric_delta"))

        def _parse_metric_delta_pct(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        metric_delta_pct = _parse_metric_delta_pct(d.pop("metric_delta_pct"))

        status_change = ComparisonDeltaReadStatusChange(d.pop("status_change"))

        comparison_delta_read = cls(
            metric_changed=metric_changed,
            metric_delta=metric_delta,
            metric_delta_pct=metric_delta_pct,
            status_change=status_change,
        )

        comparison_delta_read.additional_properties = d
        return comparison_delta_read

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
