from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="NearMissRead")


@_attrs_define
class NearMissRead:
    """A currently-active #1186 trigger-binding env mismatch (#1199).

    Attributes:
        binding_env (str):
        pipeline_or_dag_id (str):
        provider (str):
        run_env (str):
        updated_at (datetime.datetime):
    """

    binding_env: str
    pipeline_or_dag_id: str
    provider: str
    run_env: str
    updated_at: datetime.datetime
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        binding_env = self.binding_env

        pipeline_or_dag_id = self.pipeline_or_dag_id

        provider = self.provider

        run_env = self.run_env

        updated_at = self.updated_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "binding_env": binding_env,
                "pipeline_or_dag_id": pipeline_or_dag_id,
                "provider": provider,
                "run_env": run_env,
                "updated_at": updated_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        binding_env = d.pop("binding_env")

        pipeline_or_dag_id = d.pop("pipeline_or_dag_id")

        provider = d.pop("provider")

        run_env = d.pop("run_env")

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        near_miss_read = cls(
            binding_env=binding_env,
            pipeline_or_dag_id=pipeline_or_dag_id,
            provider=provider,
            run_env=run_env,
            updated_at=updated_at,
        )

        near_miss_read.additional_properties = d
        return near_miss_read

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
