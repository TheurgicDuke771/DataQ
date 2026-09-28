from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.connection_version_read_config import ConnectionVersionReadConfig


T = TypeVar("T", bound="ConnectionVersionRead")


@_attrs_define
class ConnectionVersionRead:
    """One snapshot in a connection's history. `changed_by_name` (the author's
    display name or email, NULL for a system actor / removed user) comes from the
    model property, resolved server-side so the client needn't join users. No
    credential is present — only the editable, non-secret fields are versioned.

        Attributes:
            changed_by (None | UUID):
            changed_by_name (None | str):
            config (ConnectionVersionReadConfig):
            created_at (datetime.datetime):
            env (str):
            name (str):
            type_ (str):
            version_no (int):
    """

    changed_by: None | UUID
    changed_by_name: None | str
    config: ConnectionVersionReadConfig
    created_at: datetime.datetime
    env: str
    name: str
    type_: str
    version_no: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        changed_by: None | str
        if isinstance(self.changed_by, UUID):
            changed_by = str(self.changed_by)
        else:
            changed_by = self.changed_by

        changed_by_name: None | str
        changed_by_name = self.changed_by_name

        config = self.config.to_dict()

        created_at = self.created_at.isoformat()

        env = self.env

        name = self.name

        type_ = self.type_

        version_no = self.version_no

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "changed_by": changed_by,
                "changed_by_name": changed_by_name,
                "config": config,
                "created_at": created_at,
                "env": env,
                "name": name,
                "type": type_,
                "version_no": version_no,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.connection_version_read_config import (
            ConnectionVersionReadConfig,
        )

        d = dict(src_dict)

        def _parse_changed_by(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                changed_by_type_0 = UUID(data)

                return changed_by_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        changed_by = _parse_changed_by(d.pop("changed_by"))

        def _parse_changed_by_name(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        changed_by_name = _parse_changed_by_name(d.pop("changed_by_name"))

        config = ConnectionVersionReadConfig.from_dict(d.pop("config"))

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        env = d.pop("env")

        name = d.pop("name")

        type_ = d.pop("type")

        version_no = d.pop("version_no")

        connection_version_read = cls(
            changed_by=changed_by,
            changed_by_name=changed_by_name,
            config=config,
            created_at=created_at,
            env=env,
            name=name,
            type_=type_,
            version_no=version_no,
        )

        connection_version_read.additional_properties = d
        return connection_version_read

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
