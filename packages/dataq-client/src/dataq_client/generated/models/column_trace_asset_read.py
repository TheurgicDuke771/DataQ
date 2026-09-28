from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="ColumnTraceAssetRead")


@_attrs_define
class ColumnTraceAssetRead:
    """The identity of an asset a column trace names.

    Attributes:
        env (None | str):
        id (UUID):
        is_monitored (bool):
        name (str):
        namespace (str):
    """

    env: None | str
    id: UUID
    is_monitored: bool
    name: str
    namespace: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        env: None | str
        env = self.env

        id = str(self.id)

        is_monitored = self.is_monitored

        name = self.name

        namespace = self.namespace

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "env": env,
                "id": id,
                "is_monitored": is_monitored,
                "name": name,
                "namespace": namespace,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

        def _parse_env(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        env = _parse_env(d.pop("env"))

        id = UUID(d.pop("id"))

        is_monitored = d.pop("is_monitored")

        name = d.pop("name")

        namespace = d.pop("namespace")

        column_trace_asset_read = cls(
            env=env,
            id=id,
            is_monitored=is_monitored,
            name=name,
            namespace=namespace,
        )

        column_trace_asset_read.additional_properties = d
        return column_trace_asset_read

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
