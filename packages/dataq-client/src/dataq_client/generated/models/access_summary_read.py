from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.access_level_counts import AccessLevelCounts
    from ..models.owned_suite_summary import OwnedSuiteSummary


T = TypeVar("T", bound="AccessSummaryRead")


@_attrs_define
class AccessSummaryRead:
    """The caller's suite access, counted server-side: what the Profile page renders.

    Attributes:
        counts_by_level (AccessLevelCounts):
        owned (list[OwnedSuiteSummary]):
        total_visible (int):
    """

    counts_by_level: AccessLevelCounts
    owned: list[OwnedSuiteSummary]
    total_visible: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        counts_by_level = self.counts_by_level.to_dict()

        owned = []
        for owned_item_data in self.owned:
            owned_item = owned_item_data.to_dict()
            owned.append(owned_item)

        total_visible = self.total_visible

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "counts_by_level": counts_by_level,
                "owned": owned,
                "total_visible": total_visible,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.access_level_counts import AccessLevelCounts
        from ..models.owned_suite_summary import OwnedSuiteSummary

        d = dict(src_dict)
        counts_by_level = AccessLevelCounts.from_dict(d.pop("counts_by_level"))

        owned = []
        _owned = d.pop("owned")
        for owned_item_data in _owned:
            owned_item = OwnedSuiteSummary.from_dict(owned_item_data)

            owned.append(owned_item)

        total_visible = d.pop("total_visible")

        access_summary_read = cls(
            counts_by_level=counts_by_level,
            owned=owned,
            total_visible=total_visible,
        )

        access_summary_read.additional_properties = d
        return access_summary_read

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
