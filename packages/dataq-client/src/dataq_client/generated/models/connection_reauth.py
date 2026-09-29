from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="ConnectionReauth")


@_attrs_define
class ConnectionReauth:
    """
    Attributes:
        secret (str): New credential; write-only, never returned
        skip_test (bool | Unset): Save without running the connectivity test first (#1927) — for a store the API cannot
            reach at authoring time. Recorded on the audit event. Default: False.
    """

    secret: str
    skip_test: bool | Unset = False

    def to_dict(self) -> dict[str, Any]:
        secret = self.secret

        skip_test = self.skip_test

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "secret": secret,
            }
        )
        if skip_test is not UNSET:
            field_dict["skip_test"] = skip_test

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        secret = d.pop("secret")

        skip_test = d.pop("skip_test", UNSET)

        connection_reauth = cls(
            secret=secret,
            skip_test=skip_test,
        )

        return connection_reauth
