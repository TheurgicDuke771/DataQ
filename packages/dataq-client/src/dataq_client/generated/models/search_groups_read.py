from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.search_hit_read import SearchHitRead


T = TypeVar("T", bound="SearchGroupsRead")


@_attrs_define
class SearchGroupsRead:
    """
    Attributes:
        checks (list[SearchHitRead] | Unset):
        connections (list[SearchHitRead] | Unset):
        runs (list[SearchHitRead] | Unset):
        suites (list[SearchHitRead] | Unset):
    """

    checks: list[SearchHitRead] | Unset = UNSET
    connections: list[SearchHitRead] | Unset = UNSET
    runs: list[SearchHitRead] | Unset = UNSET
    suites: list[SearchHitRead] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.checks, Unset):
            checks = []
            for checks_item_data in self.checks:
                checks_item = checks_item_data.to_dict()
                checks.append(checks_item)

        connections: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.connections, Unset):
            connections = []
            for connections_item_data in self.connections:
                connections_item = connections_item_data.to_dict()
                connections.append(connections_item)

        runs: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.runs, Unset):
            runs = []
            for runs_item_data in self.runs:
                runs_item = runs_item_data.to_dict()
                runs.append(runs_item)

        suites: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.suites, Unset):
            suites = []
            for suites_item_data in self.suites:
                suites_item = suites_item_data.to_dict()
                suites.append(suites_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if checks is not UNSET:
            field_dict["checks"] = checks
        if connections is not UNSET:
            field_dict["connections"] = connections
        if runs is not UNSET:
            field_dict["runs"] = runs
        if suites is not UNSET:
            field_dict["suites"] = suites

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.search_hit_read import SearchHitRead

        d = dict(src_dict)
        _checks = d.pop("checks", UNSET)
        checks: list[SearchHitRead] | Unset = UNSET
        if _checks is not UNSET:
            checks = []
            for checks_item_data in _checks:
                checks_item = SearchHitRead.from_dict(checks_item_data)

                checks.append(checks_item)

        _connections = d.pop("connections", UNSET)
        connections: list[SearchHitRead] | Unset = UNSET
        if _connections is not UNSET:
            connections = []
            for connections_item_data in _connections:
                connections_item = SearchHitRead.from_dict(connections_item_data)

                connections.append(connections_item)

        _runs = d.pop("runs", UNSET)
        runs: list[SearchHitRead] | Unset = UNSET
        if _runs is not UNSET:
            runs = []
            for runs_item_data in _runs:
                runs_item = SearchHitRead.from_dict(runs_item_data)

                runs.append(runs_item)

        _suites = d.pop("suites", UNSET)
        suites: list[SearchHitRead] | Unset = UNSET
        if _suites is not UNSET:
            suites = []
            for suites_item_data in _suites:
                suites_item = SearchHitRead.from_dict(suites_item_data)

                suites.append(suites_item)

        search_groups_read = cls(
            checks=checks,
            connections=connections,
            runs=runs,
            suites=suites,
        )

        search_groups_read.additional_properties = d
        return search_groups_read

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
