from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.poll_dispatch_read_scope import PollDispatchReadScope

T = TypeVar("T", bound="PollDispatchRead")


@_attrs_define
class PollDispatchRead:
    """One enqueued poll. `scope="provider"` means the sweep covers EVERY connection
    of that provider, not only `connection_id` — which is what happens when the named
    connection has no resource identifier configured to narrow on.

        Attributes:
            connection_id (None | UUID):
            provider (str):
            scope (PollDispatchReadScope):
            task_id (str):
    """

    connection_id: None | UUID
    provider: str
    scope: PollDispatchReadScope
    task_id: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        connection_id: None | str
        if isinstance(self.connection_id, UUID):
            connection_id = str(self.connection_id)
        else:
            connection_id = self.connection_id

        provider = self.provider

        scope = self.scope.value

        task_id = self.task_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "connection_id": connection_id,
                "provider": provider,
                "scope": scope,
                "task_id": task_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

        def _parse_connection_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                connection_id_type_0 = UUID(data)

                return connection_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        connection_id = _parse_connection_id(d.pop("connection_id"))

        provider = d.pop("provider")

        scope = PollDispatchReadScope(d.pop("scope"))

        task_id = d.pop("task_id")

        poll_dispatch_read = cls(
            connection_id=connection_id,
            provider=provider,
            scope=scope,
            task_id=task_id,
        )

        poll_dispatch_read.additional_properties = d
        return poll_dispatch_read

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
