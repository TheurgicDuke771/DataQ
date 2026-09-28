from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="DimensionScoreRead")


@_attrs_define
class DimensionScoreRead:
    """One scorecard row (#889, ADR 0038).

    Attributes:
        checks_evaluated (int):
        checks_passing (int):
        checks_total (int):
        dimension (str):
        score (float | None):
    """

    checks_evaluated: int
    checks_passing: int
    checks_total: int
    dimension: str
    score: float | None
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        checks_evaluated = self.checks_evaluated

        checks_passing = self.checks_passing

        checks_total = self.checks_total

        dimension = self.dimension

        score: float | None
        score = self.score

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "checks_evaluated": checks_evaluated,
                "checks_passing": checks_passing,
                "checks_total": checks_total,
                "dimension": dimension,
                "score": score,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        checks_evaluated = d.pop("checks_evaluated")

        checks_passing = d.pop("checks_passing")

        checks_total = d.pop("checks_total")

        dimension = d.pop("dimension")

        def _parse_score(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        score = _parse_score(d.pop("score"))

        dimension_score_read = cls(
            checks_evaluated=checks_evaluated,
            checks_passing=checks_passing,
            checks_total=checks_total,
            dimension=dimension,
            score=score,
        )

        dimension_score_read.additional_properties = d
        return dimension_score_read

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
