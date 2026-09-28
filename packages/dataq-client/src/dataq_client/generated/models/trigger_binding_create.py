from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="TriggerBindingCreate")


@_attrs_define
class TriggerBindingCreate:
    """
    Attributes:
        env (str):
        pipeline_or_dag_id (str):
        provider (str):
        suite_id (UUID):
        enabled (bool | Unset):  Default: True.
    """

    env: str
    pipeline_or_dag_id: str
    provider: str
    suite_id: UUID
    enabled: bool | Unset = True

    def to_dict(self) -> dict[str, Any]:
        env = self.env

        pipeline_or_dag_id = self.pipeline_or_dag_id

        provider = self.provider

        suite_id = str(self.suite_id)

        enabled = self.enabled

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "env": env,
                "pipeline_or_dag_id": pipeline_or_dag_id,
                "provider": provider,
                "suite_id": suite_id,
            }
        )
        if enabled is not UNSET:
            field_dict["enabled"] = enabled

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        env = d.pop("env")

        pipeline_or_dag_id = d.pop("pipeline_or_dag_id")

        provider = d.pop("provider")

        suite_id = UUID(d.pop("suite_id"))

        enabled = d.pop("enabled", UNSET)

        trigger_binding_create = cls(
            env=env,
            pipeline_or_dag_id=pipeline_or_dag_id,
            provider=provider,
            suite_id=suite_id,
            enabled=enabled,
        )

        return trigger_binding_create
