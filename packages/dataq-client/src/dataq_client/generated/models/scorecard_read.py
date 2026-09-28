from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.dimension_score_read import DimensionScoreRead


T = TypeVar("T", bound="ScorecardRead")


@_attrs_define
class ScorecardRead:
    """Per-dimension coverage + score, **workspace-true** (ADR 0037) — identical
    for every viewer who can see the asset.

        Attributes:
            covered (list[DimensionScoreRead]):
            unclassified_checks (int):
            uncovered (list[str]):
    """

    covered: list[DimensionScoreRead]
    unclassified_checks: int
    uncovered: list[str]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        covered = []
        for covered_item_data in self.covered:
            covered_item = covered_item_data.to_dict()
            covered.append(covered_item)

        unclassified_checks = self.unclassified_checks

        uncovered = self.uncovered

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "covered": covered,
                "unclassified_checks": unclassified_checks,
                "uncovered": uncovered,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.dimension_score_read import DimensionScoreRead

        d = dict(src_dict)
        covered = []
        _covered = d.pop("covered")
        for covered_item_data in _covered:
            covered_item = DimensionScoreRead.from_dict(covered_item_data)

            covered.append(covered_item)

        unclassified_checks = d.pop("unclassified_checks")

        uncovered = cast(list[str], d.pop("uncovered"))

        scorecard_read = cls(
            covered=covered,
            unclassified_checks=unclassified_checks,
            uncovered=uncovered,
        )

        scorecard_read.additional_properties = d
        return scorecard_read

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
