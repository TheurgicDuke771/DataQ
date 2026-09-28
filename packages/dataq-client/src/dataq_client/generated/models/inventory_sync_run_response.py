from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="InventorySyncRunResponse")


@_attrs_define
class InventorySyncRunResponse:
    """The enqueued run. `status="queued"` means the worker has been asked, not that
    tables have been read — poll `GET /admin/inventory-sync` for the outcome.

        Attributes:
            task_id (str):
            status (str | Unset):  Default: 'queued'.
    """

    task_id: str
    status: str | Unset = "queued"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        task_id = self.task_id

        status = self.status

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "task_id": task_id,
            }
        )
        if status is not UNSET:
            field_dict["status"] = status

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        task_id = d.pop("task_id")

        status = d.pop("status", UNSET)

        inventory_sync_run_response = cls(
            task_id=task_id,
            status=status,
        )

        inventory_sync_run_response.additional_properties = d
        return inventory_sync_run_response

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
