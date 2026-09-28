from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="ApiKeyCreate")


@_attrs_define
class ApiKeyCreate:
    """
    Attributes:
        name (str): Label, e.g. 'ci-smoke'
        expires_in_days (int | Unset): Days until the key expires (no non-expiring keys) Default: 90.
    """

    name: str
    expires_in_days: int | Unset = 90

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        expires_in_days = self.expires_in_days

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "name": name,
            }
        )
        if expires_in_days is not UNSET:
            field_dict["expires_in_days"] = expires_in_days

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        name = d.pop("name")

        expires_in_days = d.pop("expires_in_days", UNSET)

        api_key_create = cls(
            name=name,
            expires_in_days=expires_in_days,
        )

        return api_key_create
