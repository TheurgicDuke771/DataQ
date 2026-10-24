from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.search_response_truncated_groups_item import SearchResponseTruncatedGroupsItem
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.search_groups_read import SearchGroupsRead


T = TypeVar("T", bound="SearchResponse")


@_attrs_define
class SearchResponse:
    """Matches grouped by entity, each group capped at `limit`. `truncated_groups` names the
    groups with more matches than returned (from the real count — a full group is not by itself
    truncated). Every group is scoped like its own list endpoint: a suite the caller cannot see
    is absent, and so are its checks and runs.

        Attributes:
            groups (SearchGroupsRead):
            query (str):
            truncated_groups (list[SearchResponseTruncatedGroupsItem] | Unset):
    """

    groups: SearchGroupsRead
    query: str
    truncated_groups: list[SearchResponseTruncatedGroupsItem] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        groups = self.groups.to_dict()

        query = self.query

        truncated_groups: list[str] | Unset = UNSET
        if not isinstance(self.truncated_groups, Unset):
            truncated_groups = []
            for truncated_groups_item_data in self.truncated_groups:
                truncated_groups_item = truncated_groups_item_data.value
                truncated_groups.append(truncated_groups_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "groups": groups,
                "query": query,
            }
        )
        if truncated_groups is not UNSET:
            field_dict["truncated_groups"] = truncated_groups

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.search_groups_read import SearchGroupsRead

        d = dict(src_dict)
        groups = SearchGroupsRead.from_dict(d.pop("groups"))

        query = d.pop("query")

        _truncated_groups = d.pop("truncated_groups", UNSET)
        truncated_groups: list[SearchResponseTruncatedGroupsItem] | Unset = UNSET
        if _truncated_groups is not UNSET:
            truncated_groups = []
            for truncated_groups_item_data in _truncated_groups:
                truncated_groups_item = SearchResponseTruncatedGroupsItem(
                    truncated_groups_item_data
                )

                truncated_groups.append(truncated_groups_item)

        search_response = cls(
            groups=groups,
            query=query,
            truncated_groups=truncated_groups,
        )

        search_response.additional_properties = d
        return search_response

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
