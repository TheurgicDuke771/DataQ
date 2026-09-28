from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

T = TypeVar("T", bound="SourceConnectionRefIn")


@_attrs_define
class SourceConnectionRefIn:
    """Request-side twin of `SourceConnectionRef` (see there) — the export
    response and the import payload are the same *shape*, but only the import
    side should 422 on an unknown key.

        Attributes:
            env (str):
            name (str):
    """

    env: str
    name: str

    def to_dict(self) -> dict[str, Any]:
        env = self.env

        name = self.name

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "env": env,
                "name": name,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        env = d.pop("env")

        name = d.pop("name")

        source_connection_ref_in = cls(
            env=env,
            name=name,
        )

        return source_connection_ref_in
