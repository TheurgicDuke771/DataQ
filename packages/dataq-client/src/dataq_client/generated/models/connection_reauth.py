from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

T = TypeVar("T", bound="ConnectionReauth")


@_attrs_define
class ConnectionReauth:
    """
    Attributes:
        secret (str): New credential; write-only, never returned
    """

    secret: str

    def to_dict(self) -> dict[str, Any]:
        secret = self.secret

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "secret": secret,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        secret = d.pop("secret")

        connection_reauth = cls(
            secret=secret,
        )

        return connection_reauth
