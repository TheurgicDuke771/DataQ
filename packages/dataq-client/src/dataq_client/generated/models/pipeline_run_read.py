from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="PipelineRunRead")


@_attrs_define
class PipelineRunRead:
    """A monitored orchestrator pipeline/DAG run (`pipeline_runs` ≠ `runs`).

    Attributes:
        connection_id (UUID):
        created_at (datetime.datetime):
        env (str):
        failure_reason (None | str):
        finished_at (datetime.datetime | None):
        id (UUID):
        pipeline_or_dag_id (str):
        provider (str):
        provider_run_id (str):
        started_at (datetime.datetime | None):
        status (str):
        triggered_run_ids (list[UUID] | Unset):
    """

    connection_id: UUID
    created_at: datetime.datetime
    env: str
    failure_reason: None | str
    finished_at: datetime.datetime | None
    id: UUID
    pipeline_or_dag_id: str
    provider: str
    provider_run_id: str
    started_at: datetime.datetime | None
    status: str
    triggered_run_ids: list[UUID] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        connection_id = str(self.connection_id)

        created_at = self.created_at.isoformat()

        env = self.env

        failure_reason: None | str
        failure_reason = self.failure_reason

        finished_at: None | str
        if isinstance(self.finished_at, datetime.datetime):
            finished_at = self.finished_at.isoformat()
        else:
            finished_at = self.finished_at

        id = str(self.id)

        pipeline_or_dag_id = self.pipeline_or_dag_id

        provider = self.provider

        provider_run_id = self.provider_run_id

        started_at: None | str
        if isinstance(self.started_at, datetime.datetime):
            started_at = self.started_at.isoformat()
        else:
            started_at = self.started_at

        status = self.status

        triggered_run_ids: list[str] | Unset = UNSET
        if not isinstance(self.triggered_run_ids, Unset):
            triggered_run_ids = []
            for triggered_run_ids_item_data in self.triggered_run_ids:
                triggered_run_ids_item = str(triggered_run_ids_item_data)
                triggered_run_ids.append(triggered_run_ids_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "connection_id": connection_id,
                "created_at": created_at,
                "env": env,
                "failure_reason": failure_reason,
                "finished_at": finished_at,
                "id": id,
                "pipeline_or_dag_id": pipeline_or_dag_id,
                "provider": provider,
                "provider_run_id": provider_run_id,
                "started_at": started_at,
                "status": status,
            }
        )
        if triggered_run_ids is not UNSET:
            field_dict["triggered_run_ids"] = triggered_run_ids

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        connection_id = UUID(d.pop("connection_id"))

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        env = d.pop("env")

        def _parse_failure_reason(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        failure_reason = _parse_failure_reason(d.pop("failure_reason"))

        def _parse_finished_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                finished_at_type_0 = datetime.datetime.fromisoformat(data)

                return finished_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        finished_at = _parse_finished_at(d.pop("finished_at"))

        id = UUID(d.pop("id"))

        pipeline_or_dag_id = d.pop("pipeline_or_dag_id")

        provider = d.pop("provider")

        provider_run_id = d.pop("provider_run_id")

        def _parse_started_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                started_at_type_0 = datetime.datetime.fromisoformat(data)

                return started_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        started_at = _parse_started_at(d.pop("started_at"))

        status = d.pop("status")

        _triggered_run_ids = d.pop("triggered_run_ids", UNSET)
        triggered_run_ids: list[UUID] | Unset = UNSET
        if _triggered_run_ids is not UNSET:
            triggered_run_ids = []
            for triggered_run_ids_item_data in _triggered_run_ids:
                triggered_run_ids_item = UUID(triggered_run_ids_item_data)

                triggered_run_ids.append(triggered_run_ids_item)

        pipeline_run_read = cls(
            connection_id=connection_id,
            created_at=created_at,
            env=env,
            failure_reason=failure_reason,
            finished_at=finished_at,
            id=id,
            pipeline_or_dag_id=pipeline_or_dag_id,
            provider=provider,
            provider_run_id=provider_run_id,
            started_at=started_at,
            status=status,
            triggered_run_ids=triggered_run_ids,
        )

        pipeline_run_read.additional_properties = d
        return pipeline_run_read

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
