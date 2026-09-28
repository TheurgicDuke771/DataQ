from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="KpisRead")


@_attrs_define
class KpisRead:
    """
    Attributes:
        active_connections (int):
        avg_duration_delta_pct (float | None):
        avg_duration_ms (float | None):
        health_score (float | None):
        health_score_delta (float | None):
        pass_rate (float | None):
        pass_rate_delta (float | None):
        total_runs (int):
        total_runs_delta_pct (float | None):
    """

    active_connections: int
    avg_duration_delta_pct: float | None
    avg_duration_ms: float | None
    health_score: float | None
    health_score_delta: float | None
    pass_rate: float | None
    pass_rate_delta: float | None
    total_runs: int
    total_runs_delta_pct: float | None
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        active_connections = self.active_connections

        avg_duration_delta_pct: float | None
        avg_duration_delta_pct = self.avg_duration_delta_pct

        avg_duration_ms: float | None
        avg_duration_ms = self.avg_duration_ms

        health_score: float | None
        health_score = self.health_score

        health_score_delta: float | None
        health_score_delta = self.health_score_delta

        pass_rate: float | None
        pass_rate = self.pass_rate

        pass_rate_delta: float | None
        pass_rate_delta = self.pass_rate_delta

        total_runs = self.total_runs

        total_runs_delta_pct: float | None
        total_runs_delta_pct = self.total_runs_delta_pct

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "active_connections": active_connections,
                "avg_duration_delta_pct": avg_duration_delta_pct,
                "avg_duration_ms": avg_duration_ms,
                "health_score": health_score,
                "health_score_delta": health_score_delta,
                "pass_rate": pass_rate,
                "pass_rate_delta": pass_rate_delta,
                "total_runs": total_runs,
                "total_runs_delta_pct": total_runs_delta_pct,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        active_connections = d.pop("active_connections")

        def _parse_avg_duration_delta_pct(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        avg_duration_delta_pct = _parse_avg_duration_delta_pct(d.pop("avg_duration_delta_pct"))

        def _parse_avg_duration_ms(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        avg_duration_ms = _parse_avg_duration_ms(d.pop("avg_duration_ms"))

        def _parse_health_score(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        health_score = _parse_health_score(d.pop("health_score"))

        def _parse_health_score_delta(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        health_score_delta = _parse_health_score_delta(d.pop("health_score_delta"))

        def _parse_pass_rate(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        pass_rate = _parse_pass_rate(d.pop("pass_rate"))

        def _parse_pass_rate_delta(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        pass_rate_delta = _parse_pass_rate_delta(d.pop("pass_rate_delta"))

        total_runs = d.pop("total_runs")

        def _parse_total_runs_delta_pct(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        total_runs_delta_pct = _parse_total_runs_delta_pct(d.pop("total_runs_delta_pct"))

        kpis_read = cls(
            active_connections=active_connections,
            avg_duration_delta_pct=avg_duration_delta_pct,
            avg_duration_ms=avg_duration_ms,
            health_score=health_score,
            health_score_delta=health_score_delta,
            pass_rate=pass_rate,
            pass_rate_delta=pass_rate_delta,
            total_runs=total_runs,
            total_runs_delta_pct=total_runs_delta_pct,
        )

        kpis_read.additional_properties = d
        return kpis_read

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
