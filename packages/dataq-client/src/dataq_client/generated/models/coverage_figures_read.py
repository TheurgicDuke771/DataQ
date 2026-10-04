from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="CoverageFiguresRead")


@_attrs_define
class CoverageFiguresRead:
    """How much of the inventory is watched, and how often automatic checks cry wolf.

    Workspace-wide, like the dimension rollup: identical for every member, counts only.

    `coverage_pct` is `assets_watched / assets_total`: assets with at least one suite
    that completed a run in the last `coverage_window_days`. `assets_watched_authored`
    are watched by a suite a person authored; `assets_watched_auto_only` only by
    automatic coverage. NULL when the workspace has no assets.

    `false_positive_rate` is `false_positive / stated`, over automatic-suite incidents a
    person resolved in the last `false_positive_window_days`. `stated` counts those
    resolved WITH a stated resolution; `unstated` those without. NULL when nothing was
    stated — which is "not measured", not "no false positives". Auto-resolved incidents
    are in none of these numbers.

        Attributes:
            assets_total (int):
            assets_watched (int):
            assets_watched_authored (int):
            assets_watched_auto_only (int):
            coverage_pct (float | None):
            coverage_window_days (int):
            false_positive (int):
            false_positive_rate (float | None):
            false_positive_window_days (int):
            resolved (int):
            stated (int):
            unstated (int):
    """

    assets_total: int
    assets_watched: int
    assets_watched_authored: int
    assets_watched_auto_only: int
    coverage_pct: float | None
    coverage_window_days: int
    false_positive: int
    false_positive_rate: float | None
    false_positive_window_days: int
    resolved: int
    stated: int
    unstated: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        assets_total = self.assets_total

        assets_watched = self.assets_watched

        assets_watched_authored = self.assets_watched_authored

        assets_watched_auto_only = self.assets_watched_auto_only

        coverage_pct: float | None
        coverage_pct = self.coverage_pct

        coverage_window_days = self.coverage_window_days

        false_positive = self.false_positive

        false_positive_rate: float | None
        false_positive_rate = self.false_positive_rate

        false_positive_window_days = self.false_positive_window_days

        resolved = self.resolved

        stated = self.stated

        unstated = self.unstated

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "assets_total": assets_total,
                "assets_watched": assets_watched,
                "assets_watched_authored": assets_watched_authored,
                "assets_watched_auto_only": assets_watched_auto_only,
                "coverage_pct": coverage_pct,
                "coverage_window_days": coverage_window_days,
                "false_positive": false_positive,
                "false_positive_rate": false_positive_rate,
                "false_positive_window_days": false_positive_window_days,
                "resolved": resolved,
                "stated": stated,
                "unstated": unstated,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        assets_total = d.pop("assets_total")

        assets_watched = d.pop("assets_watched")

        assets_watched_authored = d.pop("assets_watched_authored")

        assets_watched_auto_only = d.pop("assets_watched_auto_only")

        def _parse_coverage_pct(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        coverage_pct = _parse_coverage_pct(d.pop("coverage_pct"))

        coverage_window_days = d.pop("coverage_window_days")

        false_positive = d.pop("false_positive")

        def _parse_false_positive_rate(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        false_positive_rate = _parse_false_positive_rate(d.pop("false_positive_rate"))

        false_positive_window_days = d.pop("false_positive_window_days")

        resolved = d.pop("resolved")

        stated = d.pop("stated")

        unstated = d.pop("unstated")

        coverage_figures_read = cls(
            assets_total=assets_total,
            assets_watched=assets_watched,
            assets_watched_authored=assets_watched_authored,
            assets_watched_auto_only=assets_watched_auto_only,
            coverage_pct=coverage_pct,
            coverage_window_days=coverage_window_days,
            false_positive=false_positive,
            false_positive_rate=false_positive_rate,
            false_positive_window_days=false_positive_window_days,
            resolved=resolved,
            stated=stated,
            unstated=unstated,
        )

        coverage_figures_read.additional_properties = d
        return coverage_figures_read

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
