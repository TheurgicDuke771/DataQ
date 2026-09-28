from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="LineageNodeRead")


@_attrs_define
class LineageNodeRead:
    """A lineage neighbour — OpenLineage identity + whether it is monitored. No
    run data (blast-radius browse only; ADR 0034 §2). Fully named for every
    member (ADR 0037 — lineage topology is identity).

        Attributes:
            depth (int):
            env (None | str):
            id (UUID):
            is_monitored (bool):
            name (str):
            namespace (str):
            is_accessible (bool | Unset):  Default: True.
    """

    depth: int
    env: None | str
    id: UUID
    is_monitored: bool
    name: str
    namespace: str
    is_accessible: bool | Unset = True
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        depth = self.depth

        env: None | str
        env = self.env

        id = str(self.id)

        is_monitored = self.is_monitored

        name = self.name

        namespace = self.namespace

        is_accessible = self.is_accessible

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "depth": depth,
                "env": env,
                "id": id,
                "is_monitored": is_monitored,
                "name": name,
                "namespace": namespace,
            }
        )
        if is_accessible is not UNSET:
            field_dict["is_accessible"] = is_accessible

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        depth = d.pop("depth")

        def _parse_env(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        env = _parse_env(d.pop("env"))

        id = UUID(d.pop("id"))

        is_monitored = d.pop("is_monitored")

        name = d.pop("name")

        namespace = d.pop("namespace")

        is_accessible = d.pop("is_accessible", UNSET)

        lineage_node_read = cls(
            depth=depth,
            env=env,
            id=id,
            is_monitored=is_monitored,
            name=name,
            namespace=namespace,
            is_accessible=is_accessible,
        )

        lineage_node_read.additional_properties = d
        return lineage_node_read

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
