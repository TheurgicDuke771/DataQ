from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="SeasonalPointRead")


@_attrs_define
class SeasonalPointRead:
    """A per-period anomaly result's period and what it was scored against (ADR 0048):
    `mean` and `stddev` are the centre and spread the run was scored against; `expected` is
    the level times the seasonal index where the yearly comparison applied (otherwise
    `yearly_reason` says why not). With yearly the centre is the expected value shifted by how
    far earlier periods ran from what was expected of them, so the two can differ.

        Attributes:
            banded (bool):
            declared_name (None | str):
            expected (float | None):
            mean (float | None):
            period_start (datetime.date | None):
            stddev (float | None):
            verdict_changed (bool | None):
            yearly_applied (bool | None):
            yearly_reason (None | str):
    """

    banded: bool
    declared_name: None | str
    expected: float | None
    mean: float | None
    period_start: datetime.date | None
    stddev: float | None
    verdict_changed: bool | None
    yearly_applied: bool | None
    yearly_reason: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        banded = self.banded

        declared_name: None | str
        declared_name = self.declared_name

        expected: float | None
        expected = self.expected

        mean: float | None
        mean = self.mean

        period_start: None | str
        if isinstance(self.period_start, datetime.date):
            period_start = self.period_start.isoformat()
        else:
            period_start = self.period_start

        stddev: float | None
        stddev = self.stddev

        verdict_changed: bool | None
        verdict_changed = self.verdict_changed

        yearly_applied: bool | None
        yearly_applied = self.yearly_applied

        yearly_reason: None | str
        yearly_reason = self.yearly_reason

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "banded": banded,
                "declared_name": declared_name,
                "expected": expected,
                "mean": mean,
                "period_start": period_start,
                "stddev": stddev,
                "verdict_changed": verdict_changed,
                "yearly_applied": yearly_applied,
                "yearly_reason": yearly_reason,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        banded = d.pop("banded")

        def _parse_declared_name(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        declared_name = _parse_declared_name(d.pop("declared_name"))

        def _parse_expected(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        expected = _parse_expected(d.pop("expected"))

        def _parse_mean(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        mean = _parse_mean(d.pop("mean"))

        def _parse_period_start(data: object) -> datetime.date | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                period_start_type_0 = datetime.date.fromisoformat(data)

                return period_start_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.date | None, data)

        period_start = _parse_period_start(d.pop("period_start"))

        def _parse_stddev(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        stddev = _parse_stddev(d.pop("stddev"))

        def _parse_verdict_changed(data: object) -> bool | None:
            if data is None:
                return data
            return cast(bool | None, data)

        verdict_changed = _parse_verdict_changed(d.pop("verdict_changed"))

        def _parse_yearly_applied(data: object) -> bool | None:
            if data is None:
                return data
            return cast(bool | None, data)

        yearly_applied = _parse_yearly_applied(d.pop("yearly_applied"))

        def _parse_yearly_reason(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        yearly_reason = _parse_yearly_reason(d.pop("yearly_reason"))

        seasonal_point_read = cls(
            banded=banded,
            declared_name=declared_name,
            expected=expected,
            mean=mean,
            period_start=period_start,
            stddev=stddev,
            verdict_changed=verdict_changed,
            yearly_applied=yearly_applied,
            yearly_reason=yearly_reason,
        )

        seasonal_point_read.additional_properties = d
        return seasonal_point_read

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
