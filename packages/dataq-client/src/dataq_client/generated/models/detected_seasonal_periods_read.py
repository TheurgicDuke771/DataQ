from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.detected_period_read import DetectedPeriodRead
    from ..models.detection_history_read import DetectionHistoryRead
    from ..models.detection_thresholds_read import DetectionThresholdsRead


T = TypeVar("T", bound="DetectedSeasonalPeriodsRead")


@_attrs_define
class DetectedSeasonalPeriodsRead:
    """Periods the check's own history shows running at or above `thresholds.ratio` times
    the level of the periods its scoring uses (`thresholds.level`). Computed on request and
    stored nowhere: a detected period changes no verdict until an admin promotes it to the
    calendar and it is attached.
    `reason` is set when there was nothing to look at (`not_a_per_period_metric`,
    `invalid_config`, `no_history`, `insufficient_history`); an empty `periods` with no
    reason means the history was read and nothing qualified.

        Attributes:
            history (DetectionHistoryRead):
            periods (list[DetectedPeriodRead]):
            reason (None | str):
            thresholds (DetectionThresholdsRead):
    """

    history: DetectionHistoryRead
    periods: list[DetectedPeriodRead]
    reason: None | str
    thresholds: DetectionThresholdsRead
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        history = self.history.to_dict()

        periods = []
        for periods_item_data in self.periods:
            periods_item = periods_item_data.to_dict()
            periods.append(periods_item)

        reason: None | str
        reason = self.reason

        thresholds = self.thresholds.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "history": history,
                "periods": periods,
                "reason": reason,
                "thresholds": thresholds,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.detected_period_read import DetectedPeriodRead
        from ..models.detection_history_read import DetectionHistoryRead
        from ..models.detection_thresholds_read import DetectionThresholdsRead

        d = dict(src_dict)
        history = DetectionHistoryRead.from_dict(d.pop("history"))

        periods = []
        _periods = d.pop("periods")
        for periods_item_data in _periods:
            periods_item = DetectedPeriodRead.from_dict(periods_item_data)

            periods.append(periods_item)

        def _parse_reason(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        reason = _parse_reason(d.pop("reason"))

        thresholds = DetectionThresholdsRead.from_dict(d.pop("thresholds"))

        detected_seasonal_periods_read = cls(
            history=history,
            periods=periods,
            reason=reason,
            thresholds=thresholds,
        )

        detected_seasonal_periods_read.additional_properties = d
        return detected_seasonal_periods_read

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
