from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from typing_extensions import Self

T = TypeVar("T", bound="BulkEnabledRequest")


@_attrs_define
class BulkEnabledRequest:
    """
    Attributes:
        check_ids (list[UUID]):
        enabled (bool): false switches the checks off; true switches them on
    """

    check_ids: list[UUID]
    enabled: bool

    def to_dict(self) -> dict[str, Any]:
        check_ids = []
        for check_ids_item_data in self.check_ids:
            check_ids_item = str(check_ids_item_data)
            check_ids.append(check_ids_item)

        enabled = self.enabled

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "check_ids": check_ids,
                "enabled": enabled,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        check_ids = []
        _check_ids = d.pop("check_ids")
        for check_ids_item_data in _check_ids:
            check_ids_item = UUID(check_ids_item_data)

            check_ids.append(check_ids_item)

        enabled = d.pop("enabled")

        bulk_enabled_request = cls(
            check_ids=check_ids,
            enabled=enabled,
        )

        return bulk_enabled_request
