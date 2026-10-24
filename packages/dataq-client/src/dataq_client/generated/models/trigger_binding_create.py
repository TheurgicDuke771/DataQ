from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="TriggerBindingCreate")


@_attrs_define
class TriggerBindingCreate:
    """
    Attributes:
        pipeline_or_dag_id (str):
        provider (str):
        suite_id (UUID):
        enabled (bool | Unset):  Default: True.
        env (None | str | Unset):
    """

    pipeline_or_dag_id: str
    provider: str
    suite_id: UUID
    enabled: bool | Unset = True
    env: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        pipeline_or_dag_id = self.pipeline_or_dag_id

        provider = self.provider

        suite_id = str(self.suite_id)

        enabled = self.enabled

        env: None | str | Unset
        if isinstance(self.env, Unset):
            env = UNSET
        else:
            env = self.env

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "pipeline_or_dag_id": pipeline_or_dag_id,
                "provider": provider,
                "suite_id": suite_id,
            }
        )
        if enabled is not UNSET:
            field_dict["enabled"] = enabled
        if env is not UNSET:
            field_dict["env"] = env

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        pipeline_or_dag_id = d.pop("pipeline_or_dag_id")

        provider = d.pop("provider")

        suite_id = UUID(d.pop("suite_id"))

        enabled = d.pop("enabled", UNSET)

        def _parse_env(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        env = _parse_env(d.pop("env", UNSET))

        trigger_binding_create = cls(
            pipeline_or_dag_id=pipeline_or_dag_id,
            provider=provider,
            suite_id=suite_id,
            enabled=enabled,
            env=env,
        )

        return trigger_binding_create
