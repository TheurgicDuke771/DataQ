from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.trigger_binding_warning_read import TriggerBindingWarningRead


T = TypeVar("T", bound="TriggerBindingRead")


@_attrs_define
class TriggerBindingRead:
    """
    Attributes:
        enabled (bool):
        env (str):
        id (UUID):
        pipeline_or_dag_id (str):
        provider (str):
        suite_id (UUID):
        warnings (list[TriggerBindingWarningRead] | Unset):
    """

    enabled: bool
    env: str
    id: UUID
    pipeline_or_dag_id: str
    provider: str
    suite_id: UUID
    warnings: list[TriggerBindingWarningRead] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        enabled = self.enabled

        env = self.env

        id = str(self.id)

        pipeline_or_dag_id = self.pipeline_or_dag_id

        provider = self.provider

        suite_id = str(self.suite_id)

        warnings: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.warnings, Unset):
            warnings = []
            for warnings_item_data in self.warnings:
                warnings_item = warnings_item_data.to_dict()
                warnings.append(warnings_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "enabled": enabled,
                "env": env,
                "id": id,
                "pipeline_or_dag_id": pipeline_or_dag_id,
                "provider": provider,
                "suite_id": suite_id,
            }
        )
        if warnings is not UNSET:
            field_dict["warnings"] = warnings

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.trigger_binding_warning_read import TriggerBindingWarningRead

        d = dict(src_dict)
        enabled = d.pop("enabled")

        env = d.pop("env")

        id = UUID(d.pop("id"))

        pipeline_or_dag_id = d.pop("pipeline_or_dag_id")

        provider = d.pop("provider")

        suite_id = UUID(d.pop("suite_id"))

        _warnings = d.pop("warnings", UNSET)
        warnings: list[TriggerBindingWarningRead] | Unset = UNSET
        if _warnings is not UNSET:
            warnings = []
            for warnings_item_data in _warnings:
                warnings_item = TriggerBindingWarningRead.from_dict(warnings_item_data)

                warnings.append(warnings_item)

        trigger_binding_read = cls(
            enabled=enabled,
            env=env,
            id=id,
            pipeline_or_dag_id=pipeline_or_dag_id,
            provider=provider,
            suite_id=suite_id,
            warnings=warnings,
        )

        trigger_binding_read.additional_properties = d
        return trigger_binding_read

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
