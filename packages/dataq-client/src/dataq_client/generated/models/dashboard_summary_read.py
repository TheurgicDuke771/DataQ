from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.kpis_read import KpisRead
    from ..models.suite_performance_read import SuitePerformanceRead
    from ..models.trend_point_read import TrendPointRead


T = TypeVar("T", bound="DashboardSummaryRead")


@_attrs_define
class DashboardSummaryRead:
    """
    Attributes:
        kpis (KpisRead):
        suite_performance (list[SuitePerformanceRead]):
        trend (list[TrendPointRead]):
        window_days (int):
    """

    kpis: KpisRead
    suite_performance: list[SuitePerformanceRead]
    trend: list[TrendPointRead]
    window_days: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        kpis = self.kpis.to_dict()

        suite_performance = []
        for suite_performance_item_data in self.suite_performance:
            suite_performance_item = suite_performance_item_data.to_dict()
            suite_performance.append(suite_performance_item)

        trend = []
        for trend_item_data in self.trend:
            trend_item = trend_item_data.to_dict()
            trend.append(trend_item)

        window_days = self.window_days

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "kpis": kpis,
                "suite_performance": suite_performance,
                "trend": trend,
                "window_days": window_days,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.kpis_read import KpisRead
        from ..models.suite_performance_read import SuitePerformanceRead
        from ..models.trend_point_read import TrendPointRead

        d = dict(src_dict)
        kpis = KpisRead.from_dict(d.pop("kpis"))

        suite_performance = []
        _suite_performance = d.pop("suite_performance")
        for suite_performance_item_data in _suite_performance:
            suite_performance_item = SuitePerformanceRead.from_dict(suite_performance_item_data)

            suite_performance.append(suite_performance_item)

        trend = []
        _trend = d.pop("trend")
        for trend_item_data in _trend:
            trend_item = TrendPointRead.from_dict(trend_item_data)

            trend.append(trend_item)

        window_days = d.pop("window_days")

        dashboard_summary_read = cls(
            kpis=kpis,
            suite_performance=suite_performance,
            trend=trend,
            window_days=window_days,
        )

        dashboard_summary_read.additional_properties = d
        return dashboard_summary_read

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
